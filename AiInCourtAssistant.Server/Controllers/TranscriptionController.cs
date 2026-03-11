// Server/Controllers/TranscriptionController.cs
using AiInCourtAssistant.Server.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Logging;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;   // for GetService<T>()

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/transcribe")]
[Produces("application/json")]
public class TranscriptionController : ControllerBase
{
    private readonly IHttpClientFactory _http;
    private readonly ILogger<TranscriptionController> _log;

    public TranscriptionController(
        IHttpClientFactory http,
        ILogger<TranscriptionController> log)
    {
        _http = http;
        _log = log;
    }

    // Give ourselves a client that points to this server (so relative /api/proxy/... works)
    private HttpClient CreateSelfClient()
    {
        var client = _http.CreateClient();
        if (client.BaseAddress == null)
        {
            client.BaseAddress = new Uri($"{Request.Scheme}://{Request.Host}");
        }
        return client;
    }

    // Apply incoming auth (bearer + cookies) to an outbound request
    private static void CopyAuth(HttpRequestMessage req, string bearer, string cookies)
    {
        if (!string.IsNullOrWhiteSpace(bearer))
            req.Headers.Authorization = AuthenticationHeaderValue.Parse(bearer);
        if (!string.IsNullOrWhiteSpace(cookies))
            req.Headers.TryAddWithoutValidation("Cookie", cookies); // preserve pine_token
    }

    [HttpPost("media/{mediaItemId:int}")]
    public async Task<IActionResult> TranscribeMedia(
        int mediaItemId,
        [FromQuery] int caseId,
        [FromQuery] string language = "en",
        CancellationToken ct = default)
    {
        // Try to get the OpenAI service only if this endpoint is used
        var ai = HttpContext.RequestServices.GetService<IOpenAITranscriptionService>();
        if (ai is null)
        {
            return Problem("Transcription service is not configured.");
        }

        var upstream = _http.CreateClient("AuthorizedClient"); // sandbox client
        var self = CreateSelfClient();

        // Bearer: header OR pine_token cookie fallback
        var bearer = Request.Headers.Authorization.ToString();
        if (string.IsNullOrWhiteSpace(bearer) &&
            Request.Cookies.TryGetValue("pine_token", out var jwt) &&
            !string.IsNullOrWhiteSpace(jwt))
        {
            bearer = $"Bearer {jwt}";
        }
        var cookies = Request.Headers.Cookie.ToString();

        _log.LogInformation("=== Transcribe start mediaId={MediaId} caseId={CaseId} ===", mediaItemId, caseId);

        // 1) Read media meta from sandbox
        var metaResp = await upstream.GetAsync($"/api/MediaItem/{mediaItemId}", ct);
        var metaRaw = await metaResp.Content.ReadAsStringAsync(ct);
        if (!metaResp.IsSuccessStatusCode)
        {
            _log.LogWarning("Meta read failed: {Status} {Body}", metaResp.StatusCode, metaRaw);
            return Problem($"Could not read media item {mediaItemId}: {(int)metaResp.StatusCode} {metaRaw}");
        }

        using var metaDoc = JsonDocument.Parse(metaRaw);
        var metaRoot = metaDoc.RootElement;

        var originalName =
            metaRoot.TryGetProperty("originalFileName", out var on) && on.ValueKind == JsonValueKind.String
                ? (on.GetString() ?? $"media_{mediaItemId}")
                : $"media_{mediaItemId}";

        var downloadUrl =
            metaRoot.TryGetProperty("downloadUrl", out var du) && du.ValueKind == JsonValueKind.String
                ? du.GetString()
                : null;

        // Prepare the values the transcriber expects
        var sendName = originalName;
        var sendCtype = GuessContentType(originalName); // default; may override below if proxy exposes a real type

        _log.LogInformation("Original file name: {Name}. Has downloadUrl: {HasUrl}", originalName, !string.IsNullOrWhiteSpace(downloadUrl));

        // 2) Acquire audio stream (and try to capture a better content-type)
        Stream audioStream;
        if (!string.IsNullOrWhiteSpace(downloadUrl))
        {
            var plain = new HttpClient();
            // We can't get headers from GetStreamAsync; keep the guessed content type from name
            audioStream = await plain.GetStreamAsync(downloadUrl!, ct);
        }
        else
        {
            var req = new HttpRequestMessage(HttpMethod.Get, $"/api/proxy/mediaItem/{mediaItemId}/download");
            CopyAuth(req, bearer, cookies);

            var resp = await self.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);

            if ((int)resp.StatusCode is 301 or 302 or 307 or 308 || resp.Headers.Location is not null)
            {
                var loc = resp.Headers.Location!;
                var absolute = loc.IsAbsoluteUri ? loc.ToString() : $"{Request.Scheme}://{Request.Host}{loc}";
                var plain = new HttpClient();
                audioStream = await plain.GetStreamAsync(absolute, ct);
                // keep guessed content type
            }
            else
            {
                var ctypeHeader = resp.Content.Headers.ContentType?.ToString();
                if (!resp.IsSuccessStatusCode)
                {
                    var raw = await resp.Content.ReadAsStringAsync(ct);
                    _log.LogWarning("Proxy download failed: {Status} {Body}", resp.StatusCode, raw);
                    return Problem($"Proxy download failed: {(int)resp.StatusCode} {raw}");
                }
                if (!string.IsNullOrWhiteSpace(ctypeHeader))
                    sendCtype = ctypeHeader;

                if (sendCtype.StartsWith("text/html", StringComparison.OrdinalIgnoreCase))
                {
                    var head = await PeekAsciiAsync(resp.Content, 256, ct);
                    _log.LogWarning("Downloaded content looks like HTML, not audio. Head: {Head}", head);
                    return Problem($"Downloaded content does not look like audio. content-type={sendCtype}, head={head}");
                }
                audioStream = await resp.Content.ReadAsStreamAsync(ct);
            }
        }

        // 3) Transcribe
        var transcriptText = await ai.TranscribeAsync(audioStream, sendName, sendCtype, language, ct);

        // 4) Ensure "Transcripts" folder
        var folderId = await EnsureTranscriptsFolderAsync(self, bearer, cookies, caseId, ct);
        if (folderId <= 0)
        {
            _log.LogWarning("Could not resolve/create Transcripts folder for case {CaseId}", caseId);
            return Problem("Could not locate or create the Transcripts folder.");
        }

        // 5) Create + upload + finalize transcript .txt (keep original media name for media/{id} flow)
        var bytes = Encoding.UTF8.GetBytes(transcriptText ?? "");
        var baseNameFromOriginal = Path.GetFileNameWithoutExtension(sendName);
        var txtName = baseNameFromOriginal + ".txt";

        var createPayload = new
        {
            caseID = caseId,
            mediaFolderID = folderId,
            name = baseNameFromOriginal,
            mimeType = "text/plain",
            storageType = "doc",
            originalFileName = txtName,
            extension = ".txt",
            fileSizeInBytes = bytes.Length,
            type = "Transcript"
        };

        // Create
        var createReq = new HttpRequestMessage(HttpMethod.Post, "/api/proxy/mediaItem")
        { Content = JsonContent.Create(createPayload) };
        CopyAuth(createReq, bearer, cookies);
        var createResp = await self.SendAsync(createReq, ct);
        var createRaw = await createResp.Content.ReadAsStringAsync(ct);
        createResp.EnsureSuccessStatusCode();

        using var createdDoc = JsonDocument.Parse(createRaw);
        var root = createdDoc.RootElement;
        var newId = root.GetProperty("mediaItem").GetProperty("mediaItemID").GetInt32();
        var upUrl = root.GetProperty("uploadUrls")[0].GetString()!;

        // Upload
        using (var put = new HttpRequestMessage(HttpMethod.Put, upUrl) { Content = new ByteArrayContent(bytes) })
        {
            put.Content.Headers.ContentType = new MediaTypeHeaderValue("text/plain");
            var s3 = new HttpClient();
            var putResp = await s3.SendAsync(put, ct);
            putResp.EnsureSuccessStatusCode();
        }

        // Finalize
        var finReq = new HttpRequestMessage(HttpMethod.Post, $"/api/proxy/mediaItem/{newId}/complete")
        { Content = new StringContent("", Encoding.UTF8, "application/json") };
        CopyAuth(finReq, bearer, cookies);
        var finResp = await self.SendAsync(finReq, ct);
        finResp.EnsureSuccessStatusCode();

        _log.LogInformation("Transcript saved as mediaItemID={Id}", newId);
        return Ok(new { transcriptMediaItemId = newId, transcript = transcriptText });
    }

    [HttpPost("upload")]
    [RequestSizeLimit(200_000_000)]
    public async Task<IActionResult> Upload(
        [FromForm] IFormFile file,
        [FromQuery] int caseId,
        [FromQuery] string eventType,
        [FromQuery] DateTime eventDate,
        [FromQuery] string language = "en",
        CancellationToken ct = default)
    {
        if (file is null || file.Length == 0)
            return BadRequest("No file uploaded.");

        // Try to get the OpenAI service only if this endpoint is used
        var ai = HttpContext.RequestServices.GetService<IOpenAITranscriptionService>();
        if (ai is null)
        {
            return Problem("Transcription service is not configured.");
        }

        _log.LogInformation("=== Upload start caseId={CaseId} name={Name} type={Type} size={Size} ===",
            caseId, file.FileName, file.ContentType, file.Length);

        var self = CreateSelfClient();

        // Bearer: header OR pine_token cookie fallback
        var bearer = Request.Headers.Authorization.ToString();
        if (string.IsNullOrWhiteSpace(bearer) &&
            Request.Cookies.TryGetValue("pine_token", out var jwt) &&
            !string.IsNullOrWhiteSpace(jwt))
        {
            bearer = $"Bearer {jwt}";
        }
        var cookies = Request.Headers.Cookie.ToString();

        // Read file into memory (we also keep the bytes to upload original media)
        byte[] audioBytes;
        await using (var ms = new MemoryStream())
        {
            await file.CopyToAsync(ms, ct);
            audioBytes = ms.ToArray();
        }

        // Prepare names/types
        var sendName = file.FileName;
        var sendCtype = string.IsNullOrWhiteSpace(file.ContentType)
            ? GuessContentType(file.FileName)
            : file.ContentType;

        // Transcribe the uploaded file
        string transcriptText;
        await using (var forStt = new MemoryStream(audioBytes, writable: false))
        {
            transcriptText = await ai.TranscribeAsync(forStt, sendName, sendCtype, language, ct);
        }

        // Ensure "Transcripts" folder
        var folderId = await EnsureTranscriptsFolderAsync(self, bearer, cookies, caseId, ct);
        if (folderId <= 0) return Problem("Could not locate or create the Transcripts folder.");

        // ======= NAMING: <EventType>_<MM-dd-yy> =======
        var safeType = string.IsNullOrWhiteSpace(eventType) ? "Event" : eventType.Trim();
        var baseName = $"{safeType}_{eventDate:MM-dd-yy}";

        // ---------- (A) SAVE ORIGINAL MEDIA FILE ----------
        var origExt = Path.GetExtension(file.FileName);
        var origMime = string.IsNullOrWhiteSpace(file.ContentType) ? sendCtype : file.ContentType;
        var storageType =
            origMime.StartsWith("video/", StringComparison.OrdinalIgnoreCase) ? "video" :
            origMime.StartsWith("audio/", StringComparison.OrdinalIgnoreCase) ? "audio" :
            "other";

        var createOrigPayload = new
        {
            caseID = caseId,
            mediaFolderID = folderId,
            name = baseName, // new name (not the uploaded filename)
            mimeType = origMime,
            storageType = storageType,
            originalFileName = $"{baseName}{(string.IsNullOrWhiteSpace(origExt) ? "" : origExt)}",
            extension = string.IsNullOrWhiteSpace(origExt) ? null : origExt,
            fileSizeInBytes = audioBytes.Length,
            type = "Recording"
        };

        var createOrigReq = new HttpRequestMessage(HttpMethod.Post, "/api/proxy/mediaItem")
        { Content = JsonContent.Create(createOrigPayload) };
        CopyAuth(createOrigReq, bearer, cookies);
        var createOrigResp = await self.SendAsync(createOrigReq, ct);
        var createOrigRaw = await createOrigResp.Content.ReadAsStringAsync(ct);
        createOrigResp.EnsureSuccessStatusCode();

        using var origDoc = JsonDocument.Parse(createOrigRaw);
        var origRoot = origDoc.RootElement;
        var origId = origRoot.GetProperty("mediaItem").GetProperty("mediaItemID").GetInt32();
        var origUpUrl = origRoot.GetProperty("uploadUrls")[0].GetString()!;

        using (var putOrig = new HttpRequestMessage(HttpMethod.Put, origUpUrl)
        { Content = new ByteArrayContent(audioBytes) })
        {
            putOrig.Content.Headers.ContentType = new MediaTypeHeaderValue(origMime);
            var s3 = new HttpClient();
            var putResp = await s3.SendAsync(putOrig, ct);
            putResp.EnsureSuccessStatusCode();
        }

        var finOrigReq = new HttpRequestMessage(HttpMethod.Post, $"/api/proxy/mediaItem/{origId}/complete")
        { Content = new StringContent("{}", Encoding.UTF8, "application/json") };
        CopyAuth(finOrigReq, bearer, cookies);
        var finOrigResp = await self.SendAsync(finOrigReq, ct);
        finOrigResp.EnsureSuccessStatusCode();

        _log.LogInformation("Original media saved as mediaItemID={Id}", origId);

        // ---------- (B) SAVE TRANSCRIPT .TXT ----------
        var transcriptBytes = Encoding.UTF8.GetBytes(transcriptText ?? "");
        var txtName = baseName + ".txt";

        var createTxtPayload = new
        {
            caseID = caseId,
            mediaFolderID = folderId,
            name = baseName,
            mimeType = "text/plain",
            storageType = "doc",
            originalFileName = txtName,
            extension = ".txt",
            fileSizeInBytes = transcriptBytes.Length,
            type = "Transcript"
        };

        var createTxtReq = new HttpRequestMessage(HttpMethod.Post, "/api/proxy/mediaItem")
        { Content = JsonContent.Create(createTxtPayload) };
        CopyAuth(createTxtReq, bearer, cookies);

        var createTxtResp = await self.SendAsync(createTxtReq, ct);
        var createTxtRaw = await createTxtResp.Content.ReadAsStringAsync(ct);
        createTxtResp.EnsureSuccessStatusCode();

        using var createdDoc = JsonDocument.Parse(createTxtRaw);
        var newId = createdDoc.RootElement.GetProperty("mediaItem").GetProperty("mediaItemID").GetInt32();
        var upUrl = createdDoc.RootElement.GetProperty("uploadUrls")[0].GetString()!;

        using (var put = new HttpRequestMessage(HttpMethod.Put, upUrl) { Content = new ByteArrayContent(transcriptBytes) })
        {
            put.Content.Headers.ContentType = new MediaTypeHeaderValue("text/plain");
            var s3 = new HttpClient();
            var putResp = await s3.SendAsync(put, ct);
            putResp.EnsureSuccessStatusCode();
        }

        var finReq = new HttpRequestMessage(HttpMethod.Post, $"/api/proxy/mediaItem/{newId}/complete")
        { Content = new StringContent("{}", Encoding.UTF8, "application/json") };
        CopyAuth(finReq, bearer, cookies);

        var finResp = await self.SendAsync(finReq, ct);
        finResp.EnsureSuccessStatusCode();

        return Ok(new { originalMediaItemId = origId, transcriptMediaItemId = newId, transcript = transcriptText });
    }

    // ===== Save plain text (used by LIVE) =====
    public sealed class SaveTextRequest
    {
        public int EventId { get; set; }
        public int CaseId { get; set; }
        public string? FileName { get; set; }   // e.g. "2025-01-09 - Parsons, Jeffrey H"
        public string? Text { get; set; }       // transcript contents
    }

    public sealed class SaveTextResponse
    {
        public int MediaItemId { get; set; }
        public string Url { get; set; } = "";   // download link via proxy
    }

    [HttpPost("save-text")]
    public async Task<ActionResult<SaveTextResponse>> SaveText([FromBody] SaveTextRequest req, CancellationToken ct = default)
    {
        if (req.CaseId <= 0) return BadRequest("CaseId required.");
        if (string.IsNullOrWhiteSpace(req.FileName)) return BadRequest("FileName required.");

        var self = CreateSelfClient();

        // Bearer: header OR pine_token cookie fallback
        var bearer = Request.Headers.Authorization.ToString();
        if (string.IsNullOrWhiteSpace(bearer) &&
            Request.Cookies.TryGetValue("pine_token", out var jwt) &&
            !string.IsNullOrWhiteSpace(jwt))
        {
            bearer = $"Bearer {jwt}";
        }
        var cookies = Request.Headers.Cookie.ToString();

        // Ensure /Transcripts folder in Sandbox for this case
        var folderId = await EnsureTranscriptsFolderAsync(self, bearer, cookies, req.CaseId, ct);
        if (folderId <= 0) return Problem("Could not locate or create the Transcripts folder.");

        // File name pieces
        var baseName = Path.GetFileNameWithoutExtension(req.FileName!.Trim()); // keep caller's name
        var txtName = baseName + ".txt";
        var bytes = Encoding.UTF8.GetBytes(req.Text ?? string.Empty);

        // Create mediaItem (type Transcript, storage 'doc')
        var createPayload = new
        {
            caseID = req.CaseId,
            mediaFolderID = folderId,
            name = baseName,
            mimeType = "text/plain",
            storageType = "doc",
            originalFileName = txtName,
            extension = ".txt",
            fileSizeInBytes = bytes.Length,
            type = "Transcript"
        };

        var createReq = new HttpRequestMessage(HttpMethod.Post, "/api/proxy/mediaItem")
        {
            Content = JsonContent.Create(createPayload)
        };
        CopyAuth(createReq, bearer, cookies);

        var createResp = await self.SendAsync(createReq, ct);
        var createRaw = await createResp.Content.ReadAsStringAsync(ct);
        createResp.EnsureSuccessStatusCode();

        using var createdDoc = JsonDocument.Parse(createRaw);
        var root = createdDoc.RootElement;
        var newId = root.GetProperty("mediaItem").GetProperty("mediaItemID").GetInt32();
        var upUrl = root.GetProperty("uploadUrls")[0].GetString()!;

        // Upload to S3
        using (var put = new HttpRequestMessage(HttpMethod.Put, upUrl) { Content = new ByteArrayContent(bytes) })
        {
            put.Content.Headers.ContentType = new MediaTypeHeaderValue("text/plain");
            var s3 = new HttpClient();
            var putResp = await s3.SendAsync(put, ct);
            putResp.EnsureSuccessStatusCode();
        }

        // Finalize
        var finReq = new HttpRequestMessage(HttpMethod.Post, $"/api/proxy/mediaItem/{newId}/complete")
        {
            Content = new StringContent("{}", Encoding.UTF8, "application/json")
        };
        CopyAuth(finReq, bearer, cookies);

        var finResp = await self.SendAsync(finReq, ct);
        finResp.EnsureSuccessStatusCode();

        // Return a proxy download URL consumers can open
        var url = $"/api/proxy/mediaItem/{newId}/download";
        return Ok(new SaveTextResponse { MediaItemId = newId, Url = url });
    }

    // ---------- helpers ----------

    private static async Task<string> PeekAsciiAsync(HttpContent content, int maxBytes, CancellationToken ct)
    {
        await using var s = await content.ReadAsStreamAsync(ct);
        var buf = new byte[Math.Min(maxBytes, 4096)];
        var n = await s.ReadAsync(buf.AsMemory(0, buf.Length), ct);
        var head = Encoding.ASCII.GetString(buf, 0, n);
        return head.Replace("\r", "\\r").Replace("\n", "\\n");
    }

    private static string GuessContentType(string nameOrPath)
    {
        var ext = Path.GetExtension(nameOrPath)?.ToLowerInvariant() ?? "";

        return ext switch
        {
            // Audio
            ".wav" => "audio/wav",
            ".mp3" => "audio/mpeg",
            ".m4a" => "audio/mp4",
            ".aac" => "audio/aac",
            ".ogg" or ".oga" => "audio/ogg",
            ".opus" => "audio/ogg",
            ".amr" => "audio/amr",
            ".flac" => "audio/flac",
            // Video (Deepgram can extract audio)
            ".mp4" => "video/mp4",
            ".mov" => "video/quicktime",
            ".mkv" => "video/x-matroska",
            ".webm" => "video/webm",
            ".avi" => "video/x-msvideo",
            ".wmv" => "video/x-ms-wmv",
            _ => "application/octet-stream"
        };
    }

    private static string GetQuery(string url, string key)
    {
        var q = new Uri(url).Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries);
        foreach (var kv in q)
        {
            var parts = kv.Split('=', 2);
            if (parts.Length == 2 && parts[0].Equals(key, StringComparison.OrdinalIgnoreCase))
                return Uri.UnescapeDataString(parts[1]);
        }
        return "";
    }

    private async Task<int> EnsureTranscriptsFolderAsync(HttpClient self, string bearer, string cookies, int caseId, CancellationToken ct)
    {
        // 1) Root
        var getRoot = new HttpRequestMessage(HttpMethod.Get, $"/api/proxy/mediaFolder/case/{caseId}");
        CopyAuth(getRoot, bearer, cookies);

        var rootResp = await self.SendAsync(getRoot, ct);
        var rootRaw = await rootResp.Content.ReadAsStringAsync(ct);
        if (!rootResp.IsSuccessStatusCode)
        {
            _log.LogWarning("Get root failed: {Status} {Body}", rootResp.StatusCode, rootRaw);
            return 0;
        }

        int rootId = GetInt(rootRaw, "mediaFolderID") ?? GetInt(rootRaw, "MediaFolderID") ?? 0;
        if (rootId <= 0)
        {
            _log.LogWarning("Root folder id not found in payload: {Body}", rootRaw);
            return 0;
        }

        // 2) Check for "Transcripts"
        var getCols = new HttpRequestMessage(HttpMethod.Get, $"/api/proxy/mediaFolder/{rootId}/collections");
        CopyAuth(getCols, bearer, cookies);
        var colResp = await self.SendAsync(getCols, ct);
        var colRaw = await colResp.Content.ReadAsStringAsync(ct);

        if (colResp.IsSuccessStatusCode)
        {
            try
            {
                using var doc = JsonDocument.Parse(colRaw);
                var root = doc.RootElement;
                if (root.ValueKind == JsonValueKind.Object &&
                    root.TryGetProperty("subMediaFolders", out var arr) &&
                    arr.ValueKind == JsonValueKind.Array)
                {
                    foreach (var f in arr.EnumerateArray())
                    {
                        var name = f.TryGetProperty("folderName", out var n) && n.ValueKind == JsonValueKind.String ? n.GetString() : null;
                        if (string.Equals(name, "Transcripts", StringComparison.OrdinalIgnoreCase))
                            return f.TryGetProperty("mediaFolderID", out var id) && id.TryGetInt32(out var v) ? v : 0;
                    }
                }
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Failed to parse collections response: {Body}", colRaw);
            }
        }
        else
        {
            _log.LogWarning("Get collections failed: {Status} {Body}", colResp.StatusCode, colRaw);
        }

        // 3) Create if missing
        var createBody = new { ParentMediaFolderID = rootId, FolderName = "Transcripts" };
        var makeReq = new HttpRequestMessage(HttpMethod.Post, "/api/proxy/mediaFolder")
        { Content = JsonContent.Create(createBody) };
        CopyAuth(makeReq, bearer, cookies);

        var makeResp = await self.SendAsync(makeReq, ct);
        var makeRaw = await makeResp.Content.ReadAsStringAsync(ct);

        if (makeResp.IsSuccessStatusCode)
        {
            var createdId = GetInt(makeRaw, "mediaFolderID") ?? GetInt(makeRaw, "MediaFolderID");
            if (createdId.HasValue) return createdId.Value;
            _log.LogInformation("Create returned no explicit ID. Rechecking collections…");
        }
        else
        {
            _log.LogWarning("Create Transcripts failed: {Status} {Body}", makeResp.StatusCode, makeRaw);
        }

        // 4) Recheck
        var re = new HttpRequestMessage(HttpMethod.Get, $"/api/proxy/mediaFolder/{rootId}/collections");
        CopyAuth(re, bearer, cookies);
        var r = await self.SendAsync(re, ct);
        var raw = await r.Content.ReadAsStringAsync(ct);
        if (!r.IsSuccessStatusCode)
        {
            _log.LogWarning("Recheck collections failed: {Status} {Body}", r.StatusCode, raw);
            return 0;
        }
        try
        {
            using var doc = JsonDocument.Parse(raw);
            var root = doc.RootElement;
            if (root.ValueKind == JsonValueKind.Object &&
                root.TryGetProperty("subMediaFolders", out var arr) &&
                arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var f in arr.EnumerateArray())
                {
                    var name = f.TryGetProperty("folderName", out var n) && n.ValueKind == JsonValueKind.String ? n.GetString() : null;
                    if (string.Equals(name, "Transcripts", StringComparison.OrdinalIgnoreCase))
                        return f.TryGetProperty("mediaFolderID", out var id) && id.TryGetInt32(out var v) ? v : 0;
                }
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to parse recheck response: {Body}", raw);
        }
        return 0;

        static int? GetInt(string rawJson, string prop)
        {
            try
            {
                using var doc = JsonDocument.Parse(rawJson);
                var obj = doc.RootElement;
                if (obj.ValueKind == JsonValueKind.Object && obj.TryGetProperty(prop, out var p))
                {
                    if (p.ValueKind == JsonValueKind.Number && p.TryGetInt32(out var v)) return v;
                    if (p.ValueKind == JsonValueKind.String && int.TryParse(p.GetString(), out var s)) return s;
                }
            }
            catch { }
            return null;
        }
    }
}

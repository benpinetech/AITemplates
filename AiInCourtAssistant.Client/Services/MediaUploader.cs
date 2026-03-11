using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Linq;

namespace AiInCourtAssistant.Client.Services
{
    public class MediaUploader
    {
        private readonly HttpClient _api;

        private static readonly JsonSerializerOptions J = new(JsonSerializerDefaults.Web)
        {
            PropertyNameCaseInsensitive = true,
            WriteIndented = false
        };

        public MediaUploader(HttpClient api) => _api = api;

        // ---------- PUBLIC API ----------

        /// <summary>Ensure a "Transcripts" folder exists under the case's root media folder.</summary>
        public async Task<(bool ok, int mediaFolderId, string? err)> EnsureTranscriptsFolderAsync(
            int caseId, string bearerToken)
        {
            try
            {
                var rootId = await GetRootMediaFolderIdAsync(caseId, bearerToken);
                if (rootId <= 0) return (false, 0, "RootMediaFolderID not found for case.");

                // Check for existing "Transcripts"
                var existing = await FindChildFolderIdAsync(rootId, "Transcripts", bearerToken);
                if (existing > 0) return (true, existing, null);

                // Create it (pass-through exactly what the proxy/tenant expects)
                var payload = new { ParentMediaFolderID = rootId, FolderName = "Transcripts" };

                using var req = new HttpRequestMessage(HttpMethod.Post, "api/proxy/mediaFolder");
                req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
                req.Content = new StringContent(JsonSerializer.Serialize(payload, J), Encoding.UTF8, "application/json");

                var resp = await _api.SendAsync(req);
                var body = await resp.Content.ReadAsStringAsync();
                Console.WriteLine($"[MediaUploader] Create folder resp {resp.StatusCode}: {body}");

                if (!resp.IsSuccessStatusCode)
                {
                    // Some automations create the folder but still return 400 — recheck once:
                    var retryId = await FindChildFolderIdAsync(rootId, "Transcripts", bearerToken);
                    if (retryId > 0) return (true, retryId, null);
                    return (false, 0, $"Create folder failed: {resp.StatusCode} {body}");
                }

                var created = JsonSerializer.Deserialize<CreateFolderResponse>(body, J);
                if (created?.MediaFolderID > 0) return (true, created.MediaFolderID, null);

                // Fallback: discover by name
                var doubleCheckId = await FindChildFolderIdAsync(rootId, "Transcripts", bearerToken);
                return doubleCheckId > 0
                    ? (true, doubleCheckId, null)
                    : (false, 0, "Folder create succeeded but ID not found.");
            }
            catch (Exception ex)
            {
                return (false, 0, $"Exception in EnsureTranscriptsFolderAsync: {ex.Message}");
            }
        }

        /// <summary>Upload a local audio stream into the Transcripts folder.</summary>
        public async Task<(bool ok, int mediaItemId, string? err)> UploadAudioAsync(
            int caseId,
            Stream audioStream,
            string fileName,
            string mimeType,
            long fileSizeBytes,
            string bearerToken)
        {
            if (audioStream == null || fileSizeBytes <= 0) return (false, 0, "Empty audio stream.");

            var ensure = await EnsureTranscriptsFolderAsync(caseId, bearerToken);
            if (!ensure.ok) return (false, 0, ensure.err);

            var create = await CreateMediaItemAsync(
                caseId, ensure.mediaFolderId, fileName, mimeType, "audio", fileSizeBytes, bearerToken);

            if (!create.ok) return (false, 0, create.err);

            var uploadUrl = create.uploadUrl;
            if (string.IsNullOrWhiteSpace(uploadUrl))
            {
                // Rare: some stacks only return upload URL on GET
                var up = await GetUploadInfoAsync(create.mediaItemId, bearerToken);
                if (!up.ok || string.IsNullOrWhiteSpace(up.uploadUrl))
                    return (false, create.mediaItemId, up.err ?? "No upload URL.");
                uploadUrl = up.uploadUrl!;
            }

            var putOk = await PutToPresignedUrlAsync(uploadUrl!, audioStream, mimeType);
            if (!putOk.ok) return (false, create.mediaItemId, putOk.err);

            var finalize = await CompleteAsync(create.mediaItemId, fileSizeBytes, mimeType, uploadUrl!, putOk.eTag, bearerToken);
            if (!finalize.ok) return (false, create.mediaItemId, finalize.err);

            _ = await TryReadMediaItemStateAsync(create.mediaItemId, bearerToken);
            return (true, create.mediaItemId, null);
        }

        /// <summary>Upload raw transcript text as a .txt document.</summary>
        public async Task<(bool ok, int mediaItemId, string? err)> UploadTranscriptTextAsync(
            int caseId, string transcriptText, string fileName, string bearerToken)
        {
            // Ensure .txt extension so the tenant shows it in the “Transcripts” view
            if (string.IsNullOrWhiteSpace(System.IO.Path.GetExtension(fileName)))
                fileName += ".txt";

            var bytes = Encoding.UTF8.GetBytes(transcriptText ?? string.Empty);
            await using var ms = new MemoryStream(bytes);
            return await UploadDocAsync(caseId, ms, fileName, "text/plain", bytes.LongLength, bearerToken);
        }

        // ---------- INTERNALS ----------

        private async Task<(bool ok, int mediaItemId, string? err)> UploadDocAsync(
            int caseId,
            Stream content,
            string fileName,
            string mimeType,
            long fileSizeBytes,
            string bearerToken)
        {
            var ensure = await EnsureTranscriptsFolderAsync(caseId, bearerToken);
            if (!ensure.ok) return (false, 0, ensure.err);

            var create = await CreateMediaItemAsync(
                caseId, ensure.mediaFolderId, fileName, mimeType, "doc", fileSizeBytes, bearerToken);

            if (!create.ok) return (false, 0, create.err);

            var uploadUrl = create.uploadUrl;
            if (string.IsNullOrWhiteSpace(uploadUrl))
            {
                var up = await GetUploadInfoAsync(create.mediaItemId, bearerToken);
                if (!up.ok || string.IsNullOrWhiteSpace(up.uploadUrl))
                    return (false, create.mediaItemId, up.err ?? "No upload URL.");
                uploadUrl = up.uploadUrl!;
            }

            var putOk = await PutToPresignedUrlAsync(uploadUrl!, content, mimeType);
            if (!putOk.ok) return (false, create.mediaItemId, putOk.err);

            var finalize = await CompleteAsync(create.mediaItemId, fileSizeBytes, mimeType, uploadUrl!, putOk.eTag, bearerToken);
            if (!finalize.ok) return (false, create.mediaItemId, finalize.err);

            _ = await TryReadMediaItemStateAsync(create.mediaItemId, bearerToken);
            return (true, create.mediaItemId, null);
        }

        private async Task<int> GetRootMediaFolderIdAsync(int caseId, string bearerToken)
        {
            // NOTE: your proxy exposes /api/proxy/mediaFolder/case/{caseId}
            using var req = new HttpRequestMessage(HttpMethod.Get, $"api/proxy/mediaFolder/case/{caseId}");
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);

            var resp = await _api.SendAsync(req);
            var body = await resp.Content.ReadAsStringAsync();
            var ct = resp.Content.Headers.ContentType?.MediaType ?? "";
            Console.WriteLine($"[MediaUploader] Case/Root resp {resp.StatusCode}: {ct}");

            if (!resp.IsSuccessStatusCode) return 0;
            if (!ct.Contains("json", StringComparison.OrdinalIgnoreCase)) return 0;
            if (string.IsNullOrWhiteSpace(body) || body.TrimStart().StartsWith("<")) return 0; // defensive: HTML page

            try
            {
                var dto = JsonSerializer.Deserialize<RootFolderDto>(body, J);
                return dto?.MediaFolderID ?? 0;
            }
            catch { return 0; }
        }

        private async Task<int> FindChildFolderIdAsync(int parentFolderId, string name, string bearerToken)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, $"api/proxy/mediaFolder/{parentFolderId}/collections");
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);

            var resp = await _api.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return 0;

            var body = await resp.Content.ReadAsStringAsync();
            var col = JsonSerializer.Deserialize<CollectionsResponse>(body, J);
            var match = col?.SubMediaFolders?.FirstOrDefault(f =>
                string.Equals(f.FolderName, name, StringComparison.OrdinalIgnoreCase));
            return match?.MediaFolderID ?? 0;
        }

        private async Task<(bool ok, int mediaItemId, string? uploadUrl, string? err)> CreateMediaItemAsync(
     int caseId, int mediaFolderId, string fileName, string mimeType, string storageType,
     long fileSizeBytes, string bearerToken)
        {
            var payload = new
            {
                MediaFolderID = mediaFolderId,
                CaseID = caseId,
                Name = System.IO.Path.GetFileNameWithoutExtension(fileName),
                MimeType = mimeType,
                StorageType = storageType,                  // "audio" or "doc"
                Extension = System.IO.Path.GetExtension(fileName), // <-- important for some tenants
                OriginalFileName = fileName,
                FileSizeInBytes = fileSizeBytes,
                // Optional, but some tenants like it:
                Type = storageType.Equals("audio", StringComparison.OrdinalIgnoreCase) ? "Audio" : "Transcript"
            };

            using var req = new HttpRequestMessage(HttpMethod.Post, "api/proxy/mediaItem");
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
            req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
            req.Content = JsonContent.Create(payload);

            var resp = await _api.SendAsync(req);
            var body = await resp.Content.ReadAsStringAsync();
            Console.WriteLine($"[MediaUploader] Create mediaItem resp {resp.StatusCode}: {body}");

            if (!resp.IsSuccessStatusCode)
                return (false, 0, null, $"Create media item failed: {resp.StatusCode} {body}");

            try
            {
                // robust parse (supports wrapped/encoded payloads)
                JsonElement root;
                using (var doc = JsonDocument.Parse(body))
                {
                    root = doc.RootElement;
                    if (root.ValueKind == JsonValueKind.String)
                    {
                        var s = root.GetString();
                        if (!string.IsNullOrWhiteSpace(s) && s.TrimStart().StartsWith("{"))
                        {
                            using var doc2 = JsonDocument.Parse(s);
                            root = doc2.RootElement.Clone();
                        }
                    }
                    else root = root.Clone();
                }

                var mi = root;
                if (root.ValueKind == JsonValueKind.Object)
                {
                    if (root.TryGetProperty("mediaItem", out var m1) && m1.ValueKind == JsonValueKind.Object) mi = m1;
                    else if (root.TryGetProperty("MediaItem", out var m2) && m2.ValueKind == JsonValueKind.Object) mi = m2;
                }

                int id = GetInt(mi, "mediaItemID") ?? GetInt(mi, "id") ?? 0;

                string? uploadUrl =
                    GetString(root, "uploadUrl") ??
                    GetString(root, "UploadUrl") ??
                    FirstString(root, "uploadUrls") ??
                    FirstString(root, "UploadUrls") ??
                    FirstString(root, "Urls");

                return id > 0
                    ? (true, id, uploadUrl, null)
                    : (false, 0, null, "No MediaItemID in create response.");
            }
            catch (Exception ex)
            {
                return (false, 0, null, $"Create parse error: {ex.Message}");
            }
        }
        private async Task<(bool ok, string? uploadUrl, string? err)> GetUploadInfoAsync(int mediaItemId, string bearerToken)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, $"api/proxy/mediaItem/{mediaItemId}");
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);

            var resp = await _api.SendAsync(req);
            var body = await resp.Content.ReadAsStringAsync();
            Console.WriteLine($"[MediaUploader] Get mediaItem {mediaItemId} resp {resp.StatusCode}: {body}");

            if (!resp.IsSuccessStatusCode) return (false, null, $"Get media item failed: {resp.StatusCode}");

            try
            {
                using var doc = JsonDocument.Parse(body);
                var root = doc.RootElement;

                string? url =
                    GetString(root, "uploadUrl") ??
                    GetString(root, "UploadUrl") ??
                    FirstString(root, "uploadUrls") ??
                    FirstString(root, "UploadUrls") ??
                    FirstString(root, "Urls");

                if (!string.IsNullOrWhiteSpace(url)) return (true, url, null);
                return (false, null, "Upload URL not found on media item.");
            }
            catch
            {
                return (false, null, "Could not parse media item JSON.");
            }
        }

        private async Task<(bool ok, string? eTag, string? err)> PutToPresignedUrlAsync(string url, Stream content, string mimeType)
        {
            try
            {
                using var put = new HttpRequestMessage(HttpMethod.Put, url);
                put.Content = new StreamContent(content);
                put.Content.Headers.ContentType = new MediaTypeHeaderValue(mimeType);

                // Do NOT add Authorization header to S3
                var resp = await _api.SendAsync(put);

                // Capture ETag (quoted by S3)
                string? eTag = resp.Headers.ETag?.Tag;
                if (string.IsNullOrEmpty(eTag) && resp.Headers.TryGetValues("ETag", out var etags))
                    eTag = etags.FirstOrDefault();
                eTag = TrimETag(eTag);

                if (!resp.IsSuccessStatusCode)
                {
                    var body = await resp.Content.ReadAsStringAsync();
                    return (false, null, $"S3 PUT failed: {resp.StatusCode} {body}");
                }
                return (true, eTag, null);
            }
            catch (Exception ex)
            {
                return (false, null, ex.Message);
            }

            static string? TrimETag(string? tag)
                => string.IsNullOrWhiteSpace(tag) ? null : tag.Trim().Trim('\"');
        }

        private async Task<(bool ok, string? err)> CompleteAsync(
            int mediaItemId, long size, string mimeType, string uploadUrl, string? eTag, string bearerToken)
        {
            try
            {
                var uploadId = GetQueryParam(uploadUrl, "uploadId");
                var parts = (eTag is null)
                    ? Array.Empty<object>()
                    : new[] { new { partNumber = 1, eTag } };  // server quotes as needed

                var body = new
                {
                    FileSizeInBytes = size,
                    MimeType = mimeType,
                    UploadId = uploadId,
                    Parts = parts
                };

                using var req = new HttpRequestMessage(HttpMethod.Post, $"api/proxy/mediaItem/{mediaItemId}/complete");
                req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
                req.Content = JsonContent.Create(body);

                var resp = await _api.SendAsync(req);
                if (!resp.IsSuccessStatusCode)
                {
                    var raw = await resp.Content.ReadAsStringAsync();
                    return (false, $"Finalize failed: {resp.StatusCode} {raw}");
                }
                return (true, null);
            }
            catch (Exception ex)
            {
                return (false, ex.Message);
            }
        }

        private async Task<(int fileState, long bytes)> TryReadMediaItemStateAsync(int mediaItemId, string bearerToken)
        {
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, $"api/proxy/mediaItem/{mediaItemId}");
                req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
                var resp = await _api.SendAsync(req);
                var body = await resp.Content.ReadAsStringAsync();
                var root = JsonSerializer.Deserialize<JsonElement>(body, J);

                int fs = 0; long sz = 0;

                if (root.ValueKind == JsonValueKind.Object &&
                    root.TryGetProperty("mediaItem", out var mi) &&
                    mi.ValueKind == JsonValueKind.Object)
                {
                    fs = GetInt(mi, "fileState") ?? 0;
                    sz = GetLong(mi, "fileSizeInBytes") ?? 0;
                }
                else
                {
                    fs = GetInt(root, "fileState") ?? 0;
                    sz = GetLong(root, "fileSizeInBytes") ?? 0;
                }

                return (fs, sz);
            }
            catch { return (0, 0); }

            static long? GetLong(JsonElement obj, string prop)
            {
                if (obj.ValueKind != JsonValueKind.Object) return null;
                if (!obj.TryGetProperty(prop, out var p)) return null;
                return p.ValueKind switch
                {
                    JsonValueKind.Number => p.TryGetInt64(out var v) ? v : (long?)null,
                    JsonValueKind.String => long.TryParse(p.GetString(), out var v) ? v : (long?)null,
                    _ => null
                };
            }
        }

        // ---------- Helpers ----------

        private static string? FirstString(JsonElement obj, string prop)
        {
            if (obj.ValueKind != JsonValueKind.Object) return null;
            if (!obj.TryGetProperty(prop, out var p) || p.ValueKind != JsonValueKind.Array) return null;
            foreach (var e in p.EnumerateArray())
                if (e.ValueKind == JsonValueKind.String) return e.GetString();
            return null;
        }

        private static string? GetQueryParam(string url, string key)
        {
            if (string.IsNullOrWhiteSpace(url)) return null;
            var q = url.IndexOf('?', StringComparison.Ordinal);
            if (q < 0) return null;

            var query = url[(q + 1)..];
            foreach (var kv in query.Split('&', StringSplitOptions.RemoveEmptyEntries))
            {
                var parts = kv.Split('=', 2);
                if (parts.Length == 2 &&
                    string.Equals(Uri.UnescapeDataString(parts[0]), key, StringComparison.OrdinalIgnoreCase))
                {
                    return Uri.UnescapeDataString(parts[1]);
                }
            }
            return null;
        }

        private static int? GetInt(JsonElement obj, string prop)
        {
            if (obj.ValueKind != JsonValueKind.Object) return null;
            if (!obj.TryGetProperty(prop, out var p)) return null;
            return p.ValueKind switch
            {
                JsonValueKind.Number => p.TryGetInt32(out var v32) ? v32 :
                                        (p.TryGetInt64(out var v64) && v64 >= int.MinValue && v64 <= int.MaxValue ? (int)v64 : (int?)null),
                JsonValueKind.String => int.TryParse(p.GetString(), out var iv) ? iv :
                                        (long.TryParse(p.GetString(), out var lv) && lv >= int.MinValue && lv <= int.MaxValue ? (int)lv : (int?)null),
                JsonValueKind.True => 1,
                JsonValueKind.False => 0,
                _ => (int?)null
            };
        }

        private static string? GetString(JsonElement obj, string prop)
        {
            if (obj.ValueKind != JsonValueKind.Object) return null;
            if (obj.TryGetProperty(prop, out var p) && p.ValueKind == JsonValueKind.String) return p.GetString();
            return null;
        }

        // ---------- DTOs ----------

        private sealed class RootFolderDto { public int MediaFolderID { get; set; } }

        private sealed class CollectionsResponse
        {
            public List<MediaFolderDto>? SubMediaFolders { get; set; }
        }
        private sealed class MediaFolderDto
        {
            public int MediaFolderID { get; set; }
            public string? FolderName { get; set; }
        }
        private sealed class CreateFolderResponse { public int MediaFolderID { get; set; } }
    }
}

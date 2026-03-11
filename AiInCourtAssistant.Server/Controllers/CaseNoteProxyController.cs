using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/proxy/case-note")]
[Authorize(Policy = "AllowPineTokenPassthrough")]
public sealed class CaseNoteProxyController : ControllerBase
{
    private readonly HttpClient _http;

    public CaseNoteProxyController(IHttpClientFactory httpFactory)
    {
        // Configure PineProxy in Startup/Program with BaseAddress + auth forwarding.
        _http = httpFactory.CreateClient("PineProxy");
    }

    // -------- models --------
    public sealed class CreateCaseNoteDto
    {
        public int caseID { get; set; }
        public string? text { get; set; }
        public string? type { get; set; }                 // e.g., "NFC"
        public string? updatedByDisplayName { get; set; }
        public int? updatedBySystemUserID { get; set; }
    }

    // ---------- POST (create) ----------
    // POST /api/proxy/case-note  ->  POST api/caseNote
    [HttpPost]
    public async Task<IActionResult> Post([FromBody] CreateCaseNoteDto dto, CancellationToken ct)
    {
        if (dto.caseID <= 0) return BadRequest(new { error = "caseID is required" });
        if (string.IsNullOrWhiteSpace(dto.text)) return BadRequest(new { error = "text is required" });
        dto.type ??= "NFC";

        using var resp = await _http.PostAsJsonAsync("api/caseNote", dto, ct);
        var media = resp.Content.Headers.ContentType?.MediaType ?? "";
        var body = await resp.Content.ReadAsStringAsync(ct);

        // Normalize to JSON if Pine sends HTML
        var isJson = media.StartsWith("application/json", StringComparison.OrdinalIgnoreCase) && !LooksLikeHtml(body);
        if (!isJson)
        {
            var normalized = JsonSerializer.Serialize(new { ok = resp.IsSuccessStatusCode, status = (int)resp.StatusCode });
            return Content(normalized, "application/json; charset=utf-8", (int)resp.StatusCode);
        }

        return Content(body, $"{resp.Content.Headers.ContentType}", (int)resp.StatusCode);
    }

    // ---------- GET (list passthrough) ----------
    // GET /api/proxy/case-note?caseId=96177&type=NFC&take=50&order=desc
    [HttpGet]
    public async Task<IActionResult> List(
        [FromQuery] int? caseId,
        [FromQuery] string? type = "NFC",
        [FromQuery] int take = 50,
        [FromQuery] string order = "desc",
        CancellationToken ct = default)
    {
        if (!caseId.HasValue && int.TryParse(Request.Query["caseID"], out var idAlt)) caseId = idAlt;
        if (!caseId.HasValue) return BadRequest(new { error = "caseId is required" });

        if (string.IsNullOrWhiteSpace(type)) type = "NFC";
        if (take <= 0) take = 50;

        var urls = new[]
        {
            $"api/caseNote?caseId={caseId.Value}&type={Uri.EscapeDataString(type)}&take={take}&order={order}",
            $"api/caseNote?caseID={caseId.Value}&type={Uri.EscapeDataString(type)}&take={take}&order={order}",
            $"api/caseNote/case/{caseId.Value}?type={Uri.EscapeDataString(type)}&take={take}&order={order}",
            $"api/case/{caseId.Value}/caseNotes?type={Uri.EscapeDataString(type)}&take={take}&order={order}"
        };

        foreach (var url in urls)
        {
            using var resp = await _http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct);
            var media = resp.Content.Headers.ContentType?.MediaType ?? "";
            var body = await resp.Content.ReadAsStringAsync(ct);

            var isJson = media.StartsWith("application/json", StringComparison.OrdinalIgnoreCase) && !LooksLikeHtml(body);
            if (resp.IsSuccessStatusCode && isJson)
            {
                return Content(body, $"{resp.Content.Headers.ContentType}", (int)resp.StatusCode);
            }

            if (resp.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.MethodNotAllowed)
                continue;
        }

        return Content("[]", "application/json; charset=utf-8", 200);
    }

    // ---------- GET (latest by DateTaken then CaseNoteID) ----------
    // GET /api/proxy/case-note/latest?caseId=96177&type=NFC
    [HttpGet("latest")]
    public async Task<IActionResult> GetLatestCaseNote(
        [FromQuery] int? caseId,
        [FromQuery] string? type = "NFC",
        CancellationToken ct = default)
    {
        if (!caseId.HasValue || caseId.Value <= 0)
            return BadRequest(new { error = "caseId is required" });

        // Try multiple “type” hints; some endpoints may want the label, others the code,
        // or may ignore the filter entirely, so we’ll also locally filter by type later.
        var typeHints = new[]
        {
            type ?? "NFC",
            "NFC",
            "Notes For Court"
        }.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();

        var urlShapes = new Func<string, string>[]
 {
    t => $"api/caseNote?caseId={caseId.Value}&type={WebUtility.UrlEncode(t)}&order=desc&take=50",
    t => $"api/caseNote?caseID={caseId.Value}&type={WebUtility.UrlEncode(t)}&order=desc&take=50",
    t => $"api/caseNote/case/{caseId.Value}?type={WebUtility.UrlEncode(t)}&order=desc&take=50",
    t => $"api/case/{caseId.Value}/caseNotes?type={WebUtility.UrlEncode(t)}&order=desc&take=50"
 };

        int bestId = 0;
        DateTime bestDt = DateTime.MinValue;
        string bestText = string.Empty;

        foreach (var hint in typeHints)
        {
            foreach (var makeUrl in urlShapes)
            {
                using var resp = await _http.GetAsync(makeUrl(hint), HttpCompletionOption.ResponseHeadersRead, ct);
                var body = await resp.Content.ReadAsStringAsync(ct);
                var media = resp.Content.Headers.ContentType?.MediaType ?? "";

                if (!resp.IsSuccessStatusCode) continue;

                // Be liberal in what we accept: try to parse JSON even if the content-type is odd.
                if (LooksLikeHtml(body)) continue;
                if (string.IsNullOrWhiteSpace(body)) continue;

                try
                {
                    using var doc = JsonDocument.Parse(body);
                    var root = doc.RootElement;

                    IEnumerable<JsonElement> items = root.ValueKind switch
                    {
                        JsonValueKind.Array => root.EnumerateArray(),
                        JsonValueKind.Object => new[] { root },
                        _ => Array.Empty<JsonElement>()
                    };

                    foreach (var item in items)
                    {
                        var text = ExtractNoteText(item);
                        if (string.IsNullOrWhiteSpace(text)) continue;

                        // Prefer notes whose Type matches “NFC” or “Notes For Court” (case-insensitive).
                        var itType = ExtractType(item);
                        var looksLikeNfc = string.IsNullOrWhiteSpace(itType)
                                           || itType.Equals("NFC", StringComparison.OrdinalIgnoreCase)
                                           || itType.Equals("Notes For Court", StringComparison.OrdinalIgnoreCase);

                        if (!looksLikeNfc) continue;

                        var dt = ExtractDateTaken(item);
                        var id = ExtractCaseNoteId(item);

                        var isBetter = dt > bestDt || (dt == bestDt && id > bestId);
                        if (isBetter)
                        {
                            bestDt = dt;
                            bestId = id;
                            bestText = text.Trim();
                        }
                    }
                }
                catch
                {
                    // Not JSON — try next variant
                }

                if (!string.IsNullOrWhiteSpace(bestText))
                    break; // good enough for this case
            }

            if (!string.IsNullOrWhiteSpace(bestText))
                break;
        }

        var payload = new { caseNoteID = bestId, text = bestText ?? string.Empty };
        return Content(JsonSerializer.Serialize(payload), "application/json; charset=utf-8", 200);
    }

    // -------- sandbox NFC shim (for the Blazor page) --------
    // POST /api/proxy/casenote/create  (absolute route; does not conflict with [Route("api/proxy/case-note")])
    public sealed record CreateNfcReq(int EventId, string Text);

    [HttpPost]
    [Route("/api/proxy/casenote/create")]
    public async Task<IActionResult> CreateNfc([FromBody] CreateNfcReq req)
    {
        if (req is null || req.EventId <= 0 || string.IsNullOrWhiteSpace(req.Text))
            return BadRequest("Invalid payload.");

        var folder = Path.Combine(AppContext.BaseDirectory, "App_Data", "SessionTranscripts");
        Directory.CreateDirectory(folder);

        var file = Path.Combine(folder, $"{req.EventId}-nfc-{DateTime.UtcNow:yyyyMMddTHHmmssZ}.txt");
        await System.IO.File.WriteAllTextAsync(file, req.Text, Encoding.UTF8);

        var latest = Path.Combine(folder, $"{req.EventId}-nfc-latest.txt");
        await System.IO.File.WriteAllTextAsync(latest, req.Text, Encoding.UTF8);

        return Ok(new { saved = true, file });
    }

    // ---------- helpers ----------
    private static string? ExtractNoteText(JsonElement note)
    {
        if (note.ValueKind != JsonValueKind.Object) return null;
        if (note.TryGetProperty("text", out var v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        if (note.TryGetProperty("Text", out v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        if (note.TryGetProperty("note", out v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        if (note.TryGetProperty("Note", out v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        if (note.TryGetProperty("noteText", out v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        if (note.TryGetProperty("NoteText", out v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        return null;
    }

    private static string? ExtractType(JsonElement note)
    {
        if (note.ValueKind != JsonValueKind.Object) return null;
        if (note.TryGetProperty("type", out var v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        if (note.TryGetProperty("Type", out v) && v.ValueKind == JsonValueKind.String) return v.GetString();
        return null;
    }

    private static int ExtractCaseNoteId(JsonElement note)
    {
        if (note.ValueKind != JsonValueKind.Object) return 0;
        if (note.TryGetProperty("caseNoteId", out var v) && v.TryGetInt32(out var id)) return id;
        if (note.TryGetProperty("CaseNoteID", out v) && v.TryGetInt32(out id)) return id;
        if (note.TryGetProperty("caseNoteID", out v) && v.TryGetInt32(out id)) return id;
        if (note.TryGetProperty("CaseNoteId", out v) && v.TryGetInt32(out id)) return id;
        if (note.TryGetProperty("id", out v) && v.TryGetInt32(out id)) return id;
        return 0;
    }

    private static DateTime ExtractDateTaken(JsonElement note)
    {
        if (note.ValueKind != JsonValueKind.Object) return DateTime.MinValue;

        static DateTime ParseOrMin(JsonElement e)
            => (e.ValueKind == JsonValueKind.String && DateTime.TryParse(e.GetString(), out var dt)) ? dt : DateTime.MinValue;

        if (note.TryGetProperty("dateTaken", out var v)) return ParseOrMin(v);
        if (note.TryGetProperty("DateTaken", out v)) return ParseOrMin(v);
        if (note.TryGetProperty("createdOn", out v)) return ParseOrMin(v);
        if (note.TryGetProperty("CreatedOn", out v)) return ParseOrMin(v);
        return DateTime.MinValue;
    }

    private static bool LooksLikeHtml(string s)
    {
        var t = s?.TrimStart();
        if (string.IsNullOrEmpty(t)) return false;
        return t.StartsWith("<!DOCTYPE", StringComparison.OrdinalIgnoreCase)
            || t.StartsWith("<html", StringComparison.OrdinalIgnoreCase)
            || t.StartsWith("<", StringComparison.Ordinal);
    }

    private ContentResult Content(string content, string contentType, int statusCode)
        => new() { StatusCode = statusCode, Content = content, ContentType = contentType };
}

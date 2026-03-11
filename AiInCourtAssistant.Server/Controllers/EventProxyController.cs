using AiInCourtAssistant.Server.Services;
using AiInCourtAssistant.Shared.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.WebUtilities;
using System.Net;
using System.Net.Http.Json;

namespace AiInCourtAssistant.Server.Controllers
{
    [ApiController]
    [Route("api/proxy/event")]
    [Authorize(Policy = "AllowPineTokenPassthrough")] // ← passthrough Pine JWT for proxy routes
    public sealed class EventProxyController : ControllerBase
    {
        private readonly HttpClient _http;              // ← Pine proxy client
        private readonly IEventService _eventService;

        public EventProxyController(IHttpClientFactory httpClientFactory, IEventService eventService)
        {
            _http = httpClientFactory.CreateClient("PineProxy"); // BaseAddress + bearer forwarding
            _eventService = eventService;
        }

        // ========= Advanced Search (client POST -> upstream POST/GET) =========
        [HttpPost("search")]
        public async Task<IActionResult> ProxyAdvancedEventSearch(
            [FromBody] AdvancedEventSearch request,
            CancellationToken ct)
        {
            try
            {
                var query = BuildQuery(request);

                // Try most-likely working path first, then fallbacks
                var attempts = new (HttpMethod Method, string Path, bool SendBody)[]
                {
                    (HttpMethod.Post, "api/event/advancedSearch", true), // original working route
                    (HttpMethod.Post, "api/event/search",        true),
                    (HttpMethod.Get,  "api/event/search",        false),
                    (HttpMethod.Get,  "api/events/search",       false),
                };

                HttpResponseMessage? last = null;
                string? lastBody = null;
                string? lastPath = null;

                foreach (var a in attempts)
                {
                    if (ct.IsCancellationRequested) break;

                    HttpResponseMessage resp;
                    if (a.Method == HttpMethod.Get)
                    {
                        var url = QueryHelpers.AddQueryString(a.Path, query);
                        Console.WriteLine($"[Proxy/Search] GET  {url}");
                        resp = await _http.GetAsync(url, ct);
                    }
                    else
                    {
                        Console.WriteLine($"[Proxy/Search] POST {a.Path}");
                        resp = await _http.PostAsJsonAsync(a.Path, request, ct);
                    }

                    var body = await resp.Content.ReadAsStringAsync(ct);
                    var mediaType = resp.Content.Headers.ContentType?.MediaType ?? "";
                    Console.WriteLine($"[Proxy/Search] -> {(int)resp.StatusCode} {mediaType}");

                    if (resp.IsSuccessStatusCode)
                    {
                        // Guard: sometimes 200 with HTML (SPA shell)
                        if (mediaType.StartsWith("application/json", StringComparison.OrdinalIgnoreCase) &&
                            !LooksLikeHtml(body))
                        {
                            return Content(body, "application/json");
                        }

                        last = resp; lastBody = body; lastPath = a.Path;
                        Console.WriteLine("[Proxy/Search] 200 but not JSON, trying next variant…");
                        continue;
                    }

                    if (resp.StatusCode == HttpStatusCode.NotFound ||
                        resp.StatusCode == HttpStatusCode.MethodNotAllowed)
                    {
                        last = resp; lastBody = body; lastPath = a.Path;
                        continue;
                    }

                    // Other non-2xx: bubble up
                    return StatusCode((int)resp.StatusCode, body);
                }

                var snippet = Snippet(lastBody);
                return StatusCode(502, $"Upstream search failed. Last path='{lastPath}'. Body starts with: {snippet}");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[ProxyAdvancedEventSearch ERROR] {ex}");
                return StatusCode(500, $"Proxy error: {ex.Message}");
            }
        }

        // ========= Single Update (POST) =========
        [HttpPost("update")]
        public async Task<IActionResult> UpdateEvent([FromBody] EventUpdateDto update, CancellationToken ct)
        {
            // Get full original (so we don't drop fields), modify, then PUT back
            var original = await _http.GetFromJsonAsync<FullEventDto>($"api/event/{update.EventID}", ct);
            if (original is null) return NotFound("Original event not found.");

            original.Note = update.Notes;
            original.Status = update.EventStatus;
            if (!string.IsNullOrWhiteSpace(update.Type)) original.Type = update.Type;
            original.UpdatedBySystemUserID = update.UpdatedBySystemUserID;
            original.UpdatedByDisplayName = update.UpdatedByDisplayName;
            original.StartDate = update.StartDate;

            var putResponse = await _http.PutAsJsonAsync($"api/event/{update.EventID}", original, ct);
            var content = await putResponse.Content.ReadAsStringAsync(ct);

            return putResponse.IsSuccessStatusCode
                ? Ok(content)
                : StatusCode((int)putResponse.StatusCode, content);
        }

        // ========= Case status update (LOCAL) =========
        [HttpPost("case/status")]
        public async Task<IActionResult> SetCaseStatus([FromBody] CaseStatusUpdateDto dto, CancellationToken ct)
        {
            if (dto is null || dto.CaseID <= 0 || string.IsNullOrWhiteSpace(dto.CaseStatus))
                return BadRequest("Invalid payload.");

            var ok = await _eventService.UpdateCaseStatusAsync(dto.CaseID, dto.CaseStatus, ct);
            if (!ok) return Problem("Failed to update case status", statusCode: 502);

            return Ok(new { ok = true });
        }

        [HttpPost("case/set-status")]
        [HttpPut("case/set-status")]
        public async Task<IActionResult> SetCaseStatus2([FromBody] CaseStatusUpdateDto dto, CancellationToken ct)
        {
            if (dto is null || dto.CaseID <= 0 || string.IsNullOrWhiteSpace(dto.CaseStatus))
                return BadRequest("Invalid payload.");

            var ok = await _eventService.UpdateCaseStatusAsync(dto.CaseID, dto.CaseStatus, ct);
            if (!ok) return Problem("Failed to update case status", statusCode: 502);

            return Ok(new { ok = true });
        }

        // ========= Get by Id (GET) =========
        [HttpGet("{id}")]
        public async Task<IActionResult> GetEventById(int id, CancellationToken ct)
        {
            var response = await _http.GetAsync($"api/event/{id}", ct);
            var content = await response.Content.ReadAsStringAsync(ct);

            return response.IsSuccessStatusCode
                ? Content(content, "application/json")
                : StatusCode((int)response.StatusCode, content);
        }

        // ========= Legacy Search passthrough (GET) =========
        [HttpGet("search")]
        [Produces("application/json")]
        public async Task<IActionResult> SearchEvents(
            [FromQuery] string location,
            [FromQuery] string startDate,
            CancellationToken ct)
        {
            var url = $"api/event/search?eventLocation={location}&eventStartDateFrom={startDate}";

            HttpResponseMessage resp;
            string body;
            string mediaType;
            try
            {
                resp = await _http.GetAsync(url, ct);
                body = await resp.Content.ReadAsStringAsync(ct);
                mediaType = resp.Content.Headers.ContentType?.MediaType ?? "";
            }
            catch (Exception ex)
            {
                return StatusCode(502, new { error = "ProxyRequestFailed", message = ex.Message });
            }

            if (!resp.IsSuccessStatusCode)
            {
                return StatusCode((int)resp.StatusCode, new
                {
                    error = "UpstreamError",
                    status = (int)resp.StatusCode,
                    path = url,
                    contentType = mediaType,
                    bodySnippet = body?.Length > 800 ? body[..800] : body
                });
            }

            if (!mediaType.Contains("json", StringComparison.OrdinalIgnoreCase) || LooksLikeHtml(body))
            {
                return StatusCode(502, new
                {
                    error = "UpstreamNonJson",
                    path = url,
                    contentType = mediaType,
                    bodySnippet = body?.Length > 800 ? body[..800] : body
                });
            }

            return Content(body, "application/json");
        }

        // ---------------- helpers ----------------
        private static IDictionary<string, string> BuildQuery(AdvancedEventSearch r)
        {
            var q = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            void Add(string key, string? v)
            {
                if (!string.IsNullOrWhiteSpace(v)) q[key] = v;
            }

            Add("eventLocation", AsCsv(r.EventLocation));
            Add("eventStartDateFrom", r.EventStartDateFrom?.ToString("yyyy-MM-dd"));
            Add("eventStartDateTo", r.EventStartDateTo?.ToString("yyyy-MM-dd"));
            Add("eventStatus", AsCsv(r.EventStatus));
            Add("eventType", AsCsv(r.EventType));
            return q;
        }

        private static string? AsCsv(object? value) =>
            value switch
            {
                null => null,
                string s => string.IsNullOrWhiteSpace(s) ? null : s,
                IEnumerable<string> list => list.Where(x => !string.IsNullOrWhiteSpace(x)) is var arr && arr.Any()
                    ? string.Join(",", arr)
                    : null,
                _ => value.ToString()
            };

        private static bool LooksLikeHtml(string s)
        {
            var t = s?.TrimStart();
            if (string.IsNullOrEmpty(t)) return false;
            return t.StartsWith("<!DOCTYPE", StringComparison.OrdinalIgnoreCase)
                || t.StartsWith("<html", StringComparison.OrdinalIgnoreCase)
                || t.StartsWith("<", StringComparison.Ordinal);
        }

        private static string Snippet(string? s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var max = Math.Min(240, s.Length);
            return s.Substring(0, max).Replace("\n", " ").Replace("\r", " ");
        }
    }
}

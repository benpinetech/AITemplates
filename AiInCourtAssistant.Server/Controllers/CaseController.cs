using Microsoft.AspNetCore.Mvc;
using System.Net.Http.Headers;

namespace AiInCourtAssistant.Server.Controllers
{
    [ApiController]
    [Route("api/case")] // <-- IMPORTANT: keep this base route for the UI
    public class CaseController : ControllerBase
    {
        private readonly IHttpClientFactory _httpClientFactory;
        public CaseController(IHttpClientFactory httpClientFactory) => _httpClientFactory = httpClientFactory;

        [HttpGet("_ping")]
        public IActionResult Ping() => Ok(new { ok = true, who = nameof(CaseController) });

        // Proxies to Pine: GET /api/case/search?searchTerm=...
        [HttpGet("search")]
        public async Task<IActionResult> Search([FromQuery] string searchTerm)
        {
            var token = ExtractBearer();
            if (token == null)
                return Unauthorized(new { error = "Missing Authorization: Bearer <token>" });

            var client = _httpClientFactory.CreateClient("AuthorizedClient");
            var url = $"api/case/search?searchTerm={Uri.EscapeDataString(searchTerm ?? string.Empty)}";

            using var req = new HttpRequestMessage(HttpMethod.Get, url);
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));

            using var resp = await client.SendAsync(req);
            var ct = resp.Content.Headers.ContentType?.MediaType ?? "application/json";
            var raw = await resp.Content.ReadAsStringAsync();

            if (!resp.IsSuccessStatusCode)
                return StatusCode((int)resp.StatusCode, new { error = "Upstream error", status = (int)resp.StatusCode, preview = raw });

            return Content(raw, ct);
        }

        private string? ExtractBearer()
        {
            if (!Request.Headers.TryGetValue("Authorization", out var header)) return null;
            var v = header.ToString().Trim();
            if (v.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
                v = v.Substring("Bearer ".Length).Trim();
            return string.IsNullOrEmpty(v) ? null : v;
        }
    }
}

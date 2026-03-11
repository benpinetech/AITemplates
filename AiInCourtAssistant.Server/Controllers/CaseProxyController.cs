using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers
{
    [ApiController]
    [Route("api/proxy/case")]
    [Authorize(Policy = "AllowPineTokenPassthrough")] // <- use passthrough auth for Pine token
    public class CaseProxyController : ControllerBase
    {
        private readonly HttpClient _http;

        public CaseProxyController(IHttpClientFactory httpClientFactory)
        {
            // Uses the named client configured in Program.cs with BaseAddress and ForwardBearerHandler
            _http = httpClientFactory.CreateClient("PineProxy");
        }

        // GET /api/proxy/case/search?term=...
        [HttpGet("search")]
        public async Task<IActionResult> Search([FromQuery] string? term)
        {
            // RELATIVE upstream path; BaseAddress on PineProxy makes it absolute
            var url = $"api/case/search?searchTerm={Uri.EscapeDataString(term ?? string.Empty)}";

            using var resp = await _http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead);
            var contentType = resp.Content.Headers.ContentType?.ToString() ?? "application/json; charset=utf-8";
            var body = await resp.Content.ReadAsStringAsync();

            // Pass upstream status/body verbatim so the client sees Pine's exact response
            return StatusCode((int)resp.StatusCode, body);
        }
    }
}

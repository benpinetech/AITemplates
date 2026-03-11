using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers
{
    [ApiController]
    [Route("api/proxy/auth")]
    [Authorize(Policy = "AllowPineTokenPassthrough")] // ← passthrough Pine JWT for this proxy
    public class AuthDebugProxyController : ControllerBase
    {
        private readonly HttpClient _http;

        public AuthDebugProxyController(IHttpClientFactory httpClientFactory)
        {
            // Named client configured in Program.cs with BaseAddress + ForwardBearerHandler
            _http = httpClientFactory.CreateClient("PineProxy");
        }

        // GET /api/proxy/auth/me  →  GET /api/auth/me (upstream)
        [HttpGet("me")]
        [Produces("application/json")]
        public async Task<IActionResult> Me(CancellationToken ct)
        {
            using var resp = await _http.GetAsync("api/auth/me", HttpCompletionOption.ResponseHeadersRead, ct);
            var body = await resp.Content.ReadAsStringAsync(ct);
            var contentType = resp.Content.Headers.ContentType?.ToString() ?? "application/json; charset=utf-8";

            return new ContentResult
            {
                StatusCode = (int)resp.StatusCode,
                Content = body,
                ContentType = contentType
            };
        }
    }
}

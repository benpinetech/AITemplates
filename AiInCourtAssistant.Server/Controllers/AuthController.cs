using System.Linq;
using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;          // ✅ added
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

using AiInCourtAssistant.Shared.Models;

namespace AiInCourtAssistant.Server.Controllers
{
    [ApiController]
    [Route("api/[controller]")] // => /api/auth
    public class AuthController : ControllerBase
    {
        private readonly IHttpClientFactory _http;

        public AuthController(IHttpClientFactory httpClientFactory)
        {
            _http = httpClientFactory;
        }

        // POST /api/auth/login
        // Body: { username, password }  (LoginDto)
        // Proxies to Pine, captures Token header, sets HttpOnly cookie "pine_token".
        [AllowAnonymous]                                  // ✅ allow unauthenticated
        [HttpPost("login")]
        public async Task<IActionResult> Login([FromBody] LoginDto credentials)
        {
            if (credentials is null ||
                string.IsNullOrWhiteSpace(credentials.Username) ||
                string.IsNullOrWhiteSpace(credentials.Password))
            {
                return BadRequest(new { error = "username and password are required" });
            }

            var client = _http.CreateClient("AuthorizedClient"); // BaseAddress = https://sandbox.pinetech.com/
            client.DefaultRequestHeaders.Accept.Clear();
            client.DefaultRequestHeaders.Accept.ParseAdd("application/json");

            using var req = new HttpRequestMessage(HttpMethod.Post, "api/auth/login")
            {
                Content = JsonContent.Create(credentials)
            };

            using var resp = await client.SendAsync(req);
            var body = await resp.Content.ReadAsStringAsync();

            if (!resp.IsSuccessStatusCode)
            {
                return StatusCode((int)resp.StatusCode, new
                {
                    ok = false,
                    error = "upstream login failed",
                    status = (int)resp.StatusCode,
                    preview = FirstN(body, 400)
                });
            }

            // Pine puts the JWT in the "Token" header, expiration in "Tokenexpiration"
            resp.Headers.TryGetValues("Token", out var tokenVals);
            resp.Headers.TryGetValues("Tokenexpiration", out var expVals);

            var token = tokenVals?.FirstOrDefault()?.Trim();
            var expirationRaw = expVals?.FirstOrDefault();
            if (string.IsNullOrWhiteSpace(token))
            {
                return StatusCode(502, new { ok = false, error = "Pine login succeeded but no Token header returned." });
            }

            var expires = DateTimeOffset.UtcNow.AddDays(1);
            if (DateTimeOffset.TryParse(expirationRaw, out var parsed)) expires = parsed;

            Response.Cookies.Append(
                "pine_token",
                token,
                new CookieOptions
                {
                    HttpOnly = true,
                    Secure = true,            // keep true; your dev server is HTTPS
                    SameSite = SameSiteMode.Lax,
                    Expires = expires
                });

            // Return minimal info; include token if you want the client to also keep it.
            return Ok(new
            {
                ok = true,
                token,
                expires = expirationRaw,
                body = TryJson(body)
            });
        }

        // Handy when you copy the token out of the sandbox UI and want to seed the cookie.
        public sealed class TokenBody { public string? Token { get; set; } }

        // POST /api/auth/setToken   { token: "<sandbox jwt>" }
        [AllowAnonymous]                                  // ✅ allow unauthenticated
        [HttpPost("setToken")]
        public IActionResult SetToken([FromBody] TokenBody body)
        {
            if (string.IsNullOrWhiteSpace(body?.Token))
                return BadRequest(new { error = "token is required" });

            Response.Cookies.Append(
                "pine_token",
                body.Token.Trim(),
                new CookieOptions
                {
                    HttpOnly = true,
                    Secure = true,
                    SameSite = SameSiteMode.Lax,
                    Expires = DateTimeOffset.UtcNow.AddDays(1)
                });

            return Ok(new { ok = true });
        }

        // POST /api/auth/clear
        [AllowAnonymous]                                  // ✅ allow unauthenticated
        [HttpPost("clear")]
        public IActionResult Clear()
        {
            Response.Cookies.Delete("pine_token");
            return Ok(new { ok = true });
        }

        // GET /api/auth/whoami
        [AllowAnonymous]                                  // ✅ allow unauthenticated
        [HttpGet("whoami")]
        public IActionResult WhoAmI()
        {
            var hasCookie = Request.Cookies.TryGetValue("pine_token", out var ck) && !string.IsNullOrWhiteSpace(ck);
            return Ok(new { ok = true, hasCookie });
        }

        // ---- helpers ----
        private static object TryJson(string raw)
        {
            try { return JsonSerializer.Deserialize<object>(raw) ?? new { }; }
            catch { return new { raw = FirstN(raw, 400) }; }
        }

        private static string FirstN(string s, int n)
            => string.IsNullOrEmpty(s) ? "" : (s.Length <= n ? s : s[..n]);
    }
}

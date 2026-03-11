using AiInCourtAssistant.Server.Services;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/admin/deepgram")]
public sealed class AdminDeepgramController : ControllerBase
{
    private const string KeyName = "Deepgram:ApiKey";
    private readonly ISecretStore _secrets;

    public AdminDeepgramController(ISecretStore secrets) => _secrets = secrets;

    // payloads
    public record KeySetRequest(string value);
    public record KeyMetaResponse(bool configured, string? last4, DateTimeOffset? updatedAt, string source);

    [HttpGet] // GET /api/admin/deepgram
    public async Task<ActionResult<KeyMetaResponse>> Get()
    {
        // 1) env var takes precedence if set
        var env = Environment.GetEnvironmentVariable("DEEPGRAM_API_KEY");
        if (!string.IsNullOrWhiteSpace(env))
        {
            var last4 = env.Length >= 4 ? env[^4..] : env;
            return Ok(new KeyMetaResponse(true, last4, null, "env"));
        }

        // 2) otherwise check encrypted store
        var (exists, last4FromStore, updatedAt) = await _secrets.GetMetaAsync(KeyName);
        return Ok(new KeyMetaResponse(exists, last4FromStore, updatedAt, exists ? "store" : "none"));
    }

    [HttpPost] // POST /api/admin/deepgram
    public async Task<IActionResult> Set([FromBody] KeySetRequest req)
    {
        if (req is null || string.IsNullOrWhiteSpace(req.value))
            return BadRequest(new { error = "Key cannot be empty." });

        await _secrets.SetAsync(KeyName, req.value);
        return Ok(new { ok = true });
    }
}

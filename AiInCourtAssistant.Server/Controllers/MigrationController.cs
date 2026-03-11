using AiInCourtAssistant.Server.Features.TemplateMigration;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/migration")]
[Authorize(Policy = "AllowPineTokenPassthrough")]
public sealed class MigrationController : ControllerBase
{
    private readonly MigrationService _migration;
    private readonly RtfToHtmlService _rtfToHtml;
    private readonly TemplateGenerateService _generate;

    public MigrationController(MigrationService migration, RtfToHtmlService rtfToHtml, TemplateGenerateService generate)
    {
        _migration = migration;
        _rtfToHtml = rtfToHtml;
        _generate = generate;
    }

    public sealed class MigrateRequest
    {
        public string? LegacyText { get; set; }
        public string? RtfContent { get; set; }
        public bool MigrateFullRtf { get; set; } = true;
    }

    /// <summary>Migrate legacy fillpoints to Pine. MigrateFullRtf=true reassembles full RTF; false outputs fillpoints only.</summary>
    [HttpPost("migrate")]
    public ActionResult<MigrateResult> Migrate([FromBody] MigrateRequest request, CancellationToken ct)
    {
        if (request == null)
            return BadRequest(new { error = "Request body required" });

        try
        {
            var result = _migration.MigrateText(request.LegacyText, request.RtfContent, request.MigrateFullRtf);
            return Ok(result);
        }
        catch (Exception ex)
        {
            return StatusCode(500, new { error = "Migration failed", message = ex.Message });
        }
    }

    public sealed class RtfToHtmlRequest
    {
        public string? Content { get; set; }
    }

    /// <summary>Convert RTF or plain text to HTML with fillpoint highlighting. Used when user edits migrated pane and wants to refresh the rendered view.</summary>
    [HttpPost("rtf-to-html")]
    public IActionResult RtfToHtml([FromBody] RtfToHtmlRequest request, CancellationToken ct)
    {
        if (request?.Content == null)
            return BadRequest(new { error = "Content required" });
        var (html, error) = _rtfToHtml.RtfToHtmlWithHighlight(request.Content);
        if (error != null)
            return BadRequest(new { error });
        return Ok(new { html });
    }

    public sealed class GenerateRequest
    {
        public string? Description { get; set; }
        public bool UseLegacyAsExample { get; set; }
        public string? LegacyContent { get; set; }
    }

    public sealed class SuggestFillpointsRequest
    {
        public string? Content { get; set; }
    }

    /// <summary>AI-assisted fillpoint insertion: suggest @[...] fillpoints for existing form text.</summary>
    [HttpPost("suggest-fillpoints")]
    public async Task<IActionResult> SuggestFillpoints([FromBody] SuggestFillpointsRequest request, CancellationToken ct)
    {
        if (request?.Content == null)
            return BadRequest(new { error = "Content required" });

        var (suggestedText, changes, error) = await _generate.SuggestFillpointsAsync(request.Content, ct);
        if (error != null)
            return BadRequest(new { error });
        return Ok(new { suggestedText, changes = changes ?? Array.Empty<string>() });
    }

    /// <summary>Generate Pine template from natural language description (Grok/OpenAI).</summary>
    [HttpPost("generate")]
    public async Task<IActionResult> Generate([FromBody] GenerateRequest request, CancellationToken ct)
    {
        if (request == null)
            return BadRequest(new { error = "Request body required" });

        var (generatedText, error) = await _generate.GenerateAsync(
            request.Description ?? "",
            request.UseLegacyAsExample,
            request.LegacyContent,
            ct);

        if (error != null)
            return BadRequest(new { error });
        return Ok(new { generatedText });
    }
}

using AiInCourtAssistant.Shared.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using System.Text.RegularExpressions;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/ai/row")]
[Authorize]
public sealed class AiRowController : ControllerBase
{
    [HttpPost("summarize")]
    public ActionResult<AiRowResponse> Summarize([FromBody] AiRowRequest req, CancellationToken ct)
    {
        if (req.EventId <= 0) return BadRequest("EventId is required.");
        if (string.IsNullOrWhiteSpace(req.Transcript)) return BadRequest("Transcript is required.");

        var text = req.Transcript.Trim();
        var sentences = Regex.Split(text, @"(?<=[\.\!\?])\s+")
                             .Where(s => !string.IsNullOrWhiteSpace(s))
                             .Take(3);
        var summary = string.Join(" ", sentences);
        if (summary.Length > 600) summary = summary[..600] + "…";

        var suggestions = new List<SuggestionDto>();

        if (Regex.IsMatch(text, @"\bFTA\b|\bfailure to appear\b", RegexOptions.IgnoreCase))
            suggestions.Add(new("Consider Bench Warrant",
                Detail: "Detected potential FTA language.",
                Body: "Verify attendance. If confirmed FTA, prepare bench warrant paperwork.",
                Confidence: 0.8));

        if (Regex.IsMatch(text, @"\bcontinuance\b|\bcontinue(d|s|ing)?\b", RegexOptions.IgnoreCase))
            suggestions.Add(new("Set Continuance",
                Detail: "Continuance requested/mentioned.",
                Body: "Confirm date ranges and propose the next available court date.",
                Confidence: 0.65));

        if (Regex.IsMatch(text, @"\bplea\b", RegexOptions.IgnoreCase))
            suggestions.Add(new("Record Plea",
                Detail: "Plea language detected.",
                Body: "Capture plea agreement details and update orders.",
                Confidence: 0.7));

        return Ok(new AiRowResponse(summary, suggestions, Confidence: 0.6));
    }
}

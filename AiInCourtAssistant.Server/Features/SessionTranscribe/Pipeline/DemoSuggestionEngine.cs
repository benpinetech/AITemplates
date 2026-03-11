using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Models; // TranscriptSegment
// ⬇️ force the Shared SuggestionDto
using SharedSuggestionDto = AiInCourtAssistant.Shared.Models.SuggestionDto;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe
{
    public sealed class DemoSuggestionEngine : ISuggestionEngine
    {
        public Task<IReadOnlyList<SharedSuggestionDto>> GenerateAsync(
            Guid sessionId,
            int eventId,
            IReadOnlyList<TranscriptSegment> segments,
            CancellationToken ct)
        {
            var list = new List<SharedSuggestionDto>();

            var text = string.Join(" ", segments?.Select(s => s.Text) ?? Enumerable.Empty<string>());

            if (Regex.IsMatch(text, @"\bFTA\b|\bfailure to appear\b", RegexOptions.IgnoreCase))
                list.Add(new SharedSuggestionDto("Consider Bench Warrant",
                    Detail: "Detected potential FTA language.",
                    Body: "Verify attendance; if confirmed, prepare bench warrant paperwork.",
                    Confidence: 0.75));

            if (Regex.IsMatch(text, @"\bcontinuance\b|\bcontinue(d|s|ing)?\b", RegexOptions.IgnoreCase))
                list.Add(new SharedSuggestionDto("Set Continuance",
                    Detail: "Continuance mentioned.",
                    Body: "Confirm dates and propose next court date.",
                    Confidence: 0.65));

            if (Regex.IsMatch(text, @"\bplea\b", RegexOptions.IgnoreCase))
                list.Add(new SharedSuggestionDto("Record Plea",
                    Detail: "Plea language detected.",
                    Body: "Capture plea agreement details and update orders.",
                    Confidence: 0.70));

            return Task.FromResult<IReadOnlyList<SharedSuggestionDto>>(list);
        }
    }
}

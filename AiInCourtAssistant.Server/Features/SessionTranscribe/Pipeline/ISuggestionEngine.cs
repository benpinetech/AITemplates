using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Models; // TranscriptSegment
// ⬇️ force the Shared SuggestionDto
using SharedSuggestionDto = AiInCourtAssistant.Shared.Models.SuggestionDto;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe
{
    public interface ISuggestionEngine
    {
        Task<IReadOnlyList<SharedSuggestionDto>> GenerateAsync(
            Guid sessionId,
            int eventId,
            IReadOnlyList<TranscriptSegment> segments,
            CancellationToken ct);
    }
}

using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline;

public interface ISummarizer
{
    Task<string> SummarizeAsync(Guid tenantId, int eventId, IReadOnlyList<TranscriptSegment> segments, CancellationToken ct);
}

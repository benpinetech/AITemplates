using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline;

public interface ITranscriptionProvider
{
    Task<IReadOnlyList<TranscriptSegment>> TranscribeAsync(string localFilePath, CancellationToken ct);
    bool SupportsDiarization { get; }
}

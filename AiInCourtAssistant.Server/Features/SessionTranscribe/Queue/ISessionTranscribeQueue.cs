using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Queue;

public interface ISessionTranscribeQueue
{
    ValueTask EnqueueAsync(StartTranscriptionJob job);
    IAsyncEnumerable<StartTranscriptionJob> DequeueAsync(CancellationToken ct);
}

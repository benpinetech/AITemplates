using System.Threading.Channels;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Queue;

public sealed class InMemorySessionTranscribeQueue : ISessionTranscribeQueue
{
    private readonly Channel<StartTranscriptionJob> _channel = Channel.CreateUnbounded<StartTranscriptionJob>();
    public ValueTask EnqueueAsync(StartTranscriptionJob job) => _channel.Writer.WriteAsync(job);
    public async IAsyncEnumerable<StartTranscriptionJob> DequeueAsync([System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct)
    {
        while (await _channel.Reader.WaitToReadAsync(ct))
            while (_channel.Reader.TryRead(out var job))
                yield return job;
    }
}

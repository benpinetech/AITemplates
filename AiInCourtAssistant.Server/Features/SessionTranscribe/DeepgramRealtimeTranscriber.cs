using Deepgram;
using Microsoft.Extensions.Options;
using System.Collections.Concurrent;
using AiInCourtAssistant.Shared.Models.Live;


namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline;


public interface ITranscriptionProvider : IAsyncDisposable
{
    Task StartAsync(CancellationToken ct);
    Task SendAudioAsync(ReadOnlyMemory<byte> pcmOrOpus, string encoding, int sampleRate, CancellationToken ct);
    Task CloseAsync(CancellationToken ct);
    event EventHandler<(string text, bool isFinal, double confidence)>? OnTranscript;
}


public sealed class DeepgramOptions
{
    public string ApiKey { get; set; } = string.Empty;
    public string Model { get; set; } = "nova-2"; // model family
    public string Language { get; set; } = "en-US";
    public bool InterimResults { get; set; } = true; // partials
    public bool SmartFormat { get; set; } = true; // punctuation, numbers
    public int EndpointingMs { get; set; } = 200; // VAD pause threshold
    public bool Diarize { get; set; } = false; // can enable later
}


public sealed class DeepgramRealtimeTranscriber : ITranscriptionProvider
{
    private readonly DeepgramClient _dg;
    private LiveClient? _live;
    private readonly DeepgramOptions _opts;


    public event EventHandler<(string text, bool isFinal, double confidence)>? OnTranscript;


    public DeepgramRealtimeTranscriber(IOptions<DeepgramOptions> opts)
    {
        _opts = opts.Value;
        _dg = new DeepgramClient(_opts.ApiKey);
    }


    public async Task StartAsync(CancellationToken ct)
    {
        var cfg = new LiveTranscriptionOptions
        {
            Model = _opts.Model,
            Language = _opts.Language,
            InterimResults = _opts.InterimResults,
            Punctuate = _opts.SmartFormat,
            SmartFormat = _opts.SmartFormat,
            Endpointing = _opts.EndpointingMs,
            Diarize = _opts.Diarize
        };
        _live = _dg.CreateLiveClient();


        _live.TranscriptReceived += (s, e) =>
        {
            // Deepgram .NET SDK surfaces alternatives; choose best
            var best = e.Channel?.Alternatives?.FirstOrDefault();
            if (best is null) return;
            var text = best.Transcript ?? string.Empty;
            var conf = best.Confidence ?? 0d;
            var isFinal = e.IsFinal ?? false;
            if (string.IsNullOrWhiteSpace(text)) return;
            OnTranscript?.Invoke(this, (text, isFinal, conf));
        };


        await _live.StartAsync(cfg, ct);
    }


    public async Task SendAudioAsync(ReadOnlyMemory<byte> bytes, string encoding, int sampleRate, CancellationToken ct)
    {
        if (_live is null) return;
        // The .NET SDK handles raw PCM or encoded frames.
        // For browser WebAudio (WebM Opus), pass through bytes and set encoding accordingly in Start options if needed.
        await _live.SendAsync(bytes, ct);
    }


    public async Task CloseAsync(CancellationToken ct)
    {
        if (_live is null) return;
        await _live.FinishAsync(ct); // sends CloseStream; flush finals
        _live.Dispose();
        _live = null;
    }


    public async ValueTask DisposeAsync()
    {
        if (_live is not null)
        {
            await _live.DisposeAsync();
            _live = null;
        }
    }
}
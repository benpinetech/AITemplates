using Microsoft.AspNetCore.SignalR;
using AiInCourtAssistant.Shared.Models;

namespace AiInCourtAssistant.Server.Features.Live;

// Features/Live/ILiveStreamEngine.cs
public interface ILiveStreamEngine
{
    // what your errors show the interface expects:
    Task StartAsync(CancellationToken ct = default);
    Task SendAudioAsync(ReadOnlyMemory<byte> audio, CancellationToken ct = default);
    Task StopAsync(CancellationToken ct = default);

    Task StartAsync(string sendName, int sendType, CancellationToken ct = default);
    Task SwitchEventAsync(Guid sessionId, int eventId, CancellationToken ct = default);
    Task SendAudioAsync(Guid sessionId, byte[] audio, CancellationToken ct = default);
    Task StopAsync(Guid sessionId, CancellationToken ct = default);

    // Minimal StartSessionDto (keep what you already had, this is a placeholder)
    public sealed class StartSessionDto
    {
        public string Courtroom { get; set; } = "";
        public DateTime DocketDate { get; set; } = DateTime.UtcNow.Date;
        public int? ActiveEventId { get; set; }   // to bias names/suggestions
    }
}

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using AiInCourtAssistant.Server.Features.Live.Deepgram;
using AiInCourtAssistant.Shared.Models;
using Microsoft.AspNetCore.SignalR;
using AiInCourtAssistant.Server.Hubs;
using Microsoft.Extensions.Logging;
using AiInCourtAssistant.Server.Features.Ai; // IRowAiService
using Microsoft.Extensions.DependencyInjection; // IServiceScopeFactory

namespace AiInCourtAssistant.Server.Features.Live
{
    public interface ILiveSessionManager
    {
        Task<Guid> StartAsync(string connectionId, int? initialEventId, CancellationToken ct = default);
        Task SwitchEventAsync(Guid sessionId, int eventId, CancellationToken ct = default);
        Task IngestAsync(Guid sessionId, AudioChunkDto chunk, CancellationToken ct = default);
        Task StopAsync(Guid sessionId, CancellationToken ct = default);
        Task StopByConnectionAsync(string connectionId);
    }

    public sealed class LiveSessionManager : ILiveSessionManager
    {
        private readonly DeepgramLiveEngine _engine;
        private readonly IServiceScopeFactory _scopes; // resolve IRowAiService per call
        private readonly IHubContext<LiveTranscribeHub> _hub;
        private readonly ILogger<LiveSessionManager> _log;

        // ✅ Per-session state to prevent bleed across sessions/users
        private sealed class SessionState
        {
            public readonly ConcurrentDictionary<int, DateTimeOffset> LastAiForEvent = new();
            public readonly ConcurrentDictionary<int, IReadOnlyList<SuggestionDto>> LastSuggestionsForEvent = new();
            public readonly ConcurrentDictionary<int, string> TranscriptByEvent = new();
            public readonly ConcurrentDictionary<int, int> LastAiLenByEvent = new();
        }

        private readonly ConcurrentDictionary<Guid, SessionState> _stateBySession = new();
        private SessionState GetState(Guid sessionId) => _stateBySession.GetOrAdd(sessionId, _ => new SessionState());

        // tweakable: how long a transcript should be before we bother the LLM
        private const int MinTranscriptLengthForAi = 120;

        // ✅ Faster feedback so we don’t “miss” next-date/conditions said shortly after other items
        private static readonly TimeSpan AiCooldown = TimeSpan.FromSeconds(6);

        // ✅ Only run AI when transcript grew meaningfully since last AI call
        private const int MinNewCharsForAiRerun = 80;

        public LiveSessionManager(
            DeepgramLiveEngine engine,
            IServiceScopeFactory scopes,
            IHubContext<LiveTranscribeHub> hub,
            ILogger<LiveSessionManager> log)
        {
            _engine = engine;
            _scopes = scopes;
            _hub = hub;
            _log = log;

            // Make sure we don't double-subscribe if DI ever reuses this
            _engine.TranscriptFinalized -= OnTranscriptFinalizedAsync;
            _engine.TranscriptFinalized += OnTranscriptFinalizedAsync;
        }

        public Task<Guid> StartAsync(string connectionId, int? initialEventId, CancellationToken ct = default)
            => _engine.StartAsync(connectionId, initialEventId, ct);

        public Task SwitchEventAsync(Guid sessionId, int eventId, CancellationToken ct = default)
            => _engine.SwitchEventAsync(sessionId, eventId, ct);

        public Task IngestAsync(Guid sessionId, AudioChunkDto chunk, CancellationToken ct = default)
        {
            if (chunk?.Pcm16 is null || chunk.Pcm16.Length == 0) return Task.CompletedTask;

            var bytes = System.Runtime.InteropServices.MemoryMarshal
                .AsBytes(chunk.Pcm16.AsSpan())
                .ToArray();

            return _engine.SendAudioAsync(sessionId, bytes, ct);
        }

        public async Task StopAsync(Guid sessionId, CancellationToken ct = default)
        {
            await _engine.StopAsync(sessionId, ct);

            // ✅ Critical: cleanup per-session caches so nothing bleeds into future sessions
            _stateBySession.TryRemove(sessionId, out _);
        }

        public Task StopByConnectionAsync(string connectionId)
            => _engine.StopByConnectionAsync(connectionId);

        // NOTE: matches DeepgramLiveEngine.TranscriptFinalized: Func<Guid,int,string,Task>
        private async Task OnTranscriptFinalizedAsync(Guid sessionId, int eventId, string text)
        {
            try
            {
                if (eventId == 0) return;                     // ignore generic / pre-routing
                if (string.IsNullOrWhiteSpace(text)) return;

                var trimmed = text.Trim();
                if (trimmed.Length == 0) return;

                var state = GetState(sessionId);

                // ✅ Always accumulate full transcript for this event (finalized chunks only)
                var fullTranscript = state.TranscriptByEvent.AddOrUpdate(
                    eventId,
                    trimmed,
                    (key, prev) =>
                    {
                        if (string.IsNullOrWhiteSpace(prev)) return trimmed;
                        return prev.EndsWith(" ", StringComparison.Ordinal)
                            ? prev + trimmed
                            : prev + " " + trimmed;
                    });

                // Don’t bother AI until we have something reasonably substantial
                if (fullTranscript.Length < MinTranscriptLengthForAi)
                {
                    _log.LogDebug(
                        "Skipping AI for event {EventId} because transcript is too short (len={Length}).",
                        eventId,
                        fullTranscript.Length);
                    return;
                }

                var now = DateTimeOffset.UtcNow;

                // ✅ Per-event cooldown (PER SESSION)
                if (state.LastAiForEvent.TryGetValue(eventId, out var last) &&
                    now - last < AiCooldown)
                {
                    _log.LogDebug(
                        "Skipping AI for event {EventId} (cooldown {Seconds}s still active)",
                        eventId, AiCooldown.TotalSeconds);
                    return;
                }

                // ✅ Only run AI when transcript grew enough since last AI for this event (PER SESSION)
                var lastLen = state.LastAiLenByEvent.TryGetValue(eventId, out var l) ? l : 0;
                if (fullTranscript.Length - lastLen < MinNewCharsForAiRerun)
                {
                    _log.LogDebug(
                        "Skipping AI for event {EventId} because transcript growth is small (delta={Delta}, len={Len}).",
                        eventId,
                        fullTranscript.Length - lastLen,
                        fullTranscript.Length);
                    return;
                }

                state.LastAiForEvent[eventId] = now;
                state.LastAiLenByEvent[eventId] = fullTranscript.Length;

                _log.LogInformation(
                    "AI summarizing event {EventId} (session {Session}) with transcript length {Length}",
                    eventId, sessionId, fullTranscript.Length);

                // Resolve scoped IRowAiService inside a scope (safe with singleton manager)
                using var scope = _scopes.CreateScope();
                var rowAi = scope.ServiceProvider.GetRequiredService<IRowAiService>();

                // ✅ IMPORTANT: pass the full accumulated transcript, not just last chunk
                var ai = await rowAi.SummarizeRowTextAsync(fullTranscript, CancellationToken.None);

                // --- Make suggestions sticky per event (PER SESSION) ----------
                var current = (IReadOnlyList<SuggestionDto>?)ai.Suggestions ?? Array.Empty<SuggestionDto>();

                IReadOnlyList<SuggestionDto> toSend;

                if (current.Count > 0)
                {
                    // New suggestions: cache them and send them
                    state.LastSuggestionsForEvent[eventId] = current;
                    toSend = current;
                }
                else if (state.LastSuggestionsForEvent.TryGetValue(eventId, out var previous) && previous is not null)
                {
                    // No new suggestions, but we had some before: reuse previous ones
                    toSend = previous;
                }
                else
                {
                    // No suggestions at all for this event yet
                    toSend = Array.Empty<SuggestionDto>();
                }

                await _hub.Clients.Group(LiveTranscribeHub.GroupFor(eventId))
                    .SendAsync("AiUpdate", sessionId, eventId, ai.Summary, toSend);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Live AI failed for event {EventId}", eventId);
            }
        }
    }
}

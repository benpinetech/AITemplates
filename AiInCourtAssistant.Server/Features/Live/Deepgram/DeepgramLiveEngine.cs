// Server/Features/Live/Deepgram/DeepgramLiveEngine.cs
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Linq; // Where/Select/ToList
using AiInCourtAssistant.Server.Hubs;
using AiInCourtAssistant.Shared.Models;
using Microsoft.AspNetCore.SignalR;

namespace AiInCourtAssistant.Server.Features.Live.Deepgram
{
    /// <summary>
    /// Deepgram realtime engine. Implements ILiveStreamEngine fully and also exposes
    /// a helper StartAsync(connectionId, initialEventId, ct) used by LiveSessionManager.
    /// </summary>
    public sealed class DeepgramLiveEngine : ILiveStreamEngine
    {
        private sealed record SessionState(
            string ConnId,
            Guid SessionId,
            ClientWebSocket Ws,
            CancellationTokenSource Cts)
        {
            public int CurrentEventId { get; set; }
        }

        private readonly IDeepgramStreamFactory _factory;
        private readonly IHubContext<LiveTranscribeHub> _hub;
        private readonly ILogger<DeepgramLiveEngine> _log;

        // sessionId -> state
        private readonly ConcurrentDictionary<Guid, SessionState> _sessions = new();

        // ✨ NEW: Fired when a final transcript is received for a session/event
        // Args: (sessionId, eventId, text)
        public event Func<Guid, int, string, Task>? TranscriptFinalized;

        public DeepgramLiveEngine(
            IDeepgramStreamFactory factory,
            IHubContext<LiveTranscribeHub> hub,
            ILogger<DeepgramLiveEngine> log)
        {
            _factory = factory;
            _hub = hub;
            _log = log;
        }

        // =====================================================================
        //  Extra helper used by LiveSessionManager (not on interface)
        // =====================================================================
        public async Task<Guid> StartAsync(string connectionId, int? initialEventId, CancellationToken ct)
        {
            var ws = await _factory.CreateAsync(ct);
            var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            var sid = Guid.NewGuid();

            var state = new SessionState(connectionId, sid, ws, cts)
            {
                CurrentEventId = initialEventId ?? 0
            };
            _sessions[sid] = state;

            _log.LogInformation("[Deepgram] StartAsync(connId={connId}, eventId={eventId}) -> session {sid}",
                connectionId, state.CurrentEventId, sid);

            _ = Task.Run(() => ReceiveLoop(state), cts.Token);

            _log.LogInformation("[Deepgram] WS connected for session {sid}", sid);
            return sid;
        }

        // =====================================================================
        //  ILiveStreamEngine — legacy / no-session overloads (kept as no-ops)
        // =====================================================================
        public Task StartAsync(CancellationToken ct = default) => Task.CompletedTask;

        public Task SendAudioAsync(ReadOnlyMemory<byte> audio, CancellationToken ct = default)
        {
            // Not used in our path (we stream by session). Safe no-op.
            _log.LogTrace("[Deepgram] SendAudioAsync(ReadOnlyMemory<byte>) ignored (len={len})", audio.Length);
            return Task.CompletedTask;
        }

        public Task StopAsync(CancellationToken ct = default) => Task.CompletedTask;

        public Task StartAsync(string sendName, int sendType, CancellationToken ct = default)
        {
            // Not used by our Live path. Kept for interface compatibility.
            _log.LogTrace("[Deepgram] StartAsync(name={name}, type={type}) ignored", sendName, sendType);
            return Task.CompletedTask;
        }

        // =====================================================================
        //  ILiveStreamEngine — session-based API (actively used)
        // =====================================================================
        public Task SwitchEventAsync(Guid sessionId, int eventId, CancellationToken ct = default)
        {
            if (_sessions.TryGetValue(sessionId, out var state))
            {
                state.CurrentEventId = eventId;
                _log.LogInformation("[Deepgram] SwitchEvent session {sid} -> event {evt}", sessionId, eventId);
            }
            return Task.CompletedTask;
        }

        public Task SendAudioAsync(Guid sessionId, byte[] audio, CancellationToken ct = default)
        {
            // Convert raw PCM16LE byte[] -> short[] and forward to ingest
            if (audio is null || audio.Length == 0) return Task.CompletedTask;

            // Ensure even number of bytes (2 bytes/sample)
            if ((audio.Length & 1) != 0) Array.Resize(ref audio, audio.Length - 1);

            var pcm16 = MemoryMarshal.Cast<byte, short>(audio.AsSpan()).ToArray();
            var chunk = new AudioChunkDto
            {
                TimestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                Pcm16 = pcm16
            };
            return IngestAsync(sessionId, chunk, ct);
        }

        public async Task StopAsync(Guid sessionId, CancellationToken ct = default)
        {
            if (!_sessions.TryRemove(sessionId, out var state))
                return;

            _log.LogInformation("[Deepgram] StopAsync session {sid}", sessionId);

            try { state.Cts.Cancel(); } catch { }
            try
            {
                if (state.Ws.State == WebSocketState.Open)
                    await state.Ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "client stop", ct);
            }
            catch { /* swallow */ }
            finally
            {
                state.Ws.Dispose();
                state.Cts.Dispose();
            }
        }

        // Helper used internally by SendAudioAsync(Guid, byte[])
        private async Task IngestAsync(Guid sessionId, AudioChunkDto chunk, CancellationToken ct)
        {
            if (!_sessions.TryGetValue(sessionId, out var state))
                return;

            var ws = state.Ws;
            if (ws.State != WebSocketState.Open) return;

            // short[] -> bytes (little-endian)
            var bytes = MemoryMarshal.AsBytes(chunk.Pcm16.AsSpan()).ToArray();
            var seg = new ArraySegment<byte>(bytes);

            try
            {
                await ws.SendAsync(seg, WebSocketMessageType.Binary, endOfMessage: true, cancellationToken: ct);
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "[Deepgram] SendAsync failed for session {sid}", sessionId);
            }
        }

        public async Task StopByConnectionAsync(string connectionId)
        {
            var toStop = _sessions.Values.Where(s => s.ConnId == connectionId).Select(s => s.SessionId).ToList();
            foreach (var sid in toStop)
            {
                try { await StopAsync(sid, CancellationToken.None); } catch { }
            }
        }

        // =====================================================================
        //  Receive loop & message handling
        // =====================================================================
        private async Task ReceiveLoop(SessionState state)
        {
            var ws = state.Ws;
            var buf = new byte[64 * 1024];

            try
            {
                while (!state.Cts.IsCancellationRequested && ws.State == WebSocketState.Open)
                {
                    using var ms = new MemoryStream();
                    WebSocketReceiveResult? res;
                    do
                    {
                        res = await ws.ReceiveAsync(new ArraySegment<byte>(buf), state.Cts.Token);
                        if (res.MessageType == WebSocketMessageType.Close)
                        {
                            _log.LogInformation("[Deepgram] WS close for session {sid}", state.SessionId);
                            return;
                        }
                        ms.Write(buf, 0, res.Count);
                    }
                    while (!res.EndOfMessage);

                    if (res.MessageType == WebSocketMessageType.Text)
                    {
                        var json = Encoding.UTF8.GetString(ms.ToArray());
                        // _log.LogTrace("[Deepgram] <= {json}", json);
                        HandleDeepgramJson(state, json);
                    }
                    // (binary frames from Deepgram are uncommon; ignore)
                }
            }
            catch (OperationCanceledException) { /* normal on stop */ }
            catch (Exception ex)
            {
                _log.LogError(ex, "[Deepgram] ReceiveLoop crashed for session {sid}", state.SessionId);
            }
        }

        private void HandleDeepgramJson(SessionState state, string json)
        {
            try
            {
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;

                // ----- Acceptable message types --------------------------------------
                if (root.TryGetProperty("type", out var typeEl) && typeEl.ValueKind == JsonValueKind.String)
                {
                    var t = typeEl.GetString() ?? "";
                    var tl = t.ToLowerInvariant();
                    if (tl is "metadata" or "speech_started" or "keepalive" or "close")
                        return; // not transcript content
                }

                // ----- Find the channel payload --------------------------------------
                JsonElement channel;

                if (root.TryGetProperty("channel", out channel) || root.TryGetProperty("result", out channel))
                {
                    // ok
                }
                else if (root.TryGetProperty("results", out var results) &&
                         results.ValueKind == JsonValueKind.Object &&
                         results.TryGetProperty("channels", out var chans) &&
                         chans.ValueKind == JsonValueKind.Array &&
                         chans.GetArrayLength() > 0)
                {
                    channel = chans[0];
                }
                else
                {
                    // Not a transcript-bearing message
                    return;
                }

                // ----- Finality flag (root or channel level) -------------------------
                bool isFinal =
                    (root.TryGetProperty("is_final", out var finRoot) && finRoot.ValueKind == JsonValueKind.True) ||
                    (channel.TryGetProperty("is_final", out var finCh) && finCh.ValueKind == JsonValueKind.True);

                // ----- Alternatives -> transcript text --------------------------------
                if (!channel.TryGetProperty("alternatives", out var alts) ||
                    alts.ValueKind != JsonValueKind.Array || alts.GetArrayLength() == 0)
                    return;

                var alt0 = alts[0];
                var text = alt0.TryGetProperty("transcript", out var tEl) ? (tEl.GetString() ?? "") : "";
                if (string.IsNullOrWhiteSpace(text))
                    return;

                double avgLogProb = 0;
                if (alt0.TryGetProperty("avg_logprob", out var lp) && lp.ValueKind == JsonValueKind.Number)
                    avgLogProb = lp.GetDouble();

                // ----- Broadcast to the browser --------------------------------------
                BroadcastTranscript(state, isFinal, text, avgLogProb);
            }
            catch (Exception ex)
            {
                // Log first 200 chars so we can see the shape if it ever changes again
                _log.LogDebug(ex, "[Deepgram] JSON parse error: {snippet}",
                    json.Length > 200 ? json[..200] + "…" : json);
            }
        }

        private void BroadcastTranscript(SessionState state, bool isFinal, string text, double avgLogProb)
        {
            var eventId = state.CurrentEventId;

            _hub.Clients
                .Client(state.ConnId)
                .SendAsync("Transcript", state.SessionId, eventId, isFinal, text, avgLogProb);

            _log.LogDebug("[Deepgram] → Transcript conn={conn} sess={sid} event={evt} final={final} txt='{text}'",
                state.ConnId, state.SessionId, eventId, isFinal, text);

            // ✨ NEW: raise the finalized event so LiveSessionManager can run AI
            if (isFinal && TranscriptFinalized is not null)
            {
                // Fire-and-forget a safe async invoke so we don't block the receive loop
                _ = Task.Run(async () =>
                {
                    try
                    {
                        await TranscriptFinalized.Invoke(state.SessionId, eventId, text);
                    }
                    catch (Exception ex)
                    {
                        _log.LogError(ex, "[Deepgram] TranscriptFinalized handler failed (sess={sid}, evt={evt})", state.SessionId, eventId);
                    }
                });
            }
        }
    }
}

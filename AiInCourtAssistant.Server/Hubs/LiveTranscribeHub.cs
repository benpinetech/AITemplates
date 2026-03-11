// Server/Hubs/LiveTranscribeHub.cs
using System;
using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using Microsoft.AspNetCore.SignalR;
using AiInCourtAssistant.Server.Features.Live;         // ILiveSessionManager
using LiveDtos = AiInCourtAssistant.Shared.Models.Live; // StartLiveSessionDto, SwitchEventDto
using AiInCourtAssistant.Shared.Models;                 // AudioChunkDto

namespace AiInCourtAssistant.Server.Hubs
{
    public sealed class LiveTranscribeHub : Hub
    {
        private readonly ILiveSessionManager _engine;
        public LiveTranscribeHub(ILiveSessionManager engine) => _engine = engine;

        // Track which docket row (event) each connection is currently listening to
        private static readonly ConcurrentDictionary<string, int> _currentEventByConn = new();

        // Helpers
        public static string SessionGroup(Guid sessionId) => $"live:sess:{sessionId:N}";
        public static string GroupFor(int eventId) => $"event-{eventId}";

        // ---------------------------------------------------------------------
        // A) Original API (kept)
        // ---------------------------------------------------------------------

        /// <summary>Original DTO-based start.</summary>
        public async Task<Guid> StartLive(LiveDtos.StartLiveSessionDto dto)
        {
            var sessionId = await _engine.StartAsync(
                Context.ConnectionId,
                dto?.InitialEventId,
                Context.ConnectionAborted);

            await Groups.AddToGroupAsync(Context.ConnectionId, SessionGroup(sessionId));

            // 🔹 NEW: if an initial event was provided, join its event group
            if (dto?.InitialEventId is int ev && ev > 0)
            {
                _currentEventByConn[Context.ConnectionId] = ev;
                await Groups.AddToGroupAsync(Context.ConnectionId, GroupFor(ev));
            }

            return sessionId;
        }

        /// <summary>Original DTO-based switch (legacy name kept).</summary>
        public async Task SwitchEventLegacy(LiveDtos.SwitchEventDto dto, Guid sessionId)
        {
            // 🔹 Move SignalR group membership so AiUpdate reaches the browser
            if (_currentEventByConn.TryGetValue(Context.ConnectionId, out var last) && last > 0 && last != dto.EventId)
                await Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupFor(last));

            _currentEventByConn[Context.ConnectionId] = dto.EventId;
            await Groups.AddToGroupAsync(Context.ConnectionId, GroupFor(dto.EventId));

            await _engine.SwitchEventAsync(sessionId, dto.EventId, Context.ConnectionAborted);
        }

        /// <summary>Original stop.</summary>
        public async Task StopLive(Guid sessionId)
        {
            await _engine.StopAsync(sessionId, Context.ConnectionAborted);
            await Groups.RemoveFromGroupAsync(Context.ConnectionId, SessionGroup(sessionId));

            // best-effort: leave current event group too
            if (_currentEventByConn.TryRemove(Context.ConnectionId, out var last) && last > 0)
                await Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupFor(last));
        }

        // ---------------------------------------------------------------------
        // B) Browser-facing API (what the JS calls)
        // ---------------------------------------------------------------------

        /// <summary>JS: hub.invoke("StartSession", eventId) -> Guid</summary>
        public async Task<Guid> StartSession(int eventId)
        {
            var sessionId = await _engine.StartAsync(
                Context.ConnectionId,
                eventId,
                Context.ConnectionAborted);

            await Groups.AddToGroupAsync(Context.ConnectionId, SessionGroup(sessionId));

            // 🔹 NEW: join initial event group so AiUpdate is received immediately
            if (eventId > 0)
            {
                _currentEventByConn[Context.ConnectionId] = eventId;
                await Groups.AddToGroupAsync(Context.ConnectionId, GroupFor(eventId));
            }

            return sessionId;
        }

        /// <summary>JS: hub.invoke("SwitchEvent", sessionId, eventId)</summary>
        public async Task SwitchEvent(Guid sessionId, int eventId)
        {
            // 🔹 Move SignalR group membership so AiUpdate reaches the browser
            if (_currentEventByConn.TryGetValue(Context.ConnectionId, out var last) && last > 0 && last != eventId)
                await Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupFor(last));

            _currentEventByConn[Context.ConnectionId] = eventId;
            await Groups.AddToGroupAsync(Context.ConnectionId, GroupFor(eventId));

            await _engine.SwitchEventAsync(sessionId, eventId, Context.ConnectionAborted);
        }

        /// <summary>JS: hub.invoke("StopSession", sessionId)</summary>
        public async Task StopSession(Guid sessionId)
        {
            await _engine.StopAsync(sessionId, Context.ConnectionAborted);
            await Groups.RemoveFromGroupAsync(Context.ConnectionId, SessionGroup(sessionId));

            if (_currentEventByConn.TryRemove(Context.ConnectionId, out var last) && last > 0)
                await Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupFor(last));
        }

        // ---------------------------------------------------------------------
        // Audio ingest (shared)
        // ---------------------------------------------------------------------

        /// <summary>
        /// RAW PCM16 (LE, mono, 48k) from browser via MessagePack.
        /// JS sends Uint8Array => MsgPack => binds to byte[] here.
        /// </summary>
        public Task PushAudio(Guid sessionId, byte[] pcm16Bytes)
        {
            if (pcm16Bytes == null || pcm16Bytes.Length == 0)
                return Task.CompletedTask;

            // Ensure even number of bytes (2 bytes/sample)
            if ((pcm16Bytes.Length & 1) != 0)
                Array.Resize(ref pcm16Bytes, pcm16Bytes.Length - 1);

            // Convert bytes -> Int16 samples for engine
            var pcm16 = MemoryMarshal.Cast<byte, short>(pcm16Bytes.AsSpan()).ToArray();

            var chunk = new AudioChunkDto
            {
                TimestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                Pcm16 = pcm16
            };

            return _engine.IngestAsync(sessionId, chunk, Context.ConnectionAborted);
        }

        public override async Task OnDisconnectedAsync(Exception exception)
        {
            if (_currentEventByConn.TryRemove(Context.ConnectionId, out var last) && last > 0)
                await Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupFor(last));

            await _engine.StopByConnectionAsync(Context.ConnectionId);
            await base.OnDisconnectedAsync(exception);
        }
    }
}

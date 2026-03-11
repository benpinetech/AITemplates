using System.Collections.Concurrent;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe
{
    public sealed class SessionContextStore
    {
        private readonly ConcurrentDictionary<Guid, List<DocketHint>> _hints = new();

        public void Set(Guid sessionId, IEnumerable<DocketHint> rows) =>
            _hints[sessionId] = rows.ToList();

        public List<DocketHint> Get(Guid sessionId) =>
            _hints.TryGetValue(sessionId, out var list) ? list : new List<DocketHint>();
    }
}

using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Staging;

public interface ISessionTranscribeRepo
{
    Task<StagedSession> CreateSessionAsync(Guid tenantId, string? courtroom, DateOnly docketDate, CancellationToken ct);
    Task<StagedSession?> GetAsync(Guid sessionId, CancellationToken ct);
    Task SaveAsync(StagedSession session, CancellationToken ct);
    Task MarkCommittedAsync(Guid sessionId, CancellationToken ct);
    string GetSessionsRoot();
}

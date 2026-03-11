using System.Text.Json;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Staging;

public sealed class FileSessionTranscribeRepo : ISessionTranscribeRepo
{
    private readonly IWebHostEnvironment _env;
    private readonly JsonSerializerOptions _json = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    public FileSessionTranscribeRepo(IWebHostEnvironment env) => _env = env;

    public string GetSessionsRoot()
    {
        var root = Path.Combine(_env.ContentRootPath, "App_Data", "SessionTranscripts");
        Directory.CreateDirectory(root);
        return root;
    }

    private string PathFor(Guid id) => System.IO.Path.Combine(GetSessionsRoot(), $"{id}.json");

    public async Task<StagedSession> CreateSessionAsync(Guid tenantId, string? courtroom, DateOnly docketDate, CancellationToken ct)
    {
        var s = new StagedSession { SessionId = Guid.NewGuid(), TenantId = tenantId, Courtroom = courtroom, DocketDate = docketDate };
        await SaveAsync(s, ct);
        return s;
    }

    public async Task<StagedSession?> GetAsync(Guid sessionId, CancellationToken ct)
    {
        var path = PathFor(sessionId);
        if (!File.Exists(path)) return null;
        await using var fs = File.OpenRead(path);
        return await JsonSerializer.DeserializeAsync<StagedSession>(fs, _json, ct);
    }

    public async Task SaveAsync(StagedSession session, CancellationToken ct)
    {
        var path = PathFor(session.SessionId);
        await using var fs = File.Create(path);
        await JsonSerializer.SerializeAsync(fs, session, _json, ct);
    }

    public async Task MarkCommittedAsync(Guid sessionId, CancellationToken ct)
    {
        var s = await GetAsync(sessionId, ct);
        if (s is null) return;
        s.Status = "Committed";
        await SaveAsync(s, ct);
    }
}

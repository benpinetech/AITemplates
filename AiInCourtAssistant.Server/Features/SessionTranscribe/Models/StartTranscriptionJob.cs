namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

public sealed class StartTranscriptionJob
{
    public Guid SessionId { get; }
    public Guid TenantId { get; }
    public string? Courtroom { get; }
    public DateOnly DocketDate { get; }
    public string LocalPath { get; }
    public string OriginalFileName { get; }
    public string? UserId { get; }

    // NEW: carry selected events
    public int[]? EventIds { get; }
    public int[]? CaseIds { get; }

    public StartTranscriptionJob(
        Guid sessionId,
        Guid tenantId,
        string? courtroom,
        DateOnly docketDate,
        string localPath,
        string originalFileName,
        string? userId,
        int[]? eventIds = null,
        int[]? caseIds = null)
    {
        SessionId = sessionId;
        TenantId = tenantId;
        Courtroom = courtroom;
        DocketDate = docketDate;
        LocalPath = localPath;
        OriginalFileName = originalFileName;
        UserId = userId;
        EventIds = eventIds;
        CaseIds = caseIds;
    }
}

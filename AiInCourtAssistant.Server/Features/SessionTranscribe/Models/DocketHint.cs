namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Models
{
    public sealed record DocketHint(int EventId, int CaseId, string? CaseNumber, string? Label);
}

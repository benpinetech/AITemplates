namespace AiInCourtAssistant.Shared.Models
{
    public class TranscriptSaveRequest
    {
        public int CaseId { get; set; }
        public int EventId { get; set; }
        public string EventName { get; set; } = string.Empty;
        public DateTime EventDate { get; set; }
        public string TranscriptText { get; set; } = string.Empty;
        public int MediaFolderId { get; set; }
    }
}

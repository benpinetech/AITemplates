namespace AiInCourtAssistant.Shared.Models
{
    /// <summary>Payload used by Save All to push transcript text (notes) per event.</summary>
    public sealed class NoteUpdateDto
    {
        public int EventId { get; set; }
        public int CaseId { get; set; }
        public string? Note { get; set; }

        public NoteUpdateDto() { }

        public NoteUpdateDto(int eventId, int caseId, string? note)
        {
            EventId = eventId;
            CaseId = caseId;
            Note = note;
        }
    }
}

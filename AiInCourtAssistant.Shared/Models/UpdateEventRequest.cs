namespace AiInCourtAssistant.Shared.Models
{
    public class UpdateEventRequest
    {
        public int EventID { get; set; }
        public string Notes { get; set; } = string.Empty;
        public string Status { get; set; } = string.Empty;
        public DateTime? StartDate { get; set; }
    }
}

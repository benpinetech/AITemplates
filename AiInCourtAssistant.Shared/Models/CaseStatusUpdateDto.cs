namespace AiInCourtAssistant.Shared.Models
{
    public sealed class CaseStatusUpdateDto
    {
        public int CaseID { get; set; }
        public string CaseStatus { get; set; } = "";
    }
}

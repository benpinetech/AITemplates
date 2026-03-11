namespace AiInCourtAssistant.Shared.Models
{
    public class AdvancedEventSearch
    {
        public int Page { get; set; } = 1;
        public int PageSize { get; set; } = 25;
        public string? OrderBy { get; set; } = "StartDate";
        public bool OrderAscending { get; set; } = false;

        public DateTime? EventStartDateFrom { get; set; }
        public DateTime? EventStartDateTo { get; set; }
        public string[]? EventType { get; set; }
        public string[]? EventStatus { get; set; }
        public string[]? EventLocation { get; set; }
        public string[]? EventCategory { get; set; }
        public int? EventAssignmentPersonnelID { get; set; }
        public int? EventInvolvementNameID { get; set; }
        public bool? EventIsComplete { get; set; }
    }
}

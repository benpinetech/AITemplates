namespace AiInCourtAssistant.Shared.Models
{
    public class PineCaseSearchResult
    {
        public int CaseID { get; set; }
        public string Name { get; set; } = string.Empty;
        public string Type { get; set; } = string.Empty;
        public string? Subtype { get; set; }
        public string? CachedPrimaryCaseNumber { get; set; }
        public DateTime? DateReceived { get; set; }
    }

    public class CaseSearchResponse
    {
        public List<PineCaseSearchResult> Items { get; set; } = new();
    }
}

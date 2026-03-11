namespace AiInCourtAssistant.Shared
{
    public class PineCaseSearchResult
    {
        public int RecordsInQuery { get; set; }
        public int RecordsSkipped { get; set; }
        public int RecordsReturned { get; set; }
        public int TotalRecords { get; set; }
        public List<CaseItem> Items { get; set; } = new();
    }

    public class CaseItem
    {
        public int CaseID { get; set; }
        public string Name { get; set; } = string.Empty;
        public string Type { get; set; } = string.Empty;
        public string Subtype { get; set; } = string.Empty;
        public string CachedPrimaryCaseNumber { get; set; } = string.Empty;
        public DateTime DateReceived { get; set; }
        public string CreatedByDisplayName { get; set; } = string.Empty;
    }
}

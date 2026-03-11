using System.Text.Json.Serialization;

public class AdvancedEventSearchResult
{
    [JsonPropertyName("eventID")]
    public required int EventID { get; set; }

    [JsonPropertyName("caseName")]
    public required string CaseName { get; set; }

    [JsonPropertyName("type")]
    public string? EventType { get; set; }

    [JsonPropertyName("status")]
    public string? EventStatus { get; set; }

    [JsonPropertyName("note")]
    public string? Notes { get; set; }

    [JsonPropertyName("startDate")]
    public DateTime? StartDate { get; set; }
    
    [JsonPropertyName("caseID")]
    public int CaseID { get; set; }

}

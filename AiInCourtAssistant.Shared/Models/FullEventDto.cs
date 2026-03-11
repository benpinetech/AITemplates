using System.Text.Json.Serialization;

public class FullEventDto
{
    [JsonPropertyName("EventID")]
    public int EventID { get; set; }

    [JsonPropertyName("caseID")]
    public int CaseID { get; set; }

    [JsonPropertyName("caseName")]
    public string? CaseName { get; set; }

    [JsonPropertyName("category")]
    public string? Category { get; set; }

    [JsonPropertyName("createdByDisplayName")]
    public string? CreatedByDisplayName { get; set; }

    [JsonPropertyName("createdBySystemUserID")]
    public int CreatedBySystemUserID { get; set; }

    [JsonPropertyName("currency")]
    public string? Currency { get; set; }

    [JsonPropertyName("date")]
    public DateTime? Date { get; set; }

    [JsonPropertyName("dateCreated")]
    public DateTime? DateCreated { get; set; }

    [JsonPropertyName("dateUpdated")]
    public DateTime? DateUpdated { get; set; }

    [JsonPropertyName("dueDate")]
    public DateTime? DueDate { get; set; }

    [JsonPropertyName("endDate")]
    public DateTime? EndDate { get; set; }

    [JsonPropertyName("eventAssignmentPersonnelID")]
    public int? EventAssignmentPersonnelID { get; set; }

    [JsonPropertyName("eventInvolvementNameID")]
    public int? EventInvolvementNameID { get; set; }

    [JsonPropertyName("eventName")]
    public string? EventName { get; set; }

    [JsonPropertyName("isActive")]
    public bool IsActive { get; set; }

    [JsonPropertyName("isComplete")]
    public bool IsComplete { get; set; }

    [JsonPropertyName("isDeleted")]
    public bool IsDeleted { get; set; }

    [JsonPropertyName("isSealed")]
    public bool IsSealed { get; set; }

    [JsonPropertyName("link")]
    public string? Link { get; set; }

    [JsonPropertyName("location")]
    public string? Location { get; set; }

    [JsonPropertyName("locationDescription")]
    public string? LocationDescription { get; set; }

    [JsonPropertyName("note")]
    public string? Note { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("sourceID")]
    public int? SourceID { get; set; }

    [JsonPropertyName("startDate")]
    public DateTime? StartDate { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("type")]
    public string? Type { get; set; }

    [JsonPropertyName("updatedByDisplayName")]
    public string? UpdatedByDisplayName { get; set; }

    [JsonPropertyName("updatedBySystemUserID")]
    public int UpdatedBySystemUserID { get; set; }

    // ✅ Aliases for backwards compatibility (and writing)
    [JsonIgnore]
    public string? Notes
    {
        get => Note;
        set => Note = value;
    }

    [JsonIgnore]
    public string? EventStatus
    {
        get => Status;
        set => Status = value;
    }

    [JsonIgnore]
    public DateTime? NewStartDate
    {
        get => StartDate;
        set => StartDate = value;
    }

    // ✅ UI-only
    [JsonIgnore] public bool IsSelected { get; set; }
    [JsonIgnore] public string? UpdatedNote { get; set; }
    [JsonIgnore] public string? UpdatedStatus { get; set; }
    [JsonIgnore] public DateTime? UpdatedStartDate { get; set; }
    [JsonIgnore] public string? UpdatedType { get; set; }
}

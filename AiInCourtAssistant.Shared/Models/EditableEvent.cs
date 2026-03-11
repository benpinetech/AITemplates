public class EditableEvent : AdvancedEventSearchResult
{
    // ✅ UI-only fields for inline editing
    public bool IsEditing { get; set; } = false;
    public bool IsSelected { get; set; } = false;

    public string UpdatedNotes { get; set; } = "";
    public string UpdatedStatus { get; set; } = "";
    public string UpdatedType { get; set; } = "";

    // ✅ Transcript-related fields
    public string? TranscriptText { get; set; }  // New transcript content
    public string? TranscriptUrl { get; set; }   // Link to saved transcript file
    public int? TranscriptMediaId { get; set; }  // Sandbox media ID for the transcript

    // ✅ Media linkage and playback
    public string? AudioUrl { get; set; }        // Optional audio playback URL
    public int CaseID { get; set; }              // Required for media upload
    public int MediaFolderId { get; set; }       // Target folder ID for the transcript

    // ✅ Audio upload support
    public Stream? AudioStream { get; set; }     // Audio blob/file to upload
    public string? AudioFileName { get; set; }   // Optional override
    public string? AudioContentType { get; set; }
    public long? AudioSizeBytes { get; set; }
    public bool IsLiveHighlighted { get; set; }
    public string? DefendantName { get; set; } // if not already present, fill from your API
    public string? CaseNumber { get; set; }    // same

}

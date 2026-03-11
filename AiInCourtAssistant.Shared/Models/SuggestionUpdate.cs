namespace AiInCourtAssistant.Shared.Models;

public sealed class SuggestionUpdate
{
    public string Title { get; set; } = "";      // e.g., "Bench Warrant?"
    public string Reason { get; set; } = "";     // e.g., "Heard: FTA"
    public string ActionCode { get; set; } = ""; // e.g., "BENCH_WARRANT"
    public int? EventId { get; set; }            // for row highlight/targeting
}

using System.Text.Json.Serialization;

namespace AiInCourtAssistant.Shared.Models
{
    public class PineEventSearchWrapper
    {
        [JsonPropertyName("recordsInQuery")]
        public int RecordsInQuery { get; set; }

        [JsonPropertyName("items")]
        public List<AdvancedEventSearchResult> Items { get; set; } = new();
    }
}

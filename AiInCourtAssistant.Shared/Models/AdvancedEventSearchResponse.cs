using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AiInCourtAssistant.Shared.Models
{
    public class AdvancedEventSearchResponse
    {
        [JsonPropertyName("recordsInQuery")]
        public int RecordsInQuery { get; set; }

        [JsonPropertyName("recordsSkipped")]
        public int RecordsSkipped { get; set; }

        [JsonPropertyName("recordsReturned")]
        public int RecordsReturned { get; set; }

        [JsonPropertyName("totalRecords")]
        public int TotalRecords { get; set; }

        [JsonPropertyName("items")]
        public List<AdvancedEventSearchResult> Items { get; set; } = new();
    }
}

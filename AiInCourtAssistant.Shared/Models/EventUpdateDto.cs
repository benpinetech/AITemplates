using System.Text.Json.Serialization;

namespace AiInCourtAssistant.Shared.Models
{
    public class EventUpdateDto
    {
        [JsonPropertyName("eventID")]
        public int EventID { get; set; }

        [JsonPropertyName("notes")]
        public string? Notes { get; set; }

        [JsonPropertyName("eventStatus")]
        public string? EventStatus { get; set; }

        [JsonPropertyName("startDate")]
        public DateTime? StartDate { get; set; }

        [JsonPropertyName("type")]
        public string? Type { get; set; }

        [JsonPropertyName("updatedBySystemUserID")]
        public int UpdatedBySystemUserID { get; set; }

        [JsonPropertyName("updatedByDisplayName")]
        public string? UpdatedByDisplayName { get; set; }
    }
}

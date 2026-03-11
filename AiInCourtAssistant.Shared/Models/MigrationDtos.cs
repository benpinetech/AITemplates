using System.Text.Json.Serialization;

namespace AiInCourtAssistant.Shared.Models;

public sealed class MigrateRequestDto
{
    [JsonPropertyName("legacyText")]
    public string? LegacyText { get; set; }

    [JsonPropertyName("rtfContent")]
    public string? RtfContent { get; set; }

    [JsonPropertyName("migrateFullRtf")]
    public bool MigrateFullRtf { get; set; } = true;
}

public sealed class FillpointPairDto
{
    [JsonPropertyName("legacy")]
    public string Legacy { get; set; } = "";

    [JsonPropertyName("migrated")]
    public string Migrated { get; set; } = "";
}

public sealed class MigrateResultDto
{
    [JsonPropertyName("legacyText")]
    public string LegacyText { get; set; } = "";

    [JsonPropertyName("migratedText")]
    public string MigratedText { get; set; } = "";

    [JsonPropertyName("fillpoints")]
    public List<FillpointPairDto> Fillpoints { get; set; } = new();

    [JsonPropertyName("reviewTokens")]
    public List<string> ReviewTokens { get; set; } = new();

    [JsonPropertyName("legacyHtml")]
    public string? LegacyHtml { get; set; }

    [JsonPropertyName("migratedHtml")]
    public string? MigratedHtml { get; set; }
}

using System.Text.Json.Serialization;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

// API responses
public sealed record StartSessionResponse(Guid SessionId);

public sealed record SessionPreviewItem(
    int EventId,
    string NotesSummary,
    IReadOnlyList<SuggestionDto> Suggestions,
    string TranscriptExcerpt,
    double Confidence
);

public sealed record SessionPreviewResponse(
    Guid SessionId,
    IReadOnlyList<SessionPreviewItem> Items
);

// Internal staging models
public sealed record SuggestionDto(
    string Kind,
    string Title,
    string? Body,
    double Confidence
);

public sealed record TranscriptSegment(
    string? Speaker,
    int StartMs,
    int EndMs,
    string Text
);

public sealed record StagedEventChunk(
    int EventId,
    int CaseId,
    double Confidence,
    string NotesSummary,
    string TranscriptExcerpt,
    IReadOnlyList<SuggestionDto> Suggestions,
    IReadOnlyList<TranscriptSegment> Segments
);

// ❗ Must be public because the repo interface returns it
public sealed class StagedSession // persisted to JSON
{
    public Guid SessionId { get; init; }
    public Guid TenantId { get; init; }
    public string? Courtroom { get; init; }
    public DateOnly DocketDate { get; init; }
    public string Status { get; set; } = "Processing"; // Processing|Complete|Failed|Committed
    public List<StagedEventChunk> Chunks { get; init; } = new();
    public string? Error { get; set; }
}

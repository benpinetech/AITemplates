namespace AiInCourtAssistant.Shared.Models;

public sealed record AiPreviewDto(
    Guid SessionId,
    int EventId,
    string Kind,               // "transcript" | "summary" | "suggestions"
    string? Text,
    string[]? Suggestions
);

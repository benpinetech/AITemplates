// Shared/Models/SuggestionDto.cs
namespace AiInCourtAssistant.Shared.Models;

public sealed record SuggestionDto(
    string Title,
    string? Detail = null,
    string? Body = null,
    double Confidence = 0.0
);

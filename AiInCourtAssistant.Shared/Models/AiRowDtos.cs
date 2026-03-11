// Shared/Models/AiRowDtos.cs
namespace AiInCourtAssistant.Shared.Models;

public sealed record AiRowRequest(
    int EventId,
    string Transcript,
    string? CaseNumber,
    string? Defendant);

public sealed record AiRowResponse(
    string Summary,
    IReadOnlyList<SuggestionDto> Suggestions,
    double Confidence = 0.0);

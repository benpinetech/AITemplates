namespace AiInCourtAssistant.Shared.Models;

public sealed class WordTime
{
    public string Word { get; set; } = "";
    public double Start { get; set; }           // seconds
    public double End { get; set; }
    public double? LogProb { get; set; }
}

public sealed class TranscriptUpdate
{
    public bool Final { get; set; }
    public string Text { get; set; } = "";
    public double AvgLogProb { get; set; }
    public List<WordTime>? Words { get; set; }  // optional word-level timestamps
    public string? Source { get; set; }         // "live" | "upload"
}

namespace AiInCourtAssistant.Shared.Models.Live;

public sealed class LiveTranscribeOptions
{
    public string Provider { get; set; } = "Deepgram";
    public DeepgramOptions Deepgram { get; set; } = new();
}

public sealed class DeepgramOptions
{
    public string? ApiKey { get; set; } // optional; prefer Admin store
    public string Model { get; set; } = "nova-2-general";
    public bool Punctuate { get; set; } = true;
    public bool SmartFormat { get; set; } = true;
    public bool Diarize { get; set; } = true;
    public bool InterimResults { get; set; } = true;
    public bool Paragraphs { get; set; } = true;

    public bool EnableSummarize { get; set; } = true;
    public string SummarizeStyle { get; set; } = "paragraph";
    public bool EnableTopics { get; set; } = false;
    public bool EnableRedaction { get; set; } = false;

    public int SampleRateHz { get; set; } = 16000;  // must match your audio stream
    public string Encoding { get; set; } = "linear16";
}

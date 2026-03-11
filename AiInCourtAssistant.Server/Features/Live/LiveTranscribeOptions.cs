namespace AiInCourtAssistant.Server.Features.Live;

public sealed class LiveTranscribeOptions
{
    public string Mode { get; set; } = "Basic";
    public EnhancedOptions Enhanced { get; set; } = new();
    public sealed class EnhancedOptions
    {
        public int WindowSeconds { get; set; } = 12;
        public int OverlapSeconds { get; set; } = 2;
        public int SilenceMsToEndpoint { get; set; } = 600;
        public int MinPartialIntervalMs { get; set; } = 250;
        public int StabilityHoldMs { get; set; } = 800;
        public int MaxBufferedSeconds { get; set; } = 30;
        public int BeamSize { get; set; } = 5;
        public double Temperature { get; set; } = 0.2;
        public double CompressionRatioThreshold { get; set; } = 2.4;
        public double LogProbThreshold { get; set; } = -1.0;
        public double NoSpeechThreshold { get; set; } = 0.6;
        public string Model { get; set; } = "ggml-large-v3-q5_1";
    }
}

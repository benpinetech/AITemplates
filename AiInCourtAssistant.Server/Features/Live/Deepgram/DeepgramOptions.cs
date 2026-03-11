// Server/Features/Live/DeepgramOptions.cs
namespace AiInCourtAssistant.Server.Features.Live
{
    public sealed class DeepgramOptions
    {
        // REQUIRED
        public string ApiKey { get; set; } = string.Empty;

        // Realtime WS endpoint
        public string Url { get; set; } = "wss://api.deepgram.com/v1/listen";

        // Recognition settings
        public string Model { get; set; } = "nova-2-general";
        public string Language { get; set; } = "en";   // Deepgram expects short language code
        public bool SmartFormat { get; set; } = true;
        public bool Punctuate { get; set; } = true;
        public bool InterimResults { get; set; } = true;
        public bool Diarize { get; set; } = false;
        public int EndpointingMs { get; set; } = 200;  // 0 = off; typical 200–500

        // Transport/audio (we’re sending WebM/Opus from the browser)
        public string Encoding { get; set; } = "opus";
        public int SampleRate { get; set; } = 48000;   // browser mic is usually 48k for Opus
        public int Channels { get; set; } = 1;

        // Extra raw query params if you need to experiment
        public Dictionary<string, string>? Extra { get; set; }

        public Uri BuildUri()
        {
            var qs = new List<string>
            {
                $"model={Uri.EscapeDataString(Model)}",
                $"language={Uri.EscapeDataString(Language)}",
                $"encoding={Uri.EscapeDataString(Encoding)}",
                $"sample_rate={SampleRate}",
                $"channels={Channels}",
                $"smart_format={(SmartFormat ? "true" : "false")}",
                $"punctuate={(Punctuate ? "true" : "false")}",
                $"interim_results={(InterimResults ? "true" : "false")}",
                $"endpointing={EndpointingMs}",
                $"diarize={(Diarize ? "true" : "false")}"
            };

            if (Extra is not null)
            {
                foreach (var kv in Extra)
                {
                    if (!string.IsNullOrWhiteSpace(kv.Key) && kv.Value is not null)
                        qs.Add($"{kv.Key}={Uri.EscapeDataString(kv.Value)}");
                }
            }

            var uri = $"{Url}?{string.Join("&", qs)}";
            return new Uri(uri);
        }
    }
}

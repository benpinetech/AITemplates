namespace AiInCourtAssistant.Shared.Config;
public sealed class DeepgramSettings
{
    public string ApiKey { get; set; } = "";
    public string RealtimeUrl { get; set; } = "wss://api.deepgram.com/v1/listen";
}

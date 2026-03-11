// Server/Features/SessionTranscribe/Pipeline/StructuredAiSummarizer.cs
using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;
using AiInCourtAssistant.Server.Services;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline
{
    public sealed class StructuredAiSummarizer : ISummarizer
    {
        private readonly ISecretStore _secrets;
        private static readonly HttpClient _http = new();

        public StructuredAiSummarizer(ISecretStore secrets)
        {
            _secrets = secrets;
        }

        public async Task<string> SummarizeAsync(
            Guid tenantId,
            int eventId,
            IReadOnlyList<TranscriptSegment> segments,
            CancellationToken ct)
        {
            // 1) Pull API key from your encrypted secret store
            var apiKey = await _secrets.GetAsync("OpenAI:ApiKey", ct);
            if (string.IsNullOrWhiteSpace(apiKey))
                throw new InvalidOperationException("OpenAI API key is not set. Add it in Admin → OpenAI Key.");

            // 2) Build prompt from segments (keep it short-ish)
            var text = string.Join("\n",
                (segments ?? Array.Empty<TranscriptSegment>())
                .Where(s => !string.IsNullOrWhiteSpace(s?.Text))
                .Select(s => s!.Text!.Trim()));

            var system = """
You are a court clerk assistant. Read the excerpt and produce:
1) A concise 2–4 sentence summary (≤ 80 words).
2) A key-value block with these keys (if known): Plea, Finding/Disposition, Fine/Fees, Next Date, Conditions, Action/Items.
If unknown, use "-".
""";

            var user = $"Case context: eventId={eventId}\n\nTranscript excerpt:\n\"\"\"\n{text}\n\"\"\"\n\nProduce the two outputs.";

            // 3) Prepare request
            var payload = new
            {
                model = "gpt-4o-mini",
                temperature = 0.2,
                messages = new object[]
                {
                    new { role = "system", content = system },
                    new { role = "user", content = user }
                }
            };

            using var req = new HttpRequestMessage(HttpMethod.Post, "https://api.openai.com/v1/chat/completions");
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            req.Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");

            using var resp = await _http.SendAsync(req, ct);
            var raw = await resp.Content.ReadAsStringAsync(ct);
            resp.EnsureSuccessStatusCode();

            using var doc = JsonDocument.Parse(raw);
            var content = doc.RootElement
                .GetProperty("choices")[0]
                .GetProperty("message")
                .GetProperty("content")
                .GetString();

            return content ?? string.Empty;
        }
    }
}

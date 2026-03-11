using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;

namespace AiInCourtAssistant.Server.Services
{
    public interface IOpenAITranscriptionService
    {
        Task<string> TranscribeAsync(
            Stream audio,
            string fileName,
            string mimeType,
            string language,
            CancellationToken ct = default);
    }

    public sealed class OpenAITranscriptionService : IOpenAITranscriptionService
    {
        private readonly HttpClient _http = new();
        private readonly ISecretStore _secrets;

        public OpenAITranscriptionService(ISecretStore secrets) => _secrets = secrets;

        public async Task<string> TranscribeAsync(
            Stream audio,
            string fileName,
            string mimeType,
            string language,
            CancellationToken ct = default)
        {
            var apiKey = await _secrets.GetAsync("OpenAI:ApiKey", ct);
            if (string.IsNullOrWhiteSpace(apiKey))
                throw new InvalidOperationException("OpenAI API key is not configured.");

            // Always send the stream from the beginning
            if (audio.CanSeek) audio.Position = 0;

            using var req = new HttpRequestMessage(
                HttpMethod.Post,
                "https://api.openai.com/v1/audio/transcriptions");

            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);

            using var form = new MultipartFormDataContent();

                      // Optional language hint (ISO-639-1, e.g., "en")
            if (!string.IsNullOrWhiteSpace(language))
                form.Add(new StringContent(language), "language");

            // The actual audio bytes
            var filePart = new StreamContent(audio);
            filePart.Headers.ContentType = new MediaTypeHeaderValue(mimeType);
            form.Add(filePart, "file", fileName);

            // (Optional) ask for JSON response
            form.Add(new StringContent("json"), "response_format");

            req.Content = form;

            using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
            var raw = await resp.Content.ReadAsStringAsync(ct);

            if (!resp.IsSuccessStatusCode)
                throw new Exception($"OpenAI error {resp.StatusCode}: {raw}");

            // Response shape: { "text": "..." }
            using var doc = JsonDocument.Parse(raw);
            if (doc.RootElement.TryGetProperty("text", out var t))
                return t.GetString() ?? string.Empty;

            throw new Exception($"Unexpected OpenAI response: {raw}");
        }
    }
}

using AiInCourtAssistant.Server.Services;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline
{
    /// <summary>
    /// Batch (upload/preview) client for Deepgram /v1/listen.
    /// Sends RAW audio bytes (not multipart). Options go in the query string.
    /// </summary>
    public sealed class DeepgramBatchClient
    {
        private readonly HttpClient _http;
        private readonly ISecretStore _secrets;
        private readonly ILogger<DeepgramBatchClient> _log;
        private readonly IConfiguration _cfg;

        private static readonly Dictionary<string, string> _mimeByExt = new(StringComparer.OrdinalIgnoreCase)
        {
            [".wav"] = "audio/wav",
            [".mp3"] = "audio/mpeg",
            [".m4a"] = "audio/mp4",
            [".mp4"] = "video/mp4",     // Deepgram will accept video containers too
            [".webm"] = "audio/webm",
            [".ogg"] = "audio/ogg",
            [".flac"] = "audio/flac"
        };

        public DeepgramBatchClient(HttpClient http, ISecretStore secrets, ILogger<DeepgramBatchClient> log, IConfiguration cfg)
        {
            _http = http;
            _secrets = secrets;
            _log = log;
            _cfg = cfg;
        }

        /// <summary>
        /// Path overload — infers Content-Type from file extension and streams raw bytes.
        /// </summary>
        public async Task<DeepgramBatchResult> TranscribeAsync(string localFilePath, CancellationToken ct = default)
        {
            if (string.IsNullOrWhiteSpace(localFilePath) || !File.Exists(localFilePath))
                throw new FileNotFoundException("Audio file not found.", localFilePath);

            var ext = Path.GetExtension(localFilePath);
            _mimeByExt.TryGetValue(ext, out var mime);
            if (string.IsNullOrWhiteSpace(mime)) mime = "application/octet-stream";

            await using var fs = new FileStream(localFilePath, FileMode.Open, FileAccess.Read, FileShare.Read);
            return await TranscribeAsync(fs, mime, ct);
        }

        /// <summary>
        /// Core upload — sends RAW audio to Deepgram /v1/listen with query-string options.
        /// </summary>
        public async Task<DeepgramBatchResult> TranscribeAsync(Stream file, string contentType, CancellationToken ct = default)
        {
            // 1) Resolve API key
            var apiKey = Environment.GetEnvironmentVariable("DEEPGRAM_API_KEY")
                         ?? await _secrets.GetAsync("Deepgram:ApiKey", ct);
            if (string.IsNullOrWhiteSpace(apiKey))
                throw new InvalidOperationException("Deepgram API key is not set (Admin → Deepgram).");

            // 2) Pull options from configuration
            var dg = _cfg.GetSection("Deepgram");
            string Get(string key, string fallback = "") => string.IsNullOrWhiteSpace(dg[key]) ? fallback : dg[key]!;
            bool GetBool(string key, bool fallback) => dg[key] is null ? fallback : (bool.TryParse(dg[key], out var b) ? b : fallback);

            var baseUrl = Get("BatchUrl", "https://api.deepgram.com/v1/listen");
            var model = Get("Model", "nova-2-general");
            var language = Get("Language", "en");
            var smartFormat = GetBool("SmartFormat", true);
            var punctuate = GetBool("Punctuate", true);
            var diarize = GetBool("Diarize", true);
            var paragraphs = GetBool("Paragraphs", true);
            var interim = GetBool("InterimResults", false);

            // Batch transcription does NOT support endpointing.
            // Keep query-string strictly batch-safe.
            var qsParts = new List<string>
{
    $"model={Uri.EscapeDataString(model)}",
    $"language={Uri.EscapeDataString(language)}",
    $"smart_format={(smartFormat ? "true" : "false")}",
    $"punctuate={(punctuate ? "true" : "false")}",
    $"diarize={(diarize ? "true" : "false")}",
    $"paragraphs={(paragraphs ? "true" : "false")}",
    $"interim_results={(interim ? "true" : "false")}"
};


            var url = baseUrl + "?" + string.Join("&", qsParts);

            // 3) Prepare content (RAW bytes; NOT multipart)
            if (file is null) throw new ArgumentNullException(nameof(file));
            if (file.CanSeek) file.Position = 0;

            if (string.IsNullOrWhiteSpace(contentType) ||
                contentType.Equals("application/octet-stream", StringComparison.OrdinalIgnoreCase))
            {
                contentType = "audio/wav"; // safe default
            }

            var content = new StreamContent(file);
            content.Headers.ContentType = MediaTypeHeaderValue.Parse(contentType);

            // 4) Build and send request
            using var req = new HttpRequestMessage(HttpMethod.Post, url);
            req.Headers.Authorization = new AuthenticationHeaderValue("Token", apiKey);
            req.Content = content;

            var remaining = file.CanSeek ? (file.Length - file.Position) : -1;
            _log.LogInformation("[DeepgramBatch] POST {Url} ct={ContentType} len={Len}",
                url, content.Headers.ContentType, remaining);

            var res = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
            var body = await res.Content.ReadAsStringAsync(ct);

            // Log full JSON once so we can debug shapes
            _log.LogWarning("DEEPGRAM RAW JSON: {Json}", body);

            if (!res.IsSuccessStatusCode)
            {
                _log.LogError("Deepgram batch error {Status}: {Body}", (int)res.StatusCode, body);
                res.EnsureSuccessStatusCode();
            }

            return DeepgramBatchResult.Parse(body);
        }
    }

    public sealed record DeepgramBatchResult(string Transcript, string? Summary)
    {
        public static DeepgramBatchResult Parse(string json)
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            var transcript = ExtractTranscript(root);
            var summary = ExtractSummary(root);

            return new DeepgramBatchResult(transcript, summary);
        }

        /// <summary>
        /// Extracts the main transcript from Deepgram's batch JSON.
        /// Very literal to the shape we see in your logs:
        /// results.channels[0].alternatives[0].paragraphs.transcript
        /// then results.channels[0].alternatives[0].transcript
        /// then "longest transcript/text anywhere".
        /// </summary>
        private static string ExtractTranscript(JsonElement root)
        {
            try
            {
                // ----- 1) Preferred path: paragraphs.transcript -----
                if (root.TryGetProperty("results", out var results) &&
                    results.ValueKind == JsonValueKind.Object &&
                    results.TryGetProperty("channels", out var channels) &&
                    channels.ValueKind == JsonValueKind.Array &&
                    channels.GetArrayLength() > 0)
                {
                    var channel0 = channels[0];

                    if (channel0.TryGetProperty("alternatives", out var alternatives) &&
                        alternatives.ValueKind == JsonValueKind.Array &&
                        alternatives.GetArrayLength() > 0)
                    {
                        var alt0 = alternatives[0];

                        // paragraphs.transcript
                        if (alt0.TryGetProperty("paragraphs", out var paragraphsEl) &&
                            paragraphsEl.ValueKind == JsonValueKind.Object &&
                            paragraphsEl.TryGetProperty("transcript", out var pTransEl) &&
                            pTransEl.ValueKind == JsonValueKind.String)
                        {
                            var pt = pTransEl.GetString();
                            if (!string.IsNullOrWhiteSpace(pt))
                                return pt!.Trim();
                        }

                        // 2) alternatives[0].transcript
                        if (alt0.TryGetProperty("transcript", out var tEl) &&
                            tEl.ValueKind == JsonValueKind.String)
                        {
                            var t = tEl.GetString();
                            if (!string.IsNullOrWhiteSpace(t))
                                return t!.Trim();
                        }
                    }
                }

                // ----- 3) Deep fallback: longest "transcript"/"text" anywhere -----
                string best = string.Empty;

                void Scan(JsonElement el)
                {
                    switch (el.ValueKind)
                    {
                        case JsonValueKind.Object:
                            foreach (var prop in el.EnumerateObject())
                            {
                                if ((prop.NameEquals("transcript") || prop.NameEquals("text")) &&
                                    prop.Value.ValueKind == JsonValueKind.String)
                                {
                                    var s = prop.Value.GetString() ?? string.Empty;
                                    if (s.Length > best.Length)
                                        best = s;
                                }

                                Scan(prop.Value);
                            }
                            break;

                        case JsonValueKind.Array:
                            foreach (var child in el.EnumerateArray())
                                Scan(child);
                            break;
                    }
                }

                Scan(root);
                return best.Trim();
            }
            catch
            {
                // If Deepgram changes shape unexpectedly, fail soft.
                return string.Empty;
            }
        }

        private static string? ExtractSummary(JsonElement root)
        {
            // Keep simple: if Deepgram ever returns a top-level "summary", use it.
            if (root.TryGetProperty("summary", out var sumEl) &&
                sumEl.ValueKind == JsonValueKind.String)
            {
                return sumEl.GetString();
            }

            return null;
        }
    }
}

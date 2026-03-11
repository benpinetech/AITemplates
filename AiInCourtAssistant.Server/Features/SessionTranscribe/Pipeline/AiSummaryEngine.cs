using System.Net.Http.Json;
using System.Text.Json;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline
{
    public interface IAiSummaryEngine
    {
        Task<string?> SummarizeAsync(
            string caseNumber,
            string defendant,
            string eventType,
            DateTime? startLocal,
            string transcript,
            string? priorNotes,
            CancellationToken ct);
    }

    public sealed class AiSummaryEngine : IAiSummaryEngine
    {
        private readonly HttpClient _http;
        private readonly IConfiguration _cfg;

        public AiSummaryEngine(HttpClient http, IConfiguration cfg)
        {
            _http = http;
            _cfg = cfg;
        }

        public async Task<string?> SummarizeAsync(
            string caseNumber,
            string defendant,
            string eventType,
            DateTime? startLocal,
            string transcript,
            string? priorNotes,
            CancellationToken ct)
        {
            if (string.IsNullOrWhiteSpace(transcript))
                return null;

            // --- System prompt: INTERNAL EVENT NOTES (not Notes for Court) ---
            var system =
                "You are a municipal/traffic court clerk assistant.\n" +
                "Your job is to create concise **internal event notes** for the case record.\n" +
                "Do NOT write attorney \"Notes for Court\" and do NOT give legal advice.\n\n" +
                "Guidelines:\n" +
                "- Base everything ONLY on what is actually said in the transcript.\n" +
                "- Be terse, neutral, and action-oriented, written for a clerk.\n" +
                "- Focus on outcome, next dates, warrants/FTA, fines/costs, and any conditions.\n" +
                "- If something is not stated, use \"—\" (do NOT guess).\n\n" +
                "Output format (exactly this structure):\n" +
                "Summary: <1–3 short sentences describing what happened and what needs to be done>\n" +
                "Plea: <guilty / not guilty / no contest / deferred / —>\n" +
                "Finding/Disposition: <e.g., continued, bench warrant, dismissed, sentenced, completed, —>\n" +
                "Fine/Fees: <total or description, or \"—\">\n" +
                "Next Date: <specific date/time if set, or \"—\">\n" +
                "Conditions: <probation, classes, community service, payment plan, etc., or \"—\">\n" +
                "Action Items: <clear clerk actions like enter BW, update status, set review, close case, etc., or \"—\">";

            var startText = startLocal.HasValue
                ? startLocal.Value.ToString("MM/dd/yyyy h:mm tt")
                : "—";

            // User prompt with transcript + any existing event notes
            var user =
                $"Case: {caseNumber} — {defendant}\n" +
                $"Event Type: {eventType}\n" +
                $"Event Start (local): {startText}\n\n" +
                "Transcript:\n" +
                "\"\"\"\n" +
                $"{transcript}\n" +
                "\"\"\"\n\n" +
                "Existing event notes for this case (may be empty):\n" +
                "\"\"\"\n" +
                $"{(priorNotes ?? string.Empty)}\n" +
                "\"\"\"\n\n" +
                "Update or create the internal event notes using the required format above.\n" +
                "If the transcript is clearly an FTA / no-show, make sure the Summary and Action Items mention bench warrant / FTA status when appropriate.";

            // --- Call your LLM endpoint (OpenAI/Azure OpenAI/etc.) ---
            var model = _cfg["Ai:Model"] ?? "gpt-4o-mini";
            var apiKey = _cfg["Ai:ApiKey"] ?? throw new InvalidOperationException("Ai:ApiKey missing");
            var endpoint = _cfg["Ai:Endpoint"]; // if using Azure OpenAI, set full URI here; else use OpenAI default

            var uri = string.IsNullOrWhiteSpace(endpoint)
                ? "https://api.openai.com/v1/chat/completions"
                : endpoint;

            using var req = new HttpRequestMessage(HttpMethod.Post, uri);
            req.Headers.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", apiKey);

            var payload = new
            {
                model,
                temperature = 0.15, // a bit stricter for more consistent structure
                messages = new object[]
                {
                    new { role = "system", content = system },
                    new { role = "user",   content = user   }
                }
            };

            req.Content = JsonContent.Create(payload);

            var resp = await _http.SendAsync(req, ct);
            resp.EnsureSuccessStatusCode();

            var json = await resp.Content.ReadAsStringAsync(ct);
            using var doc = JsonDocument.Parse(json);
            var content = doc.RootElement
                             .GetProperty("choices")[0]
                             .GetProperty("message")
                             .GetProperty("content")
                             .GetString();

            return content?.Trim();
        }
    }
}

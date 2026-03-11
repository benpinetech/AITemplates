using AiInCourtAssistant.Shared.Models;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;                 // <-- added
using System.Text.Json;

namespace AiInCourtAssistant.Server.Services
{
    public sealed class EventService : IEventService
    {
        private readonly IHttpClientFactory _http;
        private readonly IHttpContextAccessor _ctx;

        public EventService(IHttpClientFactory http, IHttpContextAccessor ctx)
        {
            _http = http;
            _ctx = ctx;
        }

        private HttpClient CreateAuthedClient()
        {
            var client = _http.CreateClient("AuthorizedClient");

            // Also set Authorization explicitly from the incoming request (defensive)
            var bearer = _ctx.HttpContext?.Request?.Headers["Authorization"].ToString();
            if (!string.IsNullOrWhiteSpace(bearer))
            {
                var raw = bearer.Replace("Bearer ", "", StringComparison.OrdinalIgnoreCase).Trim();
                client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", raw);
            }
            return client;
        }

        // -------- Advanced search (POST proxy to upstream) --------
        public async Task<AdvancedEventSearchResponse?> SearchAsync(
            AdvancedEventSearch request,
            CancellationToken ct = default)
        {
            var client = CreateAuthedClient();
            var resp = await client.PostAsJsonAsync("api/event/search", request, ct); // adjust if your upstream differs
            if (!resp.IsSuccessStatusCode) return null;
            return await resp.Content.ReadFromJsonAsync<AdvancedEventSearchResponse>(cancellationToken: ct);
        }

        // -------- Single event update --------
        public async Task<bool> UpdateEventAsync(
            EventUpdateDto update,
            CancellationToken ct = default)
        {
            var client = CreateAuthedClient();

            // 1) Load full event payload
            var original = await client.GetFromJsonAsync<FullEventDto>($"api/event/{update.EventID}", ct);
            if (original is null) return false;

            // 2) Apply changes
            original.Note = update.Notes;
            original.Status = update.EventStatus;
            if (!string.IsNullOrWhiteSpace(update.Type))
                original.Type = update.Type;

            original.StartDate = update.StartDate;
            original.UpdatedBySystemUserID = update.UpdatedBySystemUserID;
            original.UpdatedByDisplayName = update.UpdatedByDisplayName;

            // 3) PUT back
            var put = await client.PutAsJsonAsync($"api/event/{update.EventID}", original, ct);
            return put.IsSuccessStatusCode;
        }

        // -------- Case status update --------
        public async Task<bool> UpdateCaseStatusAsync(int caseId, string newStatus, CancellationToken ct = default)
        {
            var client = CreateAuthedClient();

            // 1) GET case
            var get = await client.GetAsync($"api/cases/{caseId}", ct);
            if (!get.IsSuccessStatusCode) return false;

            var json = await get.Content.ReadAsStringAsync(ct);
            var root = JsonSerializer.Deserialize<Dictionary<string, object?>>(json) ?? new();

            // 2) Update a likely status field (adjust to your model)
            if (root.ContainsKey("CaseStatus")) root["CaseStatus"] = newStatus;
            else if (root.ContainsKey("Status")) root["Status"] = newStatus;
            else if (root.ContainsKey("StatusCode")) root["StatusCode"] = newStatus;
            else root["CaseStatus"] = newStatus;

            // 3) PUT back
            var put = await client.PutAsJsonAsync($"api/cases/{caseId}", root, ct);
            return put.IsSuccessStatusCode;
        }

        // -------- Bulk notes save --------
        public async Task<int> SaveBulkNotesAsync(
            IEnumerable<NoteUpdateDto> items,
            CancellationToken ct = default)
        {
            if (items is null) return 0;

            var client = CreateAuthedClient();
            var saved = 0;

            foreach (var it in items)
            {
                try
                {
                    var full = await client.GetFromJsonAsync<FullEventDto>($"api/event/{it.EventId}", ct);
                    if (full is null) continue;

                    full.Note = it.Note;

                    var put = await client.PutAsJsonAsync($"api/event/{it.EventId}", full, ct);
                    if (put.IsSuccessStatusCode) saved++;
                }
                catch
                {
                    // Skip failed item, continue with the rest
                }
            }

            return saved;
        }

        // -------- Upload transcript text as a media file (NEW) --------
        public async Task UploadTranscriptAsync(
            int eventId,
            string fileName,
            string content,
            CancellationToken ct = default)
        {
            var client = CreateAuthedClient();

            // Build multipart/form-data with a single text/plain file part named "file"
            using var ms = new MemoryStream(Encoding.UTF8.GetBytes(content));
            using var fileContent = new StreamContent(ms);
            fileContent.Headers.ContentType = new MediaTypeHeaderValue("text/plain");

            using var form = new MultipartFormDataContent
            {
                { fileContent, "file", fileName }
            };

            // Adjust the route to match your proxy/controller:
            // You have MediaItemProxyController.cs; this example assumes an action like:
            // POST /api/MediaItemProxy/upload-text?eventId=123
            var resp = await client.PostAsync($"/api/MediaItemProxy/upload-text?eventId={eventId}", form, ct);
            resp.EnsureSuccessStatusCode();
        }
    }
}

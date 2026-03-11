using AiInCourtAssistant.Shared.Models;
using System.Net.Http.Headers;
using System.Net.Http.Json;

namespace AiInCourtAssistant.Client.Services
{
    public class EventService
    {
        private readonly HttpClient _http;
        private readonly LoginService _login;

        public EventService(HttpClient http, LoginService login)
        {
            _http = http;
            _login = login;
        }

        private void AddAuth(HttpRequestMessage req)
        {
            var tok = _login.GetToken();
            if (string.IsNullOrWhiteSpace(tok)) return;

            // tolerate either raw token or "Bearer xyz"
            var raw = tok.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase)
                ? tok.Substring("Bearer ".Length)
                : tok;

            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", raw);
        }

        public async Task<bool> UpdateEventAsync(EventUpdateDto update, CancellationToken ct = default)
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, "api/proxy/event/update")
            { Content = JsonContent.Create(update) };
            AddAuth(req);

            using var res = await _http.SendAsync(req, ct);
            return res.IsSuccessStatusCode;
        }

        public async Task<int> SaveBulkNotesAsync(IEnumerable<NoteUpdateDto> items, CancellationToken ct = default)
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, "api/proxy/event/bulk-notes")
            { Content = JsonContent.Create(items) };
            AddAuth(req);

            using var res = await _http.SendAsync(req, ct);
            if (!res.IsSuccessStatusCode) return 0;

            var ok = await res.Content.ReadFromJsonAsync<BulkNotesResult>(cancellationToken: ct);
            return ok?.saved ?? 0;
        }

        private sealed class BulkNotesResult { public int saved { get; set; } }
    }
}

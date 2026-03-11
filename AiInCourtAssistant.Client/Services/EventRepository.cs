using AiInCourtAssistant.Shared.Models;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace AiInCourtAssistant.Client.Services
{
    public class EventRepository
    {
        private readonly HttpClient _http;
        private readonly LoginService _login;

        public EventRepository(HttpClient http, LoginService login)
        {
            _http = http;
            _login = login;
        }

        public async Task<(int Total, List<AdvancedEventSearchResult> Items)> AdvancedSearchAsync(AdvancedEventSearch search)
        {
            var token = _login.GetToken();
            if (string.IsNullOrEmpty(token))
            {
                Console.WriteLine("[EventRepository] Missing token.");
                return (0, new List<AdvancedEventSearchResult>());
            }

            _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

            var response = await _http.PostAsJsonAsync("/api/proxy/event/search", search);
            var raw = await response.Content.ReadAsStringAsync();

            Console.WriteLine("=== RAW JSON from /proxy/event/search ===");
            Console.WriteLine(raw);

            if (!response.IsSuccessStatusCode)
            {
                Console.WriteLine($"[EventRepo] Search failed: {response.StatusCode} - {raw}");
                return (0, new List<AdvancedEventSearchResult>());
            }

            var root = JsonSerializer.Deserialize<AdvancedEventSearchResponse>(raw, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            });

            return(root?.TotalRecords ?? 0, root?.Items ?? new List<AdvancedEventSearchResult>());

        }


        public async Task UpdateEventAsync(EventUpdateDto update)
        {
            var token = _login.GetToken();
            if (string.IsNullOrEmpty(token))
            {
                Console.WriteLine("[EventRepository] Missing token.");
                return;
            }

            _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

            // ✅ Correct: go through your proxy and use POST
            var response = await _http.PostAsJsonAsync("api/proxy/event/update", update);

            if (!response.IsSuccessStatusCode)
            {
                var error = await response.Content.ReadAsStringAsync();
                Console.WriteLine($"[EventRepo] Update failed: {response.StatusCode} - {error}");
            }
        }
    }
}

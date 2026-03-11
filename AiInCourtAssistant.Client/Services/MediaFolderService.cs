using System.Net.Http.Headers;
using System.Text.Json;

namespace AiInCourtAssistant.Client.Services
{
    public class MediaFolderService
    {
        private readonly HttpClient _http;
        private readonly LoginService _login;

        public MediaFolderService(HttpClient http, LoginService login)
        {
            _http = http;
            _login = login;
        }

        private async Task AddAuthAsync()
        {
            var token = await _login.GetTokenAsync();
            _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
            if (!_http.DefaultRequestHeaders.Accept.Any(a => a.MediaType == "application/json"))
                _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        }

        // GET /api/proxy/mediaFolder/{folderId}/collections
        public async Task<JsonDocument> GetCollectionsByFolderIdAsync(int folderId)
        {
            await AddAuthAsync();
            using var resp = await _http.GetAsync($"api/proxy/mediaFolder/{folderId}/collections");
            var raw = await resp.Content.ReadAsStringAsync();
            resp.EnsureSuccessStatusCode();
            return JsonDocument.Parse(raw);
        }

        // GET /api/proxy/mediaFolder/case/{caseId}/collections  (resolves root folder first)
        public async Task<JsonDocument> GetRootCollectionsByCaseIdAsync(int caseId)
        {
            await AddAuthAsync();
            using var resp = await _http.GetAsync($"api/proxy/mediaFolder/case/{caseId}/collections");
            var raw = await resp.Content.ReadAsStringAsync();
            resp.EnsureSuccessStatusCode();
            return JsonDocument.Parse(raw);
        }

        // Find a subfolder by name (case-insensitive); returns mediaFolderID or null
        public static int? FindSubfolderId(JsonDocument collections, string subfolderName)
        {
            if (!collections.RootElement.TryGetProperty("subMediaFolders", out var subs))
                return null;

            if (subs.ValueKind == JsonValueKind.Array)
            {
                foreach (var f in subs.EnumerateArray())
                {
                    if (f.TryGetProperty("folderName", out var nameProp)
                        && string.Equals(nameProp.GetString(), subfolderName, StringComparison.OrdinalIgnoreCase)
                        && f.TryGetProperty("mediaFolderID", out var idProp)
                        && idProp.TryGetInt32(out var id))
                        return id;
                }
            }
            else if (subs.ValueKind == JsonValueKind.Object)
            {
                foreach (var f in subs.EnumerateObject().Select(p => p.Value))
                {
                    if (f.TryGetProperty("folderName", out var nameProp)
                        && string.Equals(nameProp.GetString(), subfolderName, StringComparison.OrdinalIgnoreCase)
                        && f.TryGetProperty("mediaFolderID", out var idProp)
                        && idProp.TryGetInt32(out var id))
                        return id;
                }
            }
            return null;
        }
    }
}

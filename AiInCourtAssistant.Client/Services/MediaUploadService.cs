using AiInCourtAssistant.Shared.Models;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;

namespace AiInCourtAssistant.Client.Services
{
    public class MediaUploadService
    {
        private readonly HttpClient _http;
        public MediaUploadService(HttpClient http) => _http = http;

        private static readonly JsonSerializerOptions JsonOpts = new()
        {
            PropertyNameCaseInsensitive = true
        };

        private static HttpRequestMessage Authed(HttpMethod method, string url, string token)
        {
            var req = new HttpRequestMessage(method, url);
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            return req;
        }

        private static void ThrowIfHtml(string body, string url)
        {
            if (string.IsNullOrWhiteSpace(body)) return;
            if (body.TrimStart().StartsWith("<", StringComparison.Ordinal))
                throw new InvalidOperationException(
                    $"Expected JSON from '{url}', but got HTML (SPA shell). Ensure the request path begins with '/api/proxy/' and the route exists on the server.");
        }

        /// <summary>
        /// Returns the 'Transcripts' collection for a case if it exists;
        /// otherwise tries to create it; finally falls back to the case folder.
        /// </summary>
        public async Task<MediaFolderInfo> EnsureTranscriptsFolderAsync(int caseId, string token)
        {
            var opts = JsonOpts;

            // a) Get the case folder (AUTHED)
            var caseUrl = $"/api/proxy/mediaFolder/case/{caseId}";
            var caseReq = Authed(HttpMethod.Get, caseUrl, token);
            var caseResp = await _http.SendAsync(caseReq);
            var caseRaw = await caseResp.Content.ReadAsStringAsync();
            ThrowIfHtml(caseRaw, caseUrl);
            caseResp.EnsureSuccessStatusCode();

            var caseFolder = JsonSerializer.Deserialize<MediaFolderInfo>(caseRaw, opts)
                            ?? throw new InvalidOperationException("Failed to deserialize case folder.");

            // b) Get collections (AUTHED)
            var colsUrl = $"/api/proxy/mediaFolder/case/{caseId}/collections";
            var colsReq = Authed(HttpMethod.Get, colsUrl, token);
            var colsResp = await _http.SendAsync(colsReq);
            var colsRaw = await colsResp.Content.ReadAsStringAsync();
            ThrowIfHtml(colsRaw, colsUrl);
            colsResp.EnsureSuccessStatusCode();

            // The proxy returns a folder-like shape where children live in SubMediaFolders
            var collectionsRoot = JsonSerializer.Deserialize<MediaFolderInfo>(colsRaw, opts);
            var transcripts = collectionsRoot?.SubMediaFolders?
                .FirstOrDefault(f =>
                    string.Equals((f.FolderName ?? f.Name)?.Trim(), "Transcripts",
                                  StringComparison.OrdinalIgnoreCase));
            if (transcripts != null) return transcripts;

            // c) Create "Transcripts" under the case folder (AUTHED)
            var createUrl = $"/api/proxy/mediaFolder/{caseFolder.MediaFolderID}/collections";
            var createReq = Authed(HttpMethod.Post, createUrl, token);
            createReq.Content = JsonContent.Create(new { name = "Transcripts" });

            var createResp = await _http.SendAsync(createReq);
            var createRaw = await createResp.Content.ReadAsStringAsync();
            ThrowIfHtml(createRaw, createUrl);

            if (createResp.IsSuccessStatusCode)
            {
                var created = JsonSerializer.Deserialize<MediaFolderInfo>(createRaw, opts);
                if (created != null) return created;
            }

            // Fallback: uploads will still work at the case root
            return caseFolder;
        }



        public async Task<UploadedMediaInfo> UploadTranscriptAsync(
            string transcriptText, string fileName, int caseId, int mediaFolderId, string token)
        {
            using var content = new MultipartFormDataContent();

            var transcriptStream = new MemoryStream(Encoding.UTF8.GetBytes(transcriptText));
            var fileContent = new StreamContent(transcriptStream);
            fileContent.Headers.ContentType = new MediaTypeHeaderValue("text/plain");

            content.Add(fileContent, "file", fileName);
            content.Add(new StringContent(caseId.ToString()), "caseID");
            content.Add(new StringContent(mediaFolderId.ToString()), "mediaFolderID");
            content.Add(new StringContent("Transcript"), "mediaType");

            var url = "/api/proxy/mediaFolder/upload?prefer=json";
            var req = Authed(HttpMethod.Post, url, token);
            req.Content = content;

            var resp = await _http.SendAsync(req);
            resp.EnsureSuccessStatusCode();

            return await resp.Content.ReadFromJsonAsync<UploadedMediaInfo>()
                   ?? new UploadedMediaInfo();
        }

        public async Task<UploadedMediaInfo?> UploadAudioAsync(
            Stream audioStream, string fileName, int caseId, int mediaFolderId, string token)
        {
            using var content = new MultipartFormDataContent();

            var fileContent = new StreamContent(audioStream);
            fileContent.Headers.ContentType = new MediaTypeHeaderValue("audio/mpeg"); // safe default

            content.Add(fileContent, "file", fileName);
            content.Add(new StringContent(caseId.ToString()), "caseID");
            content.Add(new StringContent(mediaFolderId.ToString()), "mediaFolderID");
            content.Add(new StringContent("Audio"), "mediaType");

            var url = "/api/proxy/mediaFolder/upload?prefer=json";
            var req = Authed(HttpMethod.Post, url, token);
            req.Content = content;

            var resp = await _http.SendAsync(req);
            resp.EnsureSuccessStatusCode();

            return await resp.Content.ReadFromJsonAsync<UploadedMediaInfo>();
        }
    }
}

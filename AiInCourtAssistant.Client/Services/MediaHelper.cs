using System.Net.Http.Headers;
using System.Net.Http.Json;
using AiInCourtAssistant.Shared.Models;

public static class MediaHelper
{
    public static async Task<int?> GetOrCreateTranscriptFolderId(HttpClient http, string token, int caseId, int parentMediaFolderId)
    {
        // 1. Check for existing folders
        var getRequest = new HttpRequestMessage(HttpMethod.Get, $"/api/MediaFolder/{parentMediaFolderId}/collections");
        getRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        var response = await http.SendAsync(getRequest);
        response.EnsureSuccessStatusCode();

        var folders = await response.Content.ReadFromJsonAsync<List<MediaFolderInfo>>();
        var existing = folders?.FirstOrDefault(f => f.FolderName?.Trim().ToLower() == "transcripts");

        if (existing != null)
        {
            Console.WriteLine($"📁 Found existing Transcripts folder: {existing.MediaFolderID}");
            return existing.MediaFolderID;
        }

        // 2. Create if not found
        var createPayload = new
        {
            parentMediaFolderID = parentMediaFolderId,
            caseID = caseId,
            folderName = "Transcripts",
            fullPath = "/Transcripts",
            source = "Case",
            sourceID = caseId.ToString(),
            isActive = true,
            isDeleted = false,
            createdByDisplayName = "test",
            createdBySystemUserID = 2
        };

        var createRequest = new HttpRequestMessage(HttpMethod.Post, "/api/MediaFolder")
        {
            Content = JsonContent.Create(createPayload)
        };
        createRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var createResponse = await http.SendAsync(createRequest);
        createResponse.EnsureSuccessStatusCode();

        var result = await createResponse.Content.ReadFromJsonAsync<MediaFolderInfo>();
        Console.WriteLine($"✅ Created new Transcripts folder: {result?.MediaFolderID}");

        return result?.MediaFolderID;
    }
}

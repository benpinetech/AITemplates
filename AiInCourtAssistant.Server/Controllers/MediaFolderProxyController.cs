using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/proxy/mediaFolder")]
[Authorize(Policy = "AllowPineTokenPassthrough")] // ← use passthrough auth for Pine token
public sealed class MediaFolderProxyController : ControllerBase
{
    private readonly HttpClient _http;

    public MediaFolderProxyController(IHttpClientFactory f)
        => _http = f.CreateClient("PineProxy"); // BaseAddress + ForwardBearerHandler

    // GET /api/proxy/mediaFolder/case/{caseId}
    // Returns { mediaFolderID: <rootId> }
    [HttpGet("case/{caseId:int}")]
    public async Task<IActionResult> GetCaseRootFolder(int caseId)
    {
        var resp = await _http.GetAsync($"api/case/{caseId}", HttpCompletionOption.ResponseHeadersRead);
        var text = await resp.Content.ReadAsStringAsync();

        if (!resp.IsSuccessStatusCode)
            return StatusCode((int)resp.StatusCode, text);

        int rootId = 0;
        try
        {
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            if (root.TryGetProperty("rootMediaFolderID", out var p) && p.TryGetInt32(out var id)) rootId = id;
            else if (root.TryGetProperty("RootMediaFolderID", out p) && p.TryGetInt32(out id)) rootId = id;
        }
        catch
        {
            // ignore parse errors; return 0
        }

        return Ok(new { mediaFolderID = rootId });
    }

    // GET /api/proxy/mediaFolder/{mediaFolderId}/collections
    [HttpGet("{mediaFolderId:int}/collections")]
    public async Task<IActionResult> GetCollections(int mediaFolderId)
    {
        var resp = await _http.GetAsync($"api/mediaFolder/{mediaFolderId}/collections", HttpCompletionOption.ResponseHeadersRead);
        var text = await resp.Content.ReadAsStringAsync();
        return StatusCode((int)resp.StatusCode, text);
    }

    // POST /api/proxy/mediaFolder  (create sub-folder)
    [HttpPost]
    public async Task<IActionResult> Create([FromBody] JsonElement body)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/mediaFolder")
        {
            Content = new StringContent(body.GetRawText(), Encoding.UTF8, "application/json")
        };
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));

        var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead);
        var text = await resp.Content.ReadAsStringAsync();
        return StatusCode((int)resp.StatusCode, text);
    }
}

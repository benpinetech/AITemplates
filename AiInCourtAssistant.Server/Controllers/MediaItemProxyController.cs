using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/proxy")]
[Authorize(Policy = "AllowPineTokenPassthrough")] // ← use passthrough auth for Pine token
public sealed class MediaItemProxyController : ControllerBase
{
    private readonly HttpClient _http;

    public MediaItemProxyController(IHttpClientFactory factory)
        => _http = factory.CreateClient("PineProxy"); // BaseAddress + ForwardBearerHandler

    // POST /api/proxy/mediaItem
    [HttpPost("mediaItem")]
    public async Task<IActionResult> Create([FromBody] JsonElement body)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/MediaItem")
        {
            Content = new StringContent(body.GetRawText(), Encoding.UTF8, "application/json")
        };
        return await Proxy(await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead));
    }

    // GET /api/proxy/mediaItem/{mediaItemId}
    [HttpGet("mediaItem/{mediaItemId:int}")]
    public async Task<IActionResult> Get(int mediaItemId)
        => await Proxy(await _http.GetAsync($"api/MediaItem/{mediaItemId}", HttpCompletionOption.ResponseHeadersRead));

    // GET /api/proxy/mediaItem/{id}/download  ->  GET /api/MediaItem/{id}/download (follows presigned redirect)
    [HttpGet("mediaItem/{mediaItemId:int}/download")]
    [ResponseCache(NoStore = true, Location = ResponseCacheLocation.None)]
    public async Task<IActionResult> Download(int mediaItemId, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, $"api/MediaItem/{mediaItemId}/download");
        using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);

        // If Pine returns a 30x to a presigned S3 URL, redirect the caller
        if ((int)resp.StatusCode is (int)HttpStatusCode.MovedPermanently
            or (int)HttpStatusCode.Found
            or (int)HttpStatusCode.SeeOther
            or (int)HttpStatusCode.TemporaryRedirect
            or (int)HttpStatusCode.PermanentRedirect)
        {
            var loc = resp.Headers.Location?.ToString();
            if (string.IsNullOrEmpty(loc)) return StatusCode((int)resp.StatusCode);
            return Redirect(loc);
        }

        // Bubble up errors as text
        if (!resp.IsSuccessStatusCode)
        {
            var textErr = await resp.Content.ReadAsStringAsync(ct);
            return StatusCode((int)resp.StatusCode, textErr);
        }

        // Stream bytes straight through
        var contentType = resp.Content.Headers.ContentType?.ToString() ?? "application/octet-stream";
        var stream = await resp.Content.ReadAsStreamAsync(ct);
        return File(stream, contentType);
    }

    // POST /api/proxy/mediaItem/{mediaItemId}/complete
    [HttpPost("mediaItem/{mediaItemId:int}/complete")]
    public async Task<IActionResult> Complete(int mediaItemId, [FromBody] JsonElement body)
    {
        // Try empty PUT first
        using (var tryEmpty = new HttpRequestMessage(HttpMethod.Put, $"api/MediaItem/{mediaItemId}/finalizeUpload"))
        {
            using var respEmpty = await _http.SendAsync(tryEmpty, HttpCompletionOption.ResponseHeadersRead);
            if (respEmpty.IsSuccessStatusCode) return await Proxy(respEmpty);
        }

        // Fallback: send provided JSON body
        var json = body.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null ? "{}" : body.GetRawText();
        using var tryWithBody = new HttpRequestMessage(HttpMethod.Put, $"api/MediaItem/{mediaItemId}/finalizeUpload")
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json")
        };
        return await Proxy(await _http.SendAsync(tryWithBody, HttpCompletionOption.ResponseHeadersRead));
    }

    // ---------- helpers ----------
    private static async Task<IActionResult> Proxy(HttpResponseMessage upstream)
    {
        var content = await upstream.Content.ReadAsStringAsync();
        var ct = upstream.Content.Headers.ContentType?.ToString() ?? "application/json";
        return new ContentResult
        {
            StatusCode = (int)upstream.StatusCode,
            Content = content,
            ContentType = ct
        };
    }
}

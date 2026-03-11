using Microsoft.AspNetCore.Components.Forms;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;

namespace AiInCourtAssistant.Client.Services;

public sealed class SessionTranscribeClient
{
    private readonly HttpClient _http;
    private readonly LoginService _login;
    private const long MaxUploadBytes = 1024L * 1024L * 500L; // 500 MB

    public SessionTranscribeClient(HttpClient http, LoginService login)
    {
        _http = http;
        _login = login;
    }

    // === DTOs (client-facing shapes that mirror server responses) ============

    public sealed record StartSessionResponse(Guid SessionId);

    // Suggestions coming back from the preview API
    public sealed record SuggestionDto(
        string Title,
        string? Detail = null,
        string? Body = null,
        double Confidence = 0d
    );

    public sealed record SessionPreviewItem(
        int EventId,
        string? NotesSummary,
        IReadOnlyList<SuggestionDto> Suggestions,
        string? TranscriptExcerpt,
        double Confidence
    );

    public sealed record SessionPreviewResponse(Guid SessionId, IReadOnlyList<SessionPreviewItem> Items);

    // Context (what the worker needs to split/attach chunks per event)
    public sealed record DocketHintDto(int EventId, int CaseId, string? CaseNumber, string? CaseName);
    public sealed record DocketContextDto(int? ActiveEventId, List<DocketHintDto> Rows);

    // === Context =============================================================

    /// <summary>Post docket context (active event + all rows) for the given session.</summary>
    public async Task PushContextAsync(Guid sessionId, int? activeEventId, IEnumerable<DocketHintDto> rows, CancellationToken ct = default)
    {
        var payload = new DocketContextDto(activeEventId, rows.ToList());
        using var req = new HttpRequestMessage(HttpMethod.Post, $"/api/session-transcribe/{sessionId}/context")
        { Content = JsonContent.Create(payload) };
        await AddBearerAsync(req);
        var res = await _http.SendAsync(req, ct);
        res.EnsureSuccessStatusCode();
    }

    /// <summary>Overload accepting a DocketContextDto directly (convenience).</summary>
    public Task PostContextAsync(Guid sessionId, DocketContextDto ctx, CancellationToken ct = default)
        => PushContextAsync(sessionId, ctx.ActiveEventId, ctx.Rows, ct);

    // === Upload ==============================================================

    /// <summary>
    /// Upload without explicit targets (kept for compatibility).
    /// </summary>
    public async Task<Guid> UploadAsync(Guid tenantId, string? courtroom, DateOnly docketDate, IBrowserFile file, CancellationToken ct = default)
    {
        using var form = new MultipartFormDataContent("Upload----" + Guid.NewGuid());
        await using var stream = file.OpenReadStream(MaxUploadBytes, ct);
        var fileContent = new StreamContent(stream);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue(
            string.IsNullOrWhiteSpace(file.ContentType) ? "application/octet-stream" : file.ContentType);
        form.Add(fileContent, "file", file.Name); // must match server parameter name

        var url = $"/api/session-transcribe/upload" +
                  $"?tenantId={tenantId}" +
                  $"&courtroom={Uri.EscapeDataString(courtroom ?? string.Empty)}" +
                  $"&docketDate={docketDate:yyyy-MM-dd}";

        using var req = new HttpRequestMessage(HttpMethod.Post, url) { Content = form };
        await AddBearerAsync(req);

        var res = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        if (res.StatusCode == HttpStatusCode.Unauthorized)
            throw new InvalidOperationException("Unauthorized (401): token missing/expired for upload.");
        res.EnsureSuccessStatusCode();

        var payload = await res.Content.ReadFromJsonAsync<StartSessionResponse>(cancellationToken: ct)
                      ?? throw new InvalidOperationException("Upload succeeded but server did not return a sessionId.");
        if (payload.SessionId == Guid.Empty)
            throw new InvalidOperationException("Upload returned an empty sessionId.");
        return payload.SessionId;
    }

    /// <summary>
    /// Upload with explicit eventIds/caseIds so the worker can fan-out immediately.
    /// </summary>
    public async Task<Guid> UploadAsyncWithTargets(
        Guid tenantId,
        string? courtroom,
        DateOnly docketDate,
        IBrowserFile file,
        int[]? eventIds,
        int[]? caseIds,
        CancellationToken ct = default)
    {
        using var form = new MultipartFormDataContent("Upload----" + Guid.NewGuid());
        await using var stream = file.OpenReadStream(MaxUploadBytes, ct);
        var fileContent = new StreamContent(stream);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue(
            string.IsNullOrWhiteSpace(file.ContentType) ? "application/octet-stream" : file.ContentType);
        form.Add(fileContent, "file", file.Name);

        var url = $"/api/session-transcribe/upload" +
                  $"?tenantId={tenantId}" +
                  $"&courtroom={Uri.EscapeDataString(courtroom ?? string.Empty)}" +
                  $"&docketDate={docketDate:yyyy-MM-dd}";

        if (eventIds is { Length: > 0 })
        {
            foreach (var id in eventIds.Where(x => x > 0).Distinct())
                url += $"&eventIds={id}";
        }
        if (caseIds is { Length: > 0 })
        {
            foreach (var cid in caseIds.Select(x => x < 0 ? 0 : x))
                url += $"&caseIds={cid}";
        }

        using var req = new HttpRequestMessage(HttpMethod.Post, url) { Content = form };
        await AddBearerAsync(req);

        var res = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        if (res.StatusCode == HttpStatusCode.Unauthorized)
            throw new InvalidOperationException("Unauthorized (401): token missing/expired for upload.");
        res.EnsureSuccessStatusCode();

        var payload = await res.Content.ReadFromJsonAsync<StartSessionResponse>(cancellationToken: ct)
                      ?? throw new InvalidOperationException("Upload succeeded but server did not return a sessionId.");
        if (payload.SessionId == Guid.Empty)
            throw new InvalidOperationException("Upload returned an empty sessionId.");
        return payload.SessionId;
    }

    // === Preview / SaveAll ===================================================

    public async Task<SessionPreviewResponse?> GetPreviewAsync(Guid sessionId, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, $"/api/session-transcribe/{sessionId}/preview");
        await AddBearerAsync(req);
        var res = await _http.SendAsync(req, ct);
        if (res.StatusCode == HttpStatusCode.Unauthorized) return null;
        res.EnsureSuccessStatusCode();
        return await res.Content.ReadFromJsonAsync<SessionPreviewResponse>(cancellationToken: ct);
    }

    public async Task<int> SaveAllAsync(Guid sessionId, Guid tenantId, int defaultEventId = 0, int defaultCaseId = 0, CancellationToken ct = default)
    {
        var url = $"/api/session-transcribe/{sessionId}/save-all?tenantId={tenantId}" +
                  $"&defaultEventId={defaultEventId}&defaultCaseId={defaultCaseId}";
        using var req = new HttpRequestMessage(HttpMethod.Post, url);
        await AddBearerAsync(req);
        var res = await _http.SendAsync(req, ct);
        res.EnsureSuccessStatusCode();

        var json = await res.Content.ReadFromJsonAsync<Dictionary<string, object>>(cancellationToken: ct);
        return json is not null && json.TryGetValue("updated", out var v) && int.TryParse(v?.ToString(), out var n) ? n : 0;
    }

    // === Helpers =============================================================

    private async Task AddBearerAsync(HttpRequestMessage req)
    {
        var token = await _login.GetTokenAsync();
        if (!string.IsNullOrWhiteSpace(token))
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }
}

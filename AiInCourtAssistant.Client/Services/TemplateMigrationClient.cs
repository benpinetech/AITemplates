using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using AiInCourtAssistant.Shared.Models;

namespace AiInCourtAssistant.Client.Services;

public sealed class TemplateMigrationClient
{
    private readonly HttpClient _http;
    private readonly LoginService _login;

    public TemplateMigrationClient(HttpClient http, LoginService login)
    {
        _http = http;
        _login = login;
    }

    private async Task AddAuthAsync(HttpRequestMessage req, CancellationToken ct)
    {
        var tok = _login.GetToken();
        if (string.IsNullOrWhiteSpace(tok))
            tok = await _login.GetTokenAsync();
        if (string.IsNullOrWhiteSpace(tok)) return;
        var raw = tok.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase)
            ? tok.Substring("Bearer ".Length)
            : tok;
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", raw);
    }

    public async Task<MigrateResultDto?> MigrateAsync(string? legacyText, string? rtfContent, bool migrateFullRtf = true, CancellationToken ct = default)
    {
        var request = new MigrateRequestDto { LegacyText = legacyText, RtfContent = rtfContent, MigrateFullRtf = migrateFullRtf };
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/migration/migrate");
        await AddAuthAsync(req, ct);
        req.Content = JsonContent.Create(request);

        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            return null;
        var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
        return await res.Content.ReadFromJsonAsync<MigrateResultDto>(options, ct);
    }

    public async Task<(bool Ok, string? Error)> SaveTemplateAsync(object templateBody, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/proxy/template");
        await AddAuthAsync(req, ct);
        req.Content = JsonContent.Create(templateBody);

        using var res = await _http.SendAsync(req, ct);
        if (res.IsSuccessStatusCode)
            return (true, null);
        var body = await res.Content.ReadAsStringAsync(ct);
        return (false, $"{(int)res.StatusCode}: {body}");
    }

    public async Task<(string? GeneratedText, string? Error)> GeneratePineAsync(string description, bool useLegacyAsExample, string? legacyContent, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/migration/generate");
        await AddAuthAsync(req, ct);
        req.Content = JsonContent.Create(new { description, useLegacyAsExample, legacyContent });

        using var res = await _http.SendAsync(req, ct);
        var json = await res.Content.ReadAsStringAsync(ct);
        if (!res.IsSuccessStatusCode)
        {
            try
            {
                var err = JsonSerializer.Deserialize<JsonElement>(json);
                if (err.TryGetProperty("error", out var errProp))
                    return (null, errProp.GetString());
            }
            catch { }
            return (null, $"{(int)res.StatusCode}: {(json.Length > 200 ? json.Substring(0, 200) + "…" : json)}");
        }
        try
        {
            var doc = JsonDocument.Parse(json);
            if (doc.RootElement.TryGetProperty("generatedText", out var textProp))
                return (textProp.GetString(), null);
        }
        catch { }
        return (null, "Invalid response");
    }

    public async Task<(string? SuggestedText, IReadOnlyList<string>? Changes, string? Error)> SuggestFillpointsAsync(string content, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/migration/suggest-fillpoints");
        await AddAuthAsync(req, ct);
        req.Content = JsonContent.Create(new { content });

        using var res = await _http.SendAsync(req, ct);
        var json = await res.Content.ReadAsStringAsync(ct);
        if (!res.IsSuccessStatusCode)
        {
            try
            {
                var err = JsonSerializer.Deserialize<JsonElement>(json);
                if (err.TryGetProperty("error", out var errProp))
                    return (null, null, errProp.GetString());
            }
            catch { }
            return (null, null, $"{(int)res.StatusCode}: {(json.Length > 200 ? json.Substring(0, 200) + "…" : json)}");
        }
        try
        {
            var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            var suggestedText = root.TryGetProperty("suggestedText", out var st) ? st.GetString() : null;
            var changes = new List<string>();
            if (root.TryGetProperty("changes", out var arr))
                foreach (var item in arr.EnumerateArray())
                    changes.Add(item.GetString() ?? "");
            return (suggestedText, changes, null);
        }
        catch
        {
            return (null, null, "Invalid response");
        }
    }

    public async Task<string?> RtfToHtmlAsync(string content, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, "api/migration/rtf-to-html");
        await AddAuthAsync(req, ct);
        req.Content = JsonContent.Create(new { content });

        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode) return null;
        var json = await res.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: ct);
        if (json.TryGetProperty("html", out var htmlProp))
            return htmlProp.GetString();
        return null;
    }

    public async Task<(bool Ok, byte[]? Bytes, string? ContentType, string? Error)> PreviewAsync(int caseId, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, $"api/proxy/caseDocument/preview/{caseId}");
        await AddAuthAsync(req, ct);

        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
        {
            var body = await res.Content.ReadAsStringAsync(ct);
            return (false, null, null, $"{(int)res.StatusCode}: {body}");
        }
        var contentType = res.Content.Headers.ContentType?.MediaType ?? "application/octet-stream";
        var bytes = await res.Content.ReadAsByteArrayAsync(ct);
        return (true, bytes, contentType, null);
    }
}

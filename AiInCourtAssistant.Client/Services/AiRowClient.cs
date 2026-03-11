using System.Net.Http.Json;
using AiInCourtAssistant.Shared.Models;

namespace AiInCourtAssistant.Client.Services;

public sealed class AiRowClient
{
    private readonly HttpClient _http;
    private readonly LoginService _login;

    public AiRowClient(HttpClient http, LoginService login)
    {
        _http = http;
        _login = login;
    }

    public async Task<AiRowResponse?> SummarizeAsync(
        int eventId,
        string transcript,
        string? caseNumber,
        string? defendant,
        CancellationToken ct = default)
    {
        var req = new AiRowRequest(eventId, transcript, caseNumber, defendant);

        using var msg = new HttpRequestMessage(HttpMethod.Post, "api/ai/row/summarize")
        { Content = JsonContent.Create(req) };
        msg.Headers.Authorization =
            new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", _login.GetToken());

        var resp = await _http.SendAsync(msg, ct);
        if (!resp.IsSuccessStatusCode) return null;

        return await resp.Content.ReadFromJsonAsync<AiRowResponse>(cancellationToken: ct);
    }
}

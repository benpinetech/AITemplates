using System;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;

namespace AiInCourtAssistant.Client.Services
{
    public sealed class CaseNoteClient : ICaseNoteClient
    {
        private readonly HttpClient _http;
        private readonly LoginService _login;

        public CaseNoteClient(HttpClient http, LoginService login)
        {
            _http = http;
            _login = login;
        }

        private void AddAuth(HttpRequestMessage req)
        {
            var tok = _login.GetToken();
            if (string.IsNullOrWhiteSpace(tok)) return;

            var raw = tok.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase)
                ? tok.Substring("Bearer ".Length)
                : tok;

            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", raw);
        }

        // -------- helpers for reading ----------
        private static string? ExtractString(JsonElement e)
        {
            if (e.ValueKind == JsonValueKind.String) return e.GetString();

            if (e.ValueKind == JsonValueKind.Object)
            {
                string? TryKey(params string[] keys)
                {
                    foreach (var k in keys)
                        if (e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String)
                            return v.GetString();
                    return null;
                }
                return TryKey("text", "Text", "noteText", "NoteText", "note", "Note", "body", "Body");
            }

            if (e.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in e.EnumerateArray())
                {
                    var s = ExtractString(item);
                    if (!string.IsNullOrWhiteSpace(s)) return s;
                }
            }
            return null;
        }

        public async Task<string?> GetLatestAsync(int caseId, CancellationToken ct = default)
        {
            if (caseId <= 0) return null;

            // 1) Prefer normalized /latest
            try
            {
                var ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                using var req = new HttpRequestMessage(HttpMethod.Get,
                    $"/api/proxy/case-note/latest?caseId={caseId}&type=NFC&_={ts}");
                AddAuth(req);

                using var res = await _http.SendAsync(req, ct);
                var raw = await res.Content.ReadAsStringAsync(ct);

                if (res.IsSuccessStatusCode)
                {
                    try
                    {
                        using var doc = JsonDocument.Parse(raw);
                        var s = ExtractString(doc.RootElement);
                        if (!string.IsNullOrWhiteSpace(s)) return s!.Trim();
                    }
                    catch
                    {
                        if (!string.IsNullOrWhiteSpace(raw))
                            return raw.Trim('"', ' ', '\n', '\r', '\t');
                    }
                }
            }
            catch { /* fall through */ }

            // 2) Fallback: list newest first
            try
            {
                var ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                using var req = new HttpRequestMessage(HttpMethod.Get,
                    $"/api/proxy/case-note?caseId={caseId}&type=NFC&take=50&order=desc&_={ts}");
                AddAuth(req);

                using var res = await _http.SendAsync(req, ct);
                if (!res.IsSuccessStatusCode) return null;

                var root = await res.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: ct);

                IEnumerable<JsonElement> items =
                    root.ValueKind == JsonValueKind.Array ? root.EnumerateArray()
                    : (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
                        ? arr.EnumerateArray()
                        : Array.Empty<JsonElement>();

                string? best = null;
                DateTime bestDt = DateTime.MinValue;
                int bestId = 0;

                foreach (var it in items)
                {
                    var s = ExtractString(it);
                    if (string.IsNullOrWhiteSpace(s)) continue;

                    DateTime dt = DateTime.MinValue;
                    if (it.TryGetProperty("dateTaken", out var d) && d.ValueKind == JsonValueKind.String)
                        DateTime.TryParse(d.GetString(), out dt);

                    int id = 0;
                    if (it.TryGetProperty("caseNoteID", out var cn) && cn.TryGetInt32(out var t))
                        id = t;

                    if (dt > bestDt || (dt == bestDt && id > bestId))
                    {
                        best = s.Trim();
                        bestDt = dt;
                        bestId = id;
                    }
                }

                return best;
            }
            catch { }

            return null;
        }

        public async Task<bool> SaveNfcAsync(int caseId, string text, CancellationToken ct = default)
        {
            if (caseId <= 0 || string.IsNullOrWhiteSpace(text)) return false;

            var payload = new
            {
                caseID = caseId,
                type = "NFC",
                text = text.Trim(),
                isActive = true,
                createdBySystemUserID = 0,   // <-- ensure these are INTS
                updatedBySystemUserID = 0    // <-- not strings
            };

            using var req = new HttpRequestMessage(HttpMethod.Post, "/api/proxy/case-note")
            {
                Content = JsonContent.Create(payload,
                    options: new System.Text.Json.JsonSerializerOptions
                    {
                        PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase
                    })
            };
            AddAuth(req);

            using var res = await _http.SendAsync(req, ct);
            return res.IsSuccessStatusCode;
        }
    }
}

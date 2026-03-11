using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Net.Http.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using AiInCourtAssistant.Client.Services;
using Microsoft.AspNetCore.Components.Forms;
using System.Net.Http.Headers;
using System.Text.Json;

namespace AiInCourtAssistant.Client.Pages
{
    // NOTE: This is a partial; the base class (ComponentBase, IAsyncDisposable) lives in the other partials.
    public partial class AdvancedEventSearchPage
    {
        // --- local state for the upload/preview flow ---
        private Guid? _uploadSessionId;
        private CancellationTokenSource? _previewPollCts;
        private bool _savingAll;

        // Fired by <InputFile OnChange="OnSessionFileInput" />
        private async Task OnSessionFileInput(InputFileChangeEventArgs e)
        {
            if (e?.File is null) return;

            // Cancel any previous session/poll
            if (_uploadSessionId is not null)
            {
                _previewPollCts?.Cancel();
                _previewPollCts = null;
            }

            _uploadUiStep = 1;  // "Uploading…"
            await InvokeAsync(StateHasChanged);

            // Build optional routing targets from selected rows
            var selected = _searchResultList.Where(r => r.IsSelected || _selected.Contains(r.EventID)).ToList();
            var eventIds = selected.Select(r => r.EventID).Where(id => id > 0).Distinct().ToArray();
            var caseIds = selected.Select(r => r.CaseID).Where(id => id > 0).Distinct().ToArray();

            // Courtroom / docketDate fallbacks
            var courtroom = string.IsNullOrWhiteSpace(CurrentCourtroom) ? "Courtroom B" : CurrentCourtroom!;
            var docketDate = CurrentDocketDate ?? DateOnly.FromDateTime(DateTime.Today);
            var tenantId = Guid.Empty; // (pass real tenant when needed)

            try
            {
                // Prefer “with targets” if we have rows selected
                Guid sessionId;
                if (eventIds.Length > 0 || caseIds.Length > 0)
                {
                    sessionId = await SessionApi.UploadAsyncWithTargets(
                        tenantId, courtroom, docketDate, e.File, eventIds, caseIds);
                }
                else
                {
                    sessionId = await SessionApi.UploadAsync(tenantId, courtroom, docketDate, e.File);
                }

                _uploadSessionId = sessionId;
                _lastUploadSessionId = sessionId;          // used elsewhere in the page
                _activeClientId = sessionId.ToString("N"); // used by live-listen.js hub
                _uploadUiStep = 2;                         // "Processing…"

                // reset per-row preview cache so we don't carry old suggestion lists forward
                _sessionPreview.Clear();

                await InvokeAsync(StateHasChanged);

                // Push docket context so the worker can align names/numbers
                await PushDocketContextAsync(sessionId);

                // Begin polling preview
                _previewPollCts?.Cancel();
                _previewPollCts = new CancellationTokenSource();
                _ = PollPreviewLoopAsync(sessionId, _previewPollCts.Token);
            }
            catch
            {
                // keep UI responsive even on failure
                _uploadUiStep = 0;
                await InvokeAsync(StateHasChanged);
                throw;
            }
        }

        private async Task PushDocketContextAsync(Guid sessionId)
        {
            // Map your rows → DocketHintDto
            var rows = _searchResultList
                .Select(r =>
                {
                    // Try to find a case number; fall back to CaseName
                    var caseNumber =
     (r.GetType().GetProperty("CaseNumber")?.GetValue(r) ??
      r.GetType().GetProperty("DocketNumber")?.GetValue(r) ??
      r.GetType().GetProperty("CaseNo")?.GetValue(r)
     )?.ToString();
                    if (string.IsNullOrWhiteSpace(caseNumber) && !string.IsNullOrWhiteSpace(r.CaseName))
                    {
                        // Extract docket-like token: “19-TC-207305” or “19TC207305”
                        var tokens = r.CaseName.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                        caseNumber = tokens.Length > 0 ? tokens[0] : null;
                    }

                    return new SessionTranscribeClient.DocketHintDto(
                        EventId: r.EventID,
                        CaseId: r.CaseID,
                        CaseNumber: caseNumber,
                        CaseName: r.CaseName
                    );
                })
                .ToList();

            var active = _activeEventId; // nullable is fine for the DTO

            await SessionApi.PushContextAsync(
                sessionId,
                active,
                rows
            );
        }

        // Polls /preview until cancelled; updates UI caches per row and auto-expands rows
        private async Task PollPreviewLoopAsync(Guid sessionId, CancellationToken ct)
        {
            var delayMs = 1000;
            string? lastFp = null;
            var lastChange = DateTimeOffset.UtcNow;
            var seenAny = false;

            while (!ct.IsCancellationRequested)
            {
                try
                {
                    var preview = await SessionApi.GetPreviewAsync(sessionId, ct);
                    if (preview is not null)
                    {
                        ApplyPreviewToUi(preview);

                        // consider it "ready" as soon as we see any items
                        if (preview.Items != null && preview.Items.Count > 0)
                        {
                            seenAny = true;
                            // make rows open as soon as we actually have preview content
                            ExpandAllRows();
                        }

                        var fp = Fingerprint(preview);
                        if (fp != lastFp) { lastFp = fp; lastChange = DateTimeOffset.UtcNow; }

                        _uploadUiStep = seenAny ? 3 : 2;   // 3 = Ready, 2 = Processing
                        await InvokeAsync(StateHasChanged);

                        if (seenAny && DateTimeOffset.UtcNow - lastChange > TimeSpan.FromSeconds(3))
                        {
                            // show "Ready" briefly, then hide the banner
                            _uploadUiStep = 3; // Ready
                            await InvokeAsync(StateHasChanged);
                            try { await Task.Delay(1200, ct); } catch { /* ignore */ }

                            _uploadUiStep = 0; // hide
                            await InvokeAsync(StateHasChanged);
                            return;
                        }
                    }

                    await Task.Delay(delayMs, ct);
                    delayMs = Math.Min(delayMs + 500, 3000);
                }
                catch (TaskCanceledException) { break; }
                catch (Exception ex)
                {
                    Console.WriteLine($"[preview poll] {ex.Message}");
                    await Task.Delay(1500, ct);
                }
            }
        }

        // Robust mapper for suggestions (rollback-style: don’t drop on missing titles)
        private static List<SuggestionItem> MapSuggestionsRobust(
            IEnumerable<SessionTranscribeClient.SuggestionDto>? src)
        {
            var list = new List<SuggestionItem>();
            if (src == null) return list;

            int i = 1;
            foreach (var s in src)
            {
                // prefer Title; fall back to Detail, then Body, then numbered placeholder
                var title = (s.Title ?? s.Detail ?? s.Body)?.Trim();
                if (string.IsNullOrWhiteSpace(title))
                    title = $"Suggestion {i}";

                var detail = (s.Detail ?? s.Body)?.Trim();

                // if title is very short but detail is meaningful, promote detail
                if (!string.IsNullOrWhiteSpace(detail) && title.Length < 6)
                    title = detail.Length > 80 ? detail[..80] + "…" : detail;

                list.Add(new SuggestionItem { Title = title, Detail = detail });
                i++;
            }
            return list;
        }

        // Heuristic fallbacks when the service doesn't return *all* suggestions we want
        private static List<SuggestionItem> GenerateFallbackSuggestions(string? summary, string? transcript)
        {
            string src = (summary ?? string.Empty) + " " + (transcript ?? string.Empty);
            string txt = src.ToLowerInvariant();
            var list = new List<SuggestionItem>();

            bool has(params string[] needles) => needles.Any(n => txt.Contains(n));

            // ---- Status cues (as before) ----
            if (has("continue", "continued", "continuance", "review scheduled", "reset", "diversion review", "continued for"))
                list.Add(new SuggestionItem { Title = "Mark status = CONT — Continuance language detected", Detail = "Continuance/review language detected in text." });

            if (has("dismissed", "case dismissed", "motion granted", "completed", "status is completed", "disposed"))
                list.Add(new SuggestionItem { Title = "Mark status = COMP — Disposition complete", Detail = "Dismissal/completion language detected." });

            if (has("failed to appear", "fta", "no appearance"))
                list.Add(new SuggestionItem { Title = "Mark status = FTA — Nonappearance detected", Detail = "FTA / nonappearance language detected." });

            if (has("vacated", "cancelled", "canceled"))
                list.Add(new SuggestionItem { Title = "Mark status = CANC — Cancellation detected", Detail = "Vacate/cancel language detected." });

            // ---- Next date/time detection ----
            var dateRx = new System.Text.RegularExpressions.Regex(
                @"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s*\d{2,4})|(?:\b\d{1,2}/\d{1,2}/\d{2,4}\b)",
                System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            var timeRx = new System.Text.RegularExpressions.Regex(
                @"\b\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)\b",
                System.Text.RegularExpressions.RegexOptions.IgnoreCase);

            string pickFrom = src;
            var nextPhrases = new[] { "review set for", "set for", "scheduled for", "next date", "continued to", "due on", "return on" };
            foreach (var phrase in nextPhrases)
            {
                var i = txt.IndexOf(phrase, StringComparison.Ordinal);
                if (i >= 0)
                {
                    pickFrom = src.Substring(i, Math.Min(200, src.Length - i));
                    break;
                }
            }

            var dateMatch = dateRx.Match(pickFrom);
            if (!dateMatch.Success) dateMatch = dateRx.Match(src);
            if (dateMatch.Success)
            {
                var timeMatch = timeRx.Match(pickFrom);
                if (!timeMatch.Success) timeMatch = timeRx.Match(src);

                var when = dateMatch.Value + (timeMatch.Success ? $" {timeMatch.Value}" : string.Empty);
                list.Add(new SuggestionItem { Title = $"Set next date: {when}", Detail = "Future date/time detected in text." });
            }

            // ---- Fine / fees / costs detection ----
            var money = new System.Text.RegularExpressions.Regex(@"\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?");
            var buckets = new List<(string label, List<string> hits)>
            {
                ("fine",              new List<string>()),
                ("fees",              new List<string>()),
                ("court costs",       new List<string>()),
                ("administrative fee",new List<string>()),
                ("costs",             new List<string>())
            };

            void scan(string label, params string[] keywords)
            {
                foreach (var k in keywords)
                {
                    var rx = new System.Text.RegularExpressions.Regex($@"{k}\s*[:=]?\s*(?:of\s*)?({money})",
                        System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                    foreach (System.Text.RegularExpressions.Match m in rx.Matches(src))
                    {
                        if (m.Groups.Count > 1)
                        {
                            var amt = m.Groups[1].Value.Replace(" ", "");
                            if (!string.IsNullOrWhiteSpace(amt))
                            {
                                var bucket = buckets.First(b => b.label == label).hits;
                                if (!bucket.Contains(amt, StringComparer.OrdinalIgnoreCase))
                                    bucket.Add(amt);
                            }
                        }
                    }
                }
            }

            scan("fine", "fine", "fines");
            scan("fees", "fee", "fees");
            scan("court costs", "court costs");
            scan("administrative fee", "administrative fee", "admin fee");
            scan("costs", "costs"); // generic fallback

            var parts = new List<string>();
            foreach (var b in buckets)
                if (b.hits.Count > 0)
                    parts.Add($"{b.label}: {string.Join(", ", b.hits.Select(h => h.StartsWith("$") ? h : $"${h}"))}");

            if (parts.Count > 0)
            {
                list.Add(new SuggestionItem
                {
                    Title = $"Add fines/fees: {string.Join("; ", parts)}",
                    Detail = "Monetary amounts detected near fine/fee/cost keywords."
                });
            }

            // De-dupe by title to avoid clutter
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            list = list.Where(s => seen.Add(s.Title)).ToList();

            return list;
        }

        // Creates a lightweight hash of the preview payload to detect changes
        private static string Fingerprint(SessionTranscribeClient.SessionPreviewResponse preview)
        {
            if (preview?.Items is null || preview.Items.Count == 0) return "none:0";

            unchecked
            {
                var sb = new StringBuilder();
                sb.Append(preview.Items.Count).Append('|');

                foreach (var it in preview.Items)
                {
                    // include event id, short text lengths, and suggestion counts
                    sb.Append(it.EventId).Append(':');
                    sb.Append((it.TranscriptExcerpt ?? "").Length).Append(':');
                    sb.Append((it.NotesSummary ?? "").Length).Append(':');
                    sb.Append((it.Suggestions?.Count ?? 0)).Append('|');
                }

                return sb.ToString();
            }
        }

        // Maps preview content into:
        //  - transcript textbox (_drafts)
        //  - AI summary into "Notes" edit buffer (_editNotesByEvent)
        //  - suggestions into _sessionPreview[eventId].Suggestions
        private void ApplyPreviewToUi(SessionTranscribeClient.SessionPreviewResponse preview)
        {
            if (preview?.Items is null) return;

            var touchedAny = false;

            foreach (var item in preview.Items)
            {
                var id = item.EventId;
                if (id <= 0) continue;

                // Ensure row exists
                var row = _searchResultList.FirstOrDefault(r => r.EventID == id);
                if (row is null) continue;

                touchedAny = true;

                // -------- Transcript → live textbox --------
                if (!string.IsNullOrWhiteSpace(item.TranscriptExcerpt))
                {
                    var snap = item.TranscriptExcerpt.Replace("\r\n", "\n");

                    if (_uploadSessionId is not null)
                    {
                        // Upload previews send the full text per event — replace wholesale.
                        _drafts[id] = snap;
                    }
                    else
                    {
                        // Live-stream path: merge deltas, but don't Trim() (can lop off tails)
                        var prev = _drafts.TryGetValue(id, out var t) ? t : string.Empty;
                        var next = ApplyDelta(prev, snap);
                        _drafts[id] = next.Replace("\r\n", "\n");
                    }
                }

                // NotesSummary → place in edit buffer (don’t clobber manual edits)
                if (!string.IsNullOrWhiteSpace(item.NotesSummary))
                {
                    if (!_editNotesByEvent.TryGetValue(id, out var curr) || string.IsNullOrWhiteSpace(curr))
                        _editNotesByEvent[id] = item.NotesSummary;
                }

                // -------- Suggestions (prefer latest) --------
                if (!_sessionPreview.TryGetValue(id, out var pv))
                    _sessionPreview[id] = pv = new PreviewInfo();

                pv.HasTranscript = !string.IsNullOrWhiteSpace(item.TranscriptExcerpt);
                pv.Summary = item.NotesSummary ?? pv.Summary;

                var incoming = MapSuggestionsRobust(item.Suggestions);

                // Always generate fallbacks (may add fines/fees or next-date even when service returns some)
                var fallbacks = GenerateFallbackSuggestions(item.NotesSummary, item.TranscriptExcerpt);

                // Merge & de-dupe by Title
                var merged = (incoming ?? new List<SuggestionItem>())
                    .Concat(fallbacks ?? new List<SuggestionItem>())
                    .GroupBy(s => s.Title, StringComparer.OrdinalIgnoreCase)
                    .Select(g => g.First())
                    .ToList();

                // Prefer the latest set when it's stronger, or during upload snapshots
                if (merged.Count > 0)
                {
                    if ((_uploadSessionId is not null) ||
                        pv.Suggestions == null ||
                        merged.Count >= pv.Suggestions.Count)
                    {
                        pv.Suggestions = merged;
                    }
                }
            }

            if (touchedAny)
            {
                ExpandAllRows();              // ensure details are visible
                _ = InvokeAsync(StateHasChanged);
            }
        }
        // Response shape returned by POST api/transcribe/save-text
        private sealed class SaveFileDto
        {
            [JsonPropertyName("mediaItemId")]
            public int MediaItemId { get; set; }

            [JsonPropertyName("url")]
            public string? Url { get; set; }
        }

        // Saves the live transcript text for a row to Sandbox -> Media -> Transcripts (server does folder creation).
        private async Task<string?> SaveTranscriptForEventAsync(EditableEvent ev, CancellationToken ct = default)
        {
            if (ev is null || ev.CaseID <= 0) return null;

            // Pull the text currently shown in the transcript box for this row
            var text = _drafts.TryGetValue(ev.EventID, out var t) ? t : string.Empty;
            if (string.IsNullOrWhiteSpace(text)) return null;

            // Date for filename from StartDate/StartTime if available
            DateTime start =
                (ev.GetType().GetProperty("StartDate")?.GetValue(ev) as DateTime?)
                ?? (ev.GetType().GetProperty("StartTime")?.GetValue(ev) as DateTime?)
                ?? DateTime.Now;

            // Try to read EventType (falls back to "Event")
            var eventType = ev.GetType().GetProperty("EventType")?.GetValue(ev)?.ToString();
            eventType = string.IsNullOrWhiteSpace(eventType) ? "Event" : eventType.Trim();

            // Build desired base name: e.g., Arraignment_10-09-25
            var baseName = $"{MakeSafe(eventType)}_{start:MM-dd-yy}";

            // Final filename without extension (server will add .txt)
            var fileName = baseName;

            // (helper to strip invalid filename chars)
            static string MakeSafe(string s)
            {
                var bad = Path.GetInvalidFileNameChars();
                return new string(s.Select(ch => bad.Contains(ch) ? '_' : ch).ToArray());
            }

            var payload = new
            {
                EventId = ev.EventID,
                CaseId = ev.CaseID,
                FileName = fileName,
                Text = text
            };

            // ---- IMPORTANT: include JWT so the server can proxy to sandbox ----
            var token = LoginService.GetToken();   // you already use this elsewhere
            using var req = new HttpRequestMessage(HttpMethod.Post, "api/transcribe/save-text")
            {
                Content = JsonContent.Create(payload)
            };
            if (!string.IsNullOrWhiteSpace(token))
            {
                req.Headers.Authorization =
                    new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
            }

            using var resp = await Http.SendAsync(req, ct);
            var raw = await resp.Content.ReadAsStringAsync(ct);

            if (!resp.IsSuccessStatusCode)
            {
                Console.WriteLine($"[SaveTranscript] save-text failed: {(int)resp.StatusCode} {resp.StatusCode} {raw}");
                resp.EnsureSuccessStatusCode(); // will throw, but now we logged the details
            }

            var saved = System.Text.Json.JsonSerializer.Deserialize<SaveFileDto>(
                raw,
                new System.Text.Json.JsonSerializerOptions { PropertyNameCaseInsensitive = true });

            if (!string.IsNullOrWhiteSpace(saved?.Url))
            {
                // lets “Open saved transcript” work
                TrySet(ev, "TranscriptUrl", saved!.Url);
            }

            return saved?.Url;
        }
        // Writes the AI Summary (if any) into the event's Notes via your existing EventService.
        private async Task PersistSummaryToNotesAsync(EditableEvent ev, CancellationToken ct = default)
        {
            if (ev is null) return;

            string? summary = null;

            // 1) Prefer the live/session preview AI Summary (blue card)
            if (HasSummary(ev.EventID))
            {
                summary = GetSummary(ev.EventID);
            }
            // 2) Fall back to anything in the edit buffer
            else if (_editNotesByEvent.TryGetValue(ev.EventID, out var buf) &&
                     !string.IsNullOrWhiteSpace(buf))
            {
                summary = buf;
            }
            // 3) Finally, fall back to the older client-side AI summary
            else if (AiHasSummary(ev.EventID))
            {
                summary = AiGetSummary(ev.EventID);
            }

            if (string.IsNullOrWhiteSpace(summary))
                return;

            var trimmed = summary.Trim();

            var dto = BuildEventUpdateDto(ev);
            dto.Notes = trimmed;  // force Notes to AI Summary

            var ok = await EventService.UpdateEventAsync(dto);
            if (ok)
            {
                // reflect immediately in the UI
                TrySet(ev, "Notes", trimmed);
                _editNotesByEvent[ev.EventID] = trimmed;
            }
        }

        // Top "Save All" / "Save All (commit session)" button
        private async Task SaveAllTranscriptsAsync()
        {
            if (_savingAll) return;
            _savingAll = true;

            try
            {
                // rows we’ll process (all that actually have transcript text)
                var rows = _searchResultList
                    .Where(r => !string.IsNullOrWhiteSpace(_drafts.TryGetValue(r.EventID, out var t) ? t : null))
                    .ToList();

                // nothing to do?
                if (rows.Count == 0)
                {
                    _savingAll = false;
                    await InvokeAsync(StateHasChanged);
                    return;
                }

                foreach (var ev in rows)
                {
                    // 1) Upload transcript as EventType_MM-dd-yy.txt and set TranscriptUrl
                    try
                    {
                        await SaveTranscriptForEventAsync(ev, CancellationToken.None);
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"[SaveAll] transcript save failed for {ev.EventID}: {ex.Message}");
                    }

                    // 2) Push AI Summary into Notes
                    try
                    {
                        await PersistSummaryToNotesAsync(ev, CancellationToken.None);
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"[SaveAll] notes update failed for {ev.EventID}: {ex.Message}");
                    }
                }

                // After “save all”, stop preview polling and hide the banner
                _previewPollCts?.Cancel();
                _uploadUiStep = 0;

                await InvokeAsync(StateHasChanged);
            }
            finally
            {
                _savingAll = false;
                await InvokeAsync(StateHasChanged);
            }
        }

        // Optional: Commit session just hides the banner & stops preview polling
        private Task CommitSessionAsync()
        {
            _previewPollCts?.Cancel();
            _uploadUiStep = 0;
            return InvokeAsync(StateHasChanged);
        }
    }
}

using System;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Net.Http.Json;
using System.Reflection;
using System.Linq;
using System.Collections.Generic;
using AiInCourtAssistant.Client.Services;
using AiInCourtAssistant.Shared.Models;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Forms;
using Microsoft.AspNetCore.SignalR.Client;
using Microsoft.JSInterop;

namespace AiInCourtAssistant.Client.Pages
{
    // Shared: DI, state, lifecycle, search, helpers
    public partial class AdvancedEventSearchPage : ComponentBase, IAsyncDisposable
    {
        #region DI
        [Inject] public HttpClient Http { get; set; } = default!;
        [Inject] public IJSRuntime JS { get; set; } = default!;
        [Inject] public NavigationManager Nav { get; set; } = default!;
        [Inject] public LoginService LoginService { get; set; } = default!;
        [Inject] private EventService EventService { get; set; } = default!;
        [Inject] public SessionTranscribeClient SessionApi { get; set; } = default!;
        // NEW: use the proxy client so we never guess routes/verbs again
        [Inject] public ICaseNoteClient CaseNotes { get; set; } = default!;
        #endregion

        #region Core State (single source of truth)
        private AdvancedEventSearchFE _searchFilter = new();
        private List<EditableEvent> _searchResultList = new();
        private bool _isLoading;
        // Save-all state (used to disable buttons / show progress)
        
        // Per-row live drafts & notes
        private readonly Dictionary<int, string> _drafts = new();
        private readonly Dictionary<int, string> _nfcNoteByEvent = new();   // latest saved NFC text per event (display)
        private readonly Dictionary<int, string> _nfcNoteEdit = new();      // edit buffer per row (textbox)

        // Selection & routing context
        private int? _activeEventId;             // for live/highlighting
        private string? _activeClientId;         // provider session id
        private string? CurrentCourtroom;
        private DateOnly? CurrentDocketDate;

        // Simple property so the .razor can check for "session"
        private string? _activeSessionId => _activeClientId;

        // Hub used ONLY to receive AI updates from /hubs/live-transcribe
        private HubConnection? _hub;
        private bool _listening;
        private CancellationTokenSource? _listenCts;

        // Upload UI state (used by .razor)
        private int _uploadUiStep;                          // 0 idle, 1..n progress
        private Guid? _lastUploadSessionId;
        private bool _armingNext;

        // Bulk UI state (used by .razor)
        private string? _bulkStatus;
        private string? _bulkNotes;
        private string? _bulkCourtNote;
        private bool _bulkCourtNoteApply;

        // Input refs / media
        private InputFile? inputSessionFile;
        private string? _nowPlayingUrl;

        // Expanded rows
        private readonly HashSet<int> _expandedRows = new();

        // ---------------- Selection (compat layer) ----------------
        // Some partials still use this set; the .razor uses ev.IsSelected.
        private readonly HashSet<int> _selected = new();

        private void ToggleSelect(int eventId)
        {
            if (!_selected.Add(eventId)) _selected.Remove(eventId);
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            if (ev is not null) ev.IsSelected = _selected.Contains(eventId);
            StateHasChanged();
        }

        private bool IsSelected(int eventId)
        {
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            return ev?.IsSelected ?? _selected.Contains(eventId);
        }
        // ----------------------------------------------------------

        // ===== AI preview caches & helpers =====

        // Upload / live preview cache keyed by EventID
        private readonly Dictionary<int, PreviewInfo> _sessionPreview = new();

        private sealed class PreviewInfo
        {
            public bool HasTranscript { get; set; }
            public string? Summary { get; set; }
            public List<SuggestionItem> Suggestions { get; set; } = new();
        }

        public sealed class SuggestionItem
        {
            public string Title { get; set; } = "";
            public string? Detail { get; set; }
            public string? Body { get; set; }           // long-form description
            public double? Confidence { get; set; }     // 0–1
        }

        // For building transcript text from preview lines
        private sealed record PreviewLine(string Kind, string Text, DateTimeOffset Ts);

        // Preview accessors used by the .razor
        private bool HasSummary(int eventId)
            => _sessionPreview.TryGetValue(eventId, out var pv) &&
               !string.IsNullOrWhiteSpace(pv.Summary);

        private string? GetSummary(int eventId)
            => _sessionPreview.TryGetValue(eventId, out var pv) ? pv.Summary : null;

        private bool HasSuggestions(int eventId)
            => _sessionPreview.TryGetValue(eventId, out var pv) &&
               pv.Suggestions is { Count: > 0 };

        private IReadOnlyList<SuggestionItem> GetSuggestions(int eventId)
            => _sessionPreview.TryGetValue(eventId, out var pv)
                ? pv.Suggestions
                : Array.Empty<SuggestionItem>();

        #endregion

        #region Lifecycle
        protected override async Task OnInitializedAsync()
        {
            try
            {
                _hub = new HubConnectionBuilder()
                    .WithUrl(Nav.ToAbsoluteUri("/live"))
                    .WithAutomaticReconnect()
                    .Build();

                // server → blazor highlight
                _hub.On<int>("ActiveEventChanged", id => SetActiveEvent(id));

                await _hub.StartAsync();

                // 🔑 hook JS → Blazor AI bridge
                await RegisterAiBridgeAsync();
            }
            catch
            {
                // Non-fatal: page should still render without live hub
            }
        }

        protected override async Task OnAfterRenderAsync(bool firstRender)
        {
            await base.OnAfterRenderAsync(firstRender);

            if (firstRender)
            {
                // JS is definitely loaded by now; retry bridge wiring
                await RegisterAiBridgeAsync();
            }
        }

        private async Task RegisterAiBridgeAsync()
        {
            if (_aiBridgeRegistered) return;

            try
            {
                _selfRef ??= DotNetObjectReference.Create(this);
                await JS.InvokeVoidAsync("liveListen.registerAiHandler", _selfRef);
                _aiBridgeRegistered = true;
                Console.WriteLine("[AI Bridge] liveListen.registerAiHandler wired.");
            }
            catch (Exception ex)
            {
                Console.WriteLine("[AI Bridge] register failed: " + ex);
            }
        }

        [JSInvokable]
        public async Task OnLiveAiUpdate(AiUpdateEnvelope dto)
        {
            if (dto is null || dto.EventId <= 0)
                return;

            // Debug logging so we can SEE that Blazor is getting hit
            Console.WriteLine(
                $"[AI Bridge] OnLiveAiUpdate event {dto.EventId} " +
                $"summary len: {dto.Summary?.Length ?? 0} " +
                $"suggestions: {dto.Suggestions?.Count ?? 0}");

            if (!_sessionPreview.TryGetValue(dto.EventId, out var pv))
                pv = _sessionPreview[dto.EventId] = new PreviewInfo();

            pv.Summary = dto.Summary ?? string.Empty;

            pv.Suggestions = dto.Suggestions is { Count: > 0 }
                ? dto.Suggestions.Select(s => new SuggestionItem
                {
                    Title = s.Title ?? "",
                    Detail = s.Detail,
                    Body = s.Body,
                    Confidence = s.Confidence
                }).ToList()
                : new List<SuggestionItem>();

            // Actually await the UI refresh
            await InvokeAsync(StateHasChanged);
        }
        #endregion

        public async ValueTask DisposeAsync()
        {
            try { if (_hub is not null) await _hub.DisposeAsync(); } catch { }
            _listenCts?.Cancel();
            _listenCts?.Dispose();
        }


        #region Search (proxy → PineTech)
        private async Task HandleSearch(AdvancedEventSearchFE req)
        {
            _isLoading = true;
            _searchResultList.Clear();
            _selected.Clear();
            StateHasChanged();

            try
            {
                // capture context (courtroom / docket date)
                CurrentCourtroom = SafeGet(req, "Courtroom") ?? SafeGet(req, "Location") ?? CurrentCourtroom;

                var ddStr = SafeGet(req, "DocketDate") ?? SafeGet(req, "Date") ?? SafeGet(req, "Start");
                if (ddStr is not null && DateTime.TryParse(ddStr, out var dt))
                    CurrentDocketDate = DateOnly.FromDateTime(dt);

                using var msg = new HttpRequestMessage(HttpMethod.Post, "api/proxy/event/search")
                { Content = JsonContent.Create(req) };
                msg.Headers.Authorization =
                    new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", LoginService.GetToken());

                var resp = await Http.SendAsync(msg);
                var raw = await resp.Content.ReadAsStringAsync();
                resp.EnsureSuccessStatusCode();

                var result = JsonSerializer.Deserialize<AdvancedEventSearchResponse>(
                    raw,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

                var items = result?.Items?.Cast<object>() ?? Array.Empty<object>();

                _searchResultList = items
                    .Select(x =>
                    {
                        var start = SafeGetDate(x, "StartDate")
                                  ?? SafeGetDate(x, "StartTime")
                                  ?? SafeGetDate(x, "Start");

                        var status = SafeGet(x, "EventStatus") ?? SafeGet(x, "Status");
                        var notes = SafeGet(x, "Notes");

                        int.TryParse(SafeGet(x, "EventID"), out var eventId);
                        int.TryParse(SafeGet(x, "CaseID"), out var caseId);
                        var caseName = SafeGet(x, "CaseName") ?? "";
                        var eventType = SafeGet(x, "EventType") ?? "";

                        var ev = new EditableEvent
                        {
                            EventID = eventId,
                            CaseID = caseId,
                            CaseName = caseName,
                            EventType = eventType,
                            IsEditing = false,
                            IsSelected = false
                        };

                        if (start is not null) TrySet(ev, "StartDate", start.Value.LocalDateTime);
                        if (!string.IsNullOrWhiteSpace(status)) TrySet(ev, "EventStatus", status);
                        if (!string.IsNullOrWhiteSpace(notes)) TrySet(ev, "Notes", notes);

                        return ev;
                    })
                    .ToList();

                // Clear any previous previews when a new search runs
                _sessionPreview.Clear();

                // Preload latest NFC for display/edit buffers
                await LoadLatestCourtNotesAsync(_searchResultList);
            }
            finally
            {
                _isLoading = false;
                StateHasChanged();
            }
        }

        #endregion

        #region UI helpers referenced by .razor
        private async void ToggleRow(int eventId)
        {
            if (!_expandedRows.Add(eventId))
                _expandedRows.Remove(eventId);
            else
            {
                var ev = _searchResultList.FirstOrDefault(e => e.EventID == eventId);
                if (ev is not null) _ = LoadLatestForEventAsync(ev); // fire & forget
            }
            await InvokeAsync(StateHasChanged);
        }

        private void ExpandRow(int eventId) => _expandedRows.Add(eventId);
        private bool IsRowExpanded(int eventId) => _expandedRows.Contains(eventId);

        private string GetLiveTranscript(int eventId)
            => _drafts.TryGetValue(eventId, out var t) ? t : string.Empty;

        private void SetLiveTranscript(int eventId, string text)
        {
            _drafts[eventId] = text ?? string.Empty;
        }

        private EventUpdateDto BuildEventUpdateDto(EditableEvent ev)
        {
            _editNotesByEvent.TryGetValue(ev.EventID, out var notesBuf);
            _editStatusByEvent.TryGetValue(ev.EventID, out var statusBuf);

            var currentNotes = GetNotes(ev.EventID);
            var currentStatus = GetStatusDisplay(ev.EventID);

            DateTime? start = null;
            var piStart = ev.GetType().GetProperty("StartDate") ?? ev.GetType().GetProperty("StartTime");
            if (piStart?.GetValue(ev) is DateTime dt) start = dt;

            return new EventUpdateDto
            {
                EventID = ev.EventID,
                Notes = notesBuf ?? currentNotes,
                EventStatus = (statusBuf ?? currentStatus)?.Trim(),
                StartDate = start,
                Type = ev.EventType,
                UpdatedBySystemUserID = 0,
                UpdatedByDisplayName = "AiInCourtAssistant"
            };
        }

        // ---------------- NFC: read helpers (via CaseNotes) ----------------
        private async Task LoadLatestCourtNotesAsync(IReadOnlyList<EditableEvent> rows)
        {
            try
            {
                var caseIds = rows.Select(r => r.CaseID).Where(id => id > 0).Distinct().ToArray();

                var jobs = caseIds.Select(async id =>
                {
                    try
                    {
                        var txt = await CaseNotes.GetLatestAsync(id) ?? string.Empty;
                        return (id, txt);
                    }
                    catch
                    {
                        return (id, txt: string.Empty);
                    }
                });

                var results = await Task.WhenAll(jobs);
                var map = results.ToDictionary(p => p.id, p => p.txt);

                foreach (var ev in rows)
                {
                    var latest = (ev.CaseID > 0 && map.TryGetValue(ev.CaseID, out var txt)) ? txt : string.Empty;
                    _nfcNoteByEvent[ev.EventID] = latest;
                    if (!_nfcNoteEdit.ContainsKey(ev.EventID) || string.IsNullOrWhiteSpace(_nfcNoteEdit[ev.EventID]))
                        _nfcNoteEdit[ev.EventID] = latest;
                }

                StateHasChanged();
            }
            catch
            {
                // best-effort
            }
        }

        // Single-row fetch used by expand
        private async Task LoadLatestForEventAsync(EditableEvent ev)
        {
            try
            {
                if (ev.CaseID <= 0) return;

                if (_nfcNoteByEvent.ContainsKey(ev.EventID)) return;

                var txt = await CaseNotes.GetLatestAsync(ev.CaseID) ?? string.Empty;
                _nfcNoteByEvent[ev.EventID] = txt;

                if (!_nfcNoteEdit.ContainsKey(ev.EventID) || string.IsNullOrWhiteSpace(_nfcNoteEdit[ev.EventID]))
                    _nfcNoteEdit[ev.EventID] = txt;

                StateHasChanged();
            }
            catch { /* best-effort */ }
        }
        // -------------------------------------------------------------------

        // bulk apply (supports both ev.IsSelected and legacy _selected)
        private async Task ApplyBulkUpdate()
        {
            var targets = _searchResultList
                .Where(x => (x.IsSelected == true) || _selected.Contains(x.EventID))
                .ToList();

            if (targets.Count == 0) return;

            foreach (var ev in targets)
            {
                ev.IsEditing = true;
                EnsureNfcBuffer(ev.EventID);

                if (!string.IsNullOrWhiteSpace(_bulkStatus))
                    _editStatusByEvent[ev.EventID] = _bulkStatus;

                if (!string.IsNullOrWhiteSpace(_bulkNotes))
                    _editNotesByEvent[ev.EventID] = _bulkNotes;

                if (_bulkCourtNoteApply && !string.IsNullOrWhiteSpace(_bulkCourtNote))
                    _nfcNoteEdit[ev.EventID] = _bulkCourtNote!;
            }
            StateHasChanged();

            foreach (var ev in targets)
            {
                var dtoOk = await EventService.UpdateEventAsync(BuildEventUpdateDto(ev));
                if (dtoOk)
                {
                    if (_editStatusByEvent.TryGetValue(ev.EventID, out var s) && !string.IsNullOrWhiteSpace(s))
                        TrySet(ev, "EventStatus", s);
                    if (_editNotesByEvent.TryGetValue(ev.EventID, out var n) && !string.IsNullOrWhiteSpace(n))
                        TrySet(ev, "Notes", n);
                    ev.IsEditing = false;
                    _isEditing[ev.EventID] = false;
                }
            }

            if (_bulkCourtNoteApply && !string.IsNullOrWhiteSpace(_bulkCourtNote))
            {
                foreach (var ev in targets)
                {
                    if (ev.CaseID <= 0) continue;
                    var text = _nfcNoteEdit.TryGetValue(ev.EventID, out var t) ? t : null;
                    if (string.IsNullOrWhiteSpace(text)) continue;

                    var ok = await CaseNotes.SaveNfcAsync(ev.CaseID, text!);
                    if (ok) _nfcNoteByEvent[ev.EventID] = text!.Trim();
                }
            }

            await InvokeAsync(StateHasChanged);
        }

        private Task SaveCourtNoteAsync(EditableEvent ev)
            => SaveNotesForCourtAsync(ev.EventID);

        private void OpenTranscript(EditableEvent ev)
        {
            var url = GetTranscriptUrl(ev.EventID);
            if (!string.IsNullOrWhiteSpace(url))
                _ = JS.InvokeVoidAsync("open", url, "_blank");
        }

        private EditableEvent? FindEvent(int eventId)
            => _searchResultList.FirstOrDefault(e => e.EventID == eventId);

        private static string? Changed(string? original, string? edited)
        {
            var a = string.IsNullOrWhiteSpace(original) ? "" : original.Trim();
            var b = string.IsNullOrWhiteSpace(edited) ? "" : edited.Trim();
            return a == b ? null : b;
        }

        private Task SaveCourtNoteAsync(int eventId)
        {
            var ev = FindEvent(eventId);
            return ev is null ? Task.CompletedTask : SaveCourtNoteAsync(ev);
        }

        private void PlayAudio(EditableEvent ev)
        {
            _nowPlayingUrl = GetAudioUrl(ev.EventID);
            StateHasChanged();
        }

        private Task ApplySuggestionAsync(EditableEvent ev, SuggestionItem s)
        {
            // For now: append suggestion title into transcript draft (can tweak later)
            var prev = _drafts.TryGetValue(ev.EventID, out var t) ? t + " " : "";
            _drafts[ev.EventID] = (prev + s.Title).Trim();
            StateHasChanged();
            return Task.CompletedTask;
        }

        private void DismissPreviewSuggestion(int eventId, SuggestionItem s)
        {
            if (_sessionPreview.TryGetValue(eventId, out var pv))
            {
                pv.Suggestions.RemoveAll(x => x.Title == s.Title && x.Detail == s.Detail);
                StateHasChanged();
            }
        }

        private void ToggleAll(ChangeEventArgs _)
        {
            var selectAll = _searchResultList.Any() && !_searchResultList.All(x => x.IsSelected == true);
            foreach (var ev in _searchResultList)
            {
                ev.IsSelected = selectAll;
                if (selectAll) _selected.Add(ev.EventID); else _selected.Remove(ev.EventID);
            }
            StateHasChanged();
        }

        private readonly Dictionary<int, bool> _isEditing = new();
        private readonly Dictionary<int, string?> _editStatusByEvent = new();
        private readonly Dictionary<int, string?> _editNotesByEvent = new();

        private bool IsEditing(int eventId) => _isEditing.TryGetValue(eventId, out var on) && on;

        private void EnsureNfcBuffer(int eventId)
        {
            if (!_nfcNoteEdit.ContainsKey(eventId))
                _nfcNoteEdit[eventId] = "";
        }

        private void CommitInlineBuffersToRow(EditableEvent ev)
        {
            if (!string.IsNullOrWhiteSpace(ev.UpdatedNotes))
                TrySet(ev, "Notes", ev.UpdatedNotes);

            if (!string.IsNullOrWhiteSpace(ev.UpdatedStatus))
                TrySet(ev, "EventStatus", ev.UpdatedStatus);

            if (_nfcNoteEdit.TryGetValue(ev.EventID, out var nfc) && !string.IsNullOrWhiteSpace(nfc))
                _nfcNoteByEvent[ev.EventID] = nfc;
        }

        private void BeginInlineEdit(EditableEvent ev)
        {
            ev.IsEditing = true;
            _isEditing[ev.EventID] = true;
            _editStatusByEvent.TryAdd(ev.EventID, GetStatusDisplay(ev.EventID));
            _editNotesByEvent.TryAdd(ev.EventID, GetNotes(ev.EventID));
            EnsureNfcBuffer(ev.EventID);
            StateHasChanged();
        }

        private void CancelInlineEdit(EditableEvent ev)
        {
            ev.IsEditing = false;
            _isEditing[ev.EventID] = false;
            StateHasChanged();
        }

        private async void SaveInlineEdit(EditableEvent ev)
        {
            try
            {
                var dto = BuildEventUpdateDto(ev);
                var ok = await EventService.UpdateEventAsync(dto);

                if (ok)
                {
                    if (_editNotesByEvent.TryGetValue(ev.EventID, out var n) && !string.IsNullOrWhiteSpace(n))
                        TrySet(ev, "Notes", n);

                    if (_editStatusByEvent.TryGetValue(ev.EventID, out var s) && !string.IsNullOrWhiteSpace(s))
                        TrySet(ev, "EventStatus", s);

                    if (_nfcNoteEdit.TryGetValue(ev.EventID, out var nfc) && !string.IsNullOrWhiteSpace(nfc))
                        _nfcNoteByEvent[ev.EventID] = nfc;

                    ev.IsEditing = false;
                    _isEditing[ev.EventID] = false;
                    await InvokeAsync(StateHasChanged);
                }
                else
                {
                    Console.WriteLine($"[EventUpdate] Save failed for Event {ev.EventID}");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("[EventUpdate] " + ex);
            }
        }

        private static string BuildTranscriptFileName(EditableEvent ev, DateOnly? docketDate)
        {
            // Use case name if we have it; fall back to EventID
            var rawName = string.IsNullOrWhiteSpace(ev.CaseName)
                ? $"Event_{ev.EventID}"
                : ev.CaseName!;

            // Make it filesystem-safe: letters/digits stay, everything else → underscore
            var safeChars = rawName
                .Select(ch => char.IsLetterOrDigit(ch) ? ch : '_')
                .ToArray();

            var safeName = new string(safeChars).Trim('_');
            if (string.IsNullOrWhiteSpace(safeName))
                safeName = $"Event_{ev.EventID}";

            // Use docket date if we captured it; otherwise today
            var date = docketDate ?? DateOnly.FromDateTime(DateTime.UtcNow);
            var datePart = date.ToString("yyyyMMdd");

            return $"{safeName}_{datePart}.txt";
        }

        private string GetStartDisplay(int eventId)
        {
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            if (ev is null) return "";
            var dtObj = ev.GetType().GetProperty("StartDate")?.GetValue(ev)
                ?? ev.GetType().GetProperty("StartTime")?.GetValue(ev);
            if (dtObj is DateTime dt) return dt.ToString("MM/dd/yyyy h:mm tt");
            if (dtObj is DateTimeOffset dto) return dto.LocalDateTime.ToString("MM/dd/yyyy h:mm tt");
            return "";
        }

        private string GetStatusDisplay(int eventId)
        {
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            if (ev is null) return "";
            return ev.GetType().GetProperty("EventStatus")?.GetValue(ev)?.ToString() ?? "";
        }

        private string GetNotes(int eventId)
        {
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            if (ev is null) return "";
            return ev.GetType().GetProperty("Notes")?.GetValue(ev)?.ToString() ?? "";
        }

        private string GetTranscriptUrl(int eventId)
        {
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            if (ev is null) return "";
            return ev.GetType().GetProperty("TranscriptUrl")?.GetValue(ev)?.ToString() ?? "";
        }

        private string GetAudioUrl(int eventId)
        {
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            if (ev is null) return "";
            return ev.GetType().GetProperty("AudioUrl")?.GetValue(ev)?.ToString() ?? "";
        }
        #endregion

        // ===================== Open all rows after upload/transcription =====================

        private void ExpandAllRows()
        {
            if (_searchResultList is null || _searchResultList.Count == 0) return;
            foreach (var ev in _searchResultList)
                _expandedRows.Add(ev.EventID);
            StateHasChanged();
        }

        #region Live highlight & helpers (legacy)
        private void SetActiveEvent(int eventId)
        {
            _activeEventId = eventId;
            _ = Highlight(eventId);
        }

        private async Task Highlight(int eventId)
        {
            try { await JS.InvokeVoidAsync("highlightRowByEventId", eventId); } catch { }
        }

        private static string ApplyDelta(string prev, string next)
        {
            if (string.IsNullOrEmpty(prev)) return next ?? string.Empty;
            if (string.IsNullOrEmpty(next)) return prev;
            if (next.StartsWith(prev, StringComparison.Ordinal)) return next;
            return next.Length >= prev.Length ? next : prev;
        }

        private static string BuildPreviewPatch(IEnumerable<PreviewLine> lines, string prior)
        {
            // Start from whatever text we already had
            var baseText = prior ?? string.Empty;
            var sb = new StringBuilder(baseText);

            foreach (var l in lines)
            {
                // Ignore empty/noisy segments
                if (string.IsNullOrWhiteSpace(l.Text))
                    continue;

                var segment = l.Text.Trim();

                // If this exact segment is already at the end of the buffer,
                // skip it so we don't get obvious duplicates from partial updates.
                if (sb.Length > 0)
                {
                    var current = sb.ToString();
                    if (current.EndsWith(segment, StringComparison.Ordinal))
                        continue;
                }

                // Add a space if needed before appending
                if (sb.Length > 0 && !char.IsWhiteSpace(sb[^1]))
                    sb.Append(' ');

                sb.Append(segment);
            }

            // Normalize multiple spaces/newlines and trim
            var merged = Regex.Replace(sb.ToString(), @"\s{2,}", " ").Trim();

            // Make sure we never "go backwards" if something odd happens:
            // ApplyDelta keeps whichever of baseText/merged is more complete.
            return ApplyDelta(baseText, merged);
        }
        #endregion

        // ---------- CENTRALIZED reflection helpers ----------
        private static string? SafeGet(object obj, string prop)
        {
            var pi = obj.GetType().GetProperty(prop, BindingFlags.Public | BindingFlags.Instance);
            return pi?.GetValue(obj)?.ToString();
        }

        private static DateTimeOffset? SafeGetDate(object obj, string prop)
        {
            var pi = obj.GetType().GetProperty(prop, BindingFlags.Public | BindingFlags.Instance);
            var v = pi?.GetValue(obj);
            if (v is DateTimeOffset dto) return dto;
            if (v is DateTime dt) return new DateTimeOffset(dt);
            if (v is string s && DateTimeOffset.TryParse(s, out var parsed)) return parsed;
            return null;
        }

        private static IEnumerable<object>? TryGetEnumerable(object obj, string prop)
        {
            var pi = obj.GetType().GetProperty(prop, BindingFlags.Public | BindingFlags.Instance);
            if (pi is null) return null;
            var val = pi.GetValue(obj);
            return val as IEnumerable<object> ??
                   (val as System.Collections.IEnumerable)?.Cast<object>();
        }

        private static void TrySet(object obj, string prop, object? value)
        {
            var pi = obj.GetType().GetProperty(prop, BindingFlags.Public | BindingFlags.Instance);
            if (pi is null || !pi.CanWrite) return;

            try
            {
                var t = Nullable.GetUnderlyingType(pi.PropertyType) ?? pi.PropertyType;
                if (value is not null && t.IsAssignableFrom(value.GetType()))
                {
                    pi.SetValue(obj, value);
                    return;
                }
                if (value is not null)
                {
                    var converted = Convert.ChangeType(value, t);
                    pi.SetValue(obj, converted);
                    return;
                }
                pi.SetValue(obj, null);
            }
            catch { /* best effort */ }
        }

        private static T? GetTaskResult<T>(Task task)
        {
            var type = task.GetType();
            if (!type.IsGenericType) return default;
            if (type.GetGenericTypeDefinition() != typeof(Task<>)) return default;
            return (T?)type.GetProperty("Result")?.GetValue(task);
        }

        // ===================== Added: helpers used by .razor =====================

        private static string EventTypePrettyLocal(string? code)
            => (code ?? "").ToUpperInvariant() switch
            {
                "ARR" or "ARRAIGN" => "Arraignment",
                "DISP" => "Disposition",
                "HRG" => "Hearing",
                "PRETR" => "Pretrial",
                "TR" => "Trial",
                _ => code ?? ""
            };

        private void OnBulkCourtNoteInput(ChangeEventArgs e)
        {
            var s = e?.Value?.ToString();
            _bulkCourtNote = s;
            _bulkCourtNoteApply = !string.IsNullOrWhiteSpace(s);
        }

        // ===================== NFC live editor (doc-like autosave) =====================

        private readonly HashSet<int> _nfcSaving = new();
        private readonly HashSet<int> _nfcError = new();
        private readonly Dictionary<int, DateTime> _nfcSavedAt = new();

        private string GetNfcLive(int eventId)
            => _nfcNoteEdit.TryGetValue(eventId, out var v) ? v : string.Empty;

        private void OnNfcInput(EditableEvent ev, ChangeEventArgs e)
        {
            if (ev is null) return;
            _nfcNoteEdit[ev.EventID] = e?.Value?.ToString() ?? string.Empty;
        }

        private async Task OnNfcBlur(EditableEvent ev)
        {
            if (ev is null) return;
            var text = GetNfcLive(ev.EventID);
            await SaveNfcAsync(ev, text, forceImmediate: true);
        }

        private async Task SaveNfcAsync(EditableEvent ev, string text, bool forceImmediate = false)
        {
            if (ev is null) return;

            var id = ev.EventID;
            _nfcNoteEdit[id] = text;

            _nfcError.Remove(id);
            _nfcSaving.Add(id);
            StateHasChanged();

            try
            {
                await SaveCourtNoteAsync(ev);
                _nfcSavedAt[id] = DateTime.UtcNow;
            }
            catch
            {
                _nfcError.Add(id);
            }
            finally
            {
                _nfcSaving.Remove(id);
                StateHasChanged();
            }
        }

        private bool IsNfcSaving(int id) => _nfcSaving.Contains(id);
        private bool IsNfcError(int id) => _nfcError.Contains(id);
        private DateTime? GetNfcSavedAt(int id) => _nfcSavedAt.TryGetValue(id, out var dt) ? dt : (DateTime?)null;

        // ---------------- AI preview bridge (JS → Blazor) ----------------
        private bool _aiBridgeRegistered;
        private DotNetObjectReference<AdvancedEventSearchPage>? _selfRef;

        public sealed class AiSuggestionDto
        {
            public string Title { get; set; } = "";
            public string? Detail { get; set; }
            public string? Body { get; set; }
            public double? Confidence { get; set; }
        }

        public sealed class AiUpdateEnvelope
        {
            public string? SessionId { get; set; }
            public int EventId { get; set; }
            public string? Summary { get; set; }
            public List<AiSuggestionDto>? Suggestions { get; set; }
        }

        #region Save “Notes for Court” (via CaseNotes)
        private async Task SaveNotesForCourtAsync(int eventId)
        {
            var ev = _searchResultList.FirstOrDefault(x => x.EventID == eventId);
            if (ev is null || ev.CaseID <= 0) return;

            if (!_nfcNoteEdit.TryGetValue(eventId, out var text) || string.IsNullOrWhiteSpace(text))
                return;

            var ok = await CaseNotes.SaveNfcAsync(ev.CaseID, text);
            if (ok)
            {
                _nfcNoteByEvent[eventId] = text.Trim();
                _nfcNoteEdit[eventId] = text.Trim();
                await InvokeAsync(StateHasChanged);
            }
            else
            {
                Console.WriteLine($"[NFC] Save failed for Event {ev.EventID} (case {ev.CaseID}).");
            }
        }
        #endregion

        #region Toolbar actions referenced by .razor
        private void ArmNextCase() { _armingNext = true; StateHasChanged(); }
        internal string UploadTitle() => _uploadUiStep switch
        {
            <= 0 => string.Empty,
            1 => "Uploading…",
            2 => "Processing…",
            _ => "Working…"
        };
        internal string UploadPct() => _uploadUiStep <= 0 ? "0" : Math.Min(100, _uploadUiStep * 20).ToString();
        #endregion

    }
}

using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Queue;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Staging;
using AiInCourtAssistant.Server.Hubs;
using AiInCourtAssistant.Shared.Models;
using Microsoft.AspNetCore.SignalR;
using System.Text;
using System.Text.RegularExpressions;

// Alias both DTOs so we can be explicit where needed
using ServerSuggestion = AiInCourtAssistant.Server.Features.SessionTranscribe.Models.SuggestionDto;
using SharedSuggestion = AiInCourtAssistant.Shared.Models.SuggestionDto;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe;

public sealed class SessionTranscribeWorker : BackgroundService
{
    private readonly ISessionTranscribeQueue _queue;
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<SessionTranscribeWorker> _log;
    private readonly IHubContext<LiveTranscribeHub> _hub;

    public SessionTranscribeWorker(
        ISessionTranscribeQueue queue,
        IServiceScopeFactory scopeFactory,
        IHubContext<LiveTranscribeHub> hub,
        ILogger<SessionTranscribeWorker> log)
    {
        _queue = queue;
        _scopeFactory = scopeFactory;
        _hub = hub;
        _log = log;
    }
    public static string GroupFor(int eventId) => $"event-{eventId}";
    public static string SessionGroup(Guid sessionId) => sessionId.ToString();

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("SessionTranscribeWorker started.");

        await foreach (var job in _queue.DequeueAsync(stoppingToken))
        {
            try
            {
                using var scope = _scopeFactory.CreateScope();

                var transcriber = scope.ServiceProvider.GetRequiredService<ITranscriptionProvider>();
                var summarizer = scope.ServiceProvider.GetRequiredService<ISummarizer>();
                var suggester = scope.ServiceProvider.GetRequiredService<ISuggestionEngine>();
                var repo = scope.ServiceProvider.GetRequiredService<ISessionTranscribeRepo>();
                var ctxStore = scope.ServiceProvider.GetRequiredService<SessionContextStore>();

                var session = await repo.GetAsync(job.SessionId, stoppingToken);
                if (session is null)
                {
                    _log.LogWarning("No staged session found for {SessionId}. Skipping.", job.SessionId);
                    continue;
                }

                // 1) Transcribe once
                var segments = await transcriber.TranscribeAsync(job.LocalPath, stoppingToken);

                // let UI know we’re alive
                await SendPreviewAsync(job.SessionId, 0, "summary", text: "Processing…");

                // 2) Wait for docket hints
                var hints = await WaitForHintsAsync(ctxStore, job.SessionId, stoppingToken);
                _log.LogInformation("Context hints for {SessionId}: {Count}", job.SessionId, hints.Count);

                // 3) Build robust splitter
                var perEventText = new Dictionary<int, StringBuilder>();

                static string Digits(string? s)
                    => string.IsNullOrWhiteSpace(s) ? "" : new string(s.Where(char.IsDigit).ToArray());

                // map docket digit suffixes → eventId (longest-first wins)
                var suffixMap = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                void RegisterSuffixes(string? caseNo, int eventId)
                {
                    var d = Digits(caseNo);
                    if (string.IsNullOrEmpty(d)) return;

                    void add(string key)
                    {
                        if (string.IsNullOrEmpty(key)) return;
                        if (!suffixMap.ContainsKey(key))
                            suffixMap[key] = eventId;
                    }

                    add(d);
                    if (d.Length >= 8) add(d[^8..]);
                    if (d.Length >= 6) add(d[^6..]);
                    if (d.Length >= 4) add(d[^4..]);
                }
                foreach (var h in hints) RegisterSuffixes(h.CaseNumber, h.EventId);

                // name tokens (fallback)
                var nameTokensByEvent = new Dictionary<int, HashSet<string>>(hints.Count);
                foreach (var h in hints)
                {
                    var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    if (!string.IsNullOrWhiteSpace(h.Label))
                    {
                        foreach (var w in h.Label.Split(new[] { ' ', ',', '–', '—', '-', '.' }, StringSplitOptions.RemoveEmptyEntries))
                        {
                            var t = w.Trim();
                            if (t.Length >= 3 && t.All(char.IsLetter)) set.Add(t);
                        }
                    }
                    nameTokensByEvent[h.EventId] = set;
                }

                var rxCasePhrase = new Regex(@"\b(case|cause)\s*number\b[^0-9]*([0-9A-Za-z\-\s]+)",
                    RegexOptions.IgnoreCase | RegexOptions.Compiled);

                var rxBareDocket = new Regex(
    @"\b\d{2}\s*,?\s*(?:[A-Z]\s*){0,3}[-\s]*\d{4,7}\b",
    RegexOptions.IgnoreCase | RegexOptions.Compiled);

                int ResolveEventIdFromDigits(string raw)
                {
                    var d = Digits(raw);
                    if (string.IsNullOrEmpty(d)) return 0;

                    string[] candidates =
                    {
                        d,
                        d.Length >= 8 ? d[^8..] : "",
                        d.Length >= 6 ? d[^6..] : "",
                        d.Length >= 4 ? d[^4..] : ""
                    };

                    foreach (var key in candidates)
                        if (!string.IsNullOrEmpty(key) && suffixMap.TryGetValue(key, out var ev))
                            return ev;

                    return 0;
                }

                int ScoreNameMatch(string sentence, int evId)
                {
                    if (!nameTokensByEvent.TryGetValue(evId, out var set) || set.Count == 0) return 0;
                    int score = 0;
                    foreach (var token in set)
                        if (sentence.IndexOf(token, StringComparison.OrdinalIgnoreCase) >= 0)
                            score++;
                    return score;
                }

                int currentEv = 0;

                foreach (var seg in segments)
                {
                    var full = seg.Text ?? string.Empty;
                    if (string.IsNullOrWhiteSpace(full)) continue;

                    foreach (var sentence in Regex.Split(full, @"(?<=[\.\?!])\s+|\n+"))
                    {
                        var s = sentence.Trim();
                        if (s.Length == 0) continue;

                        // keep wrap-up/transition lines with the current event
                        if (currentEv > 0 && IsWrapUpSentence(s))
                        {
                            if (!perEventText.TryGetValue(currentEv, out var sbWrap))
                                perEventText[currentEv] = sbWrap = new StringBuilder();
                            if (sbWrap.Length > 0) sbWrap.AppendLine();
                            sbWrap.Append(s);
                            continue;
                        }

                        // 1) "case/cause number ..." → switch
                        var m1 = rxCasePhrase.Match(s);
                        if (m1.Success)
                        {
                            var raw = m1.Groups[2].Value;
                            var ev = ResolveEventIdFromDigits(raw);
                            if (ev > 0)
                            {
                                currentEv = ev;
                                if (!perEventText.TryGetValue(currentEv, out var sb1))
                                    perEventText[currentEv] = sb1 = new StringBuilder();
                                if (sb1.Length > 0) sb1.AppendLine();
                                sb1.Append(s);
                                continue;
                            }
                            // fall through
                        }

                        // 2) bare docket → switch
                        if (!m1.Success)
                        {
                            var m2 = rxBareDocket.Match(s);
                            if (m2.Success)
                            {
                                var ev = ResolveEventIdFromDigits(m2.Value);
                                if (ev > 0)
                                {
                                    currentEv = ev;
                                    if (!perEventText.TryGetValue(currentEv, out var sb2))
                                        perEventText[currentEv] = sb2 = new StringBuilder();
                                    if (sb2.Length > 0) sb2.AppendLine();
                                    sb2.Append(s);
                                    continue;
                                }
                            }
                        }

                        // 3) unique name match → switch
                        int bestEv = 0, bestScore = 0, ties = 0;
                        foreach (var kv in nameTokensByEvent)
                        {
                            var sc = ScoreNameMatch(s, kv.Key);
                            if (sc > bestScore) { bestScore = sc; bestEv = kv.Key; ties = 0; }
                            else if (sc == bestScore && sc > 0) { ties++; }
                        }
                        if (bestScore > 0 && ties == 0)
                        {
                            currentEv = bestEv;
                            if (!perEventText.TryGetValue(currentEv, out var sb3))
                                perEventText[currentEv] = sb3 = new StringBuilder();
                            if (sb3.Length > 0) sb3.AppendLine();
                            sb3.Append(s);
                            continue;
                        }

                        // 4) No boundary → keep with current event (if set)
                        if (currentEv > 0)
                        {
                            if (!perEventText.TryGetValue(currentEv, out var sb))
                                perEventText[currentEv] = sb = new StringBuilder();
                            if (sb.Length > 0) sb.AppendLine();
                            sb.Append(s);
                        }
                        // else: still in preamble; drop
                    }
                }

                foreach (var kv in perEventText)
                    _log.LogInformation("PerEvent (final split): EventId={EventId} chars={Len}", kv.Key, kv.Value.Length);

                // 4) Emit previews & save chunks (AFTER perEventText is fully built)
                // Be defensive: ignore null/zero eventIds; if nothing positive, fall back to perEventText keys.
                int[] targets;
                if (job.EventIds is { Length: > 0 })
                {
                    var cleaned = job.EventIds.Where(id => id > 0).Distinct().ToArray();
                    targets = cleaned.Length > 0
                        ? cleaned
                        : perEventText.Keys.ToArray(); // fall back to what we actually detected
                }
                else
                {
                    targets = perEventText.Keys.ToArray();
                }

                if (targets.Length == 0)
                {
                    // Graceful fallback: if we couldn't map to specific events,
                    // still send *something* to the UI using known docket hints.
                    var flat0 = FlattenSegmentsToText(segments);
                    _log.LogInformation("Fallback previews: transcript {Len} chars", flat0.Length);

                    // Try to use known eventIds from hints (if any) so the UI will actually show it.
                    var hintEventIds = hints
                        .Select(h => h.EventId)
                        .Where(id => id > 0)
                        .Distinct()
                        .ToArray();

                    if (hintEventIds.Length == 0)
                    {
                        // Absolute last resort: keep your original EventId = 0 behavior
                        var summary0 = await summarizer.SummarizeAsync(job.TenantId, 0, segments, stoppingToken);
                        var suggestions0 = await suggester.GenerateAsync(job.TenantId, 0, segments, stoppingToken);

                        await SendPreviewAsync(job.SessionId, 0, "transcript", text: flat0);
                        await SendPreviewAsync(job.SessionId, 0, "summary", text: summary0);

                        var titles0 = suggestions0.Select(s => s.Title ?? string.Empty).Where(t => t.Length > 0).ToArray();
                        if (titles0.Length > 0)
                            await SendPreviewAsync(job.SessionId, 0, "suggestions", suggestions: titles0);

                        session.Chunks.Add(new StagedEventChunk(
                            EventId: 0,
                            CaseId: 0,
                            Confidence: 0.60,
                            NotesSummary: summary0,
                            TranscriptExcerpt: FirstExcerpt(segments),
                            Suggestions: MapToServer(suggestions0),
                            Segments: segments.ToList()
                        ));
                    }
                    else
                    {
                        // Better fallback: same full transcript/summary for each docket row,
                        // so the client sees text + suggestions per row instead of nothing.
                        foreach (var evId in hintEventIds)
                        {
                            int caseId = hints.FirstOrDefault(h => h.EventId == evId)?.CaseId ?? 0;

                            string summary;
                            IReadOnlyList<SharedSuggestion> suggestions;
                            try
                            {
                                summary = await summarizer.SummarizeAsync(job.TenantId, evId, segments, stoppingToken);
                            }
                            catch (Exception ex)
                            {
                                _log.LogWarning(ex, "Summarizer fallback failed for {SessionId} event {EventId}", job.SessionId, evId);
                                summary = string.Empty;
                            }

                            try
                            {
                                suggestions = await suggester.GenerateAsync(job.TenantId, evId, segments, stoppingToken);
                            }
                            catch (Exception ex)
                            {
                                _log.LogWarning(ex, "Suggester fallback failed for {SessionId} event {EventId}", job.SessionId, evId);
                                suggestions = Array.Empty<SharedSuggestion>();
                            }

                            await SendPreviewAsync(job.SessionId, evId, "transcript", text: flat0);
                            if (!string.IsNullOrWhiteSpace(summary))
                                await SendPreviewAsync(job.SessionId, evId, "summary", text: summary);

                            var titles = suggestions.Select(s => s.Title ?? string.Empty).Where(t => t.Length > 0).ToArray();
                            if (titles.Length > 0)
                                await SendPreviewAsync(job.SessionId, evId, "suggestions", suggestions: titles);

                            session.Chunks.Add(new StagedEventChunk(
                                EventId: evId,
                                CaseId: caseId,
                                Confidence: 0.40, // a bit lower since routing was fuzzy
                                NotesSummary: summary,
                                TranscriptExcerpt: FirstExcerpt(segments),
                                Suggestions: MapToServer(suggestions),
                                Segments: segments.ToList()
                            ));
                        }
                    }
                }
                else
                {
                    foreach (var evId in targets)
                    {
                        int caseId = 0;
                        if (job.EventIds is { Length: > 0 } && job.CaseIds is { Length: > 0 })
                        {
                            var idx = Array.IndexOf(job.EventIds, evId);
                            if (idx >= 0 && idx < job.CaseIds.Length) caseId = job.CaseIds[idx];
                        }

                        var evText = perEventText.TryGetValue(evId, out var sb) ? sb.ToString() : string.Empty;

                        if (string.IsNullOrWhiteSpace(evText))
                        {
                            session.Chunks.Add(new StagedEventChunk(
                                EventId: evId,
                                CaseId: caseId,
                                Confidence: 0.25,
                                NotesSummary: string.Empty,
                                TranscriptExcerpt: string.Empty,
                                Suggestions: new List<ServerSuggestion>(),
                                Segments: new List<TranscriptSegment>()
                            ));
                            continue;
                        }

                        // Use excerpt only for AI; store full text in Segments
                        var hint = hints.FirstOrDefault(h => h.EventId == evId);
                        var caseNo = hint?.CaseNumber;
                        var label = hint?.Label;

                        var excerpt = PickBestExcerpt(evText, caseNo, label);

                        // Summary: keep it focused (best excerpt)
                        var summarySegs = TextToSegments(excerpt);

                        // Suggestions: give more context (closer to live)
                        var suggestText = Truncate(evText, 8000); // adjust cap if you want
                        var fullSegs = TextToSegments(evText);   // persist FULL transcript

                        string summary = "";
                        IReadOnlyList<SharedSuggestion> suggestions = Array.Empty<SharedSuggestion>();

                        try
                        {
                            summary = await summarizer.SummarizeAsync(job.TenantId, evId, summarySegs, stoppingToken);
                        }
                        catch (Exception ex)
                        {
                            _log.LogWarning(ex, "Summarizer failed for session {SessionId} event {EventId}", job.SessionId, evId);
                        }

                        var allSuggestions = new List<SharedSuggestion>();

                        try
                        {
                            foreach (var chunk in ChunkText(suggestText, 1200))
                            {
                                var chunkSegs = TextToSegments(chunk);
                                var chunkSuggestions =
                                    await suggester.GenerateAsync(job.TenantId, evId, chunkSegs, stoppingToken);

                                if (chunkSuggestions?.Count > 0)
                                    allSuggestions.AddRange(chunkSuggestions);
                            }

                            suggestions = DeduplicateSuggestions(allSuggestions);
                        }
                        catch (Exception ex)
                        {
                            _log.LogWarning(ex, "Suggester failed for session {SessionId} event {EventId}", job.SessionId, evId);
                            suggestions = Array.Empty<SharedSuggestion>();
                        }
                        // Live preview: send the FULL text so the UI can render it immediately
                        await SendPreviewAsync(job.SessionId, evId, "transcript", text: evText);
                        if (!string.IsNullOrWhiteSpace(summary))
                            await SendPreviewAsync(job.SessionId, evId, "summary", text: summary);

                        var titles = suggestions.Select(s => s.Title ?? "").Where(t => t.Length > 0).ToArray();
                        if (titles.Length > 0)
                            await SendPreviewAsync(job.SessionId, evId, "suggestions", suggestions: titles);

                        // Persist full transcript in Segments; preview card shows a longer excerpt
                        session.Chunks.Add(new StagedEventChunk(
                            EventId: evId,
                            CaseId: caseId,
                            Confidence: 0.60,
                            NotesSummary: summary,
                            TranscriptExcerpt: Truncate(excerpt, 600),
                            Suggestions: MapToServer(suggestions), // map to Server type for storage
                            Segments: fullSegs.ToList()
                        ));
                    }
                }

                session.Status = "Complete";
                await repo.SaveAsync(session, stoppingToken);

                _log.LogInformation("Processed session {SessionId}; chunks: {Count}", job.SessionId, session.Chunks.Count);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Session transcription failed for {SessionId}", job.SessionId);

                try
                {
                    using var scope = _scopeFactory.CreateScope();
                    var repo = scope.ServiceProvider.GetRequiredService<ISessionTranscribeRepo>();
                    var s = await repo.GetAsync(job.SessionId, stoppingToken);
                    if (s is not null)
                    {
                        s.Status = "Failed";
                        s.Error = ex.Message;
                        await repo.SaveAsync(s, stoppingToken);
                    }
                }
                catch { /* ignore */ }
            }
            finally
            {
                try { System.IO.File.Delete(job.LocalPath); } catch { /* ignore */ }
            }
        }
    }

    // ---- Helpers ------------------------------------------------------------

    private async Task<List<DocketHint>> WaitForHintsAsync(SessionContextStore store, Guid sessionId, CancellationToken ct)
    {
        for (int i = 0; i < 25; i++)
        {
            ct.ThrowIfCancellationRequested();
            var list = store.Get(sessionId);
            if (list is { Count: > 0 }) return list;
            await Task.Delay(200, ct);
        }
        return store.Get(sessionId) ?? new List<DocketHint>();
    }

    private Task SendPreviewAsync(Guid sessionId, int eventId, string kind, string? text = null, string[]? suggestions = null)
    {
        var dto = new AiPreviewDto(sessionId, eventId, kind, text, suggestions);
        return _hub.Clients.Group(SessionGroup(sessionId)).SendAsync("ai_preview", dto);
    }

    private static string FirstExcerpt(IReadOnlyList<TranscriptSegment> segments)
        => (segments.Count > 0 && !string.IsNullOrWhiteSpace(segments[0].Text))
            ? segments[0].Text[..Math.Min(160, segments[0].Text.Length)]
            : string.Empty;

    private static string Truncate(string s, int len)
        => s.Length <= len ? s : s[..len];

    private static string FlattenSegmentsToText(IReadOnlyList<TranscriptSegment> segments)
    {
        if (segments is null || segments.Count == 0) return string.Empty;
        var sb = new StringBuilder();
        foreach (var seg in segments)
        {
            var t = seg.Text;
            if (!string.IsNullOrWhiteSpace(t))
            {
                if (sb.Length > 0) sb.AppendLine();
                sb.Append(t);
            }
        }
        return sb.ToString();
    }

    private static string PickBestExcerpt(string full, string? caseNumber, string? defName)
    {
        if (string.IsNullOrWhiteSpace(full)) return string.Empty;
        var text = full;

        var anchors = new[]
        {
            caseNumber, defName,
            "plea","guilty","not guilty","no contest",
            "fine","costs","fees","continued","continuance",
            "next court date","next date","pay","payment plan",
            "bench warrant","warrant","fta","failure to appear",
            "probation","diversion","traffic school","community service"
        }
        .Where(s => !string.IsNullOrWhiteSpace(s))
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .ToArray();

        int idx = -1;
        foreach (var a in anchors)
        {
            var i = text.IndexOf(a, StringComparison.OrdinalIgnoreCase);
            if (i >= 0) { idx = i; break; }
        }

        const int window = 1200; // a bit larger excerpt for better summaries
        if (idx < 0) return text.Length <= 2400 ? text : text[..2400];

        var start = Math.Max(0, idx - window / 2);
        var len = Math.Min(window, text.Length - start);
        return text.Substring(start, len);
    }

    static IEnumerable<string> ChunkText(string text, int maxChars)
    {
        if (string.IsNullOrWhiteSpace(text))
            yield break;

        var sb = new StringBuilder();

        foreach (var sentence in Regex.Split(text, @"(?<=[\.\?!])\s+"))
        {
            if (sb.Length + sentence.Length > maxChars && sb.Length > 0)
            {
                yield return sb.ToString();
                sb.Clear();
            }

            sb.Append(sentence).Append(' ');
        }

        if (sb.Length > 0)
            yield return sb.ToString();
    }

    static IReadOnlyList<SharedSuggestion> DeduplicateSuggestions(IReadOnlyList<SharedSuggestion> input)
    {
        if (input is null || input.Count == 0)
            return Array.Empty<SharedSuggestion>();

        static string Safe(string? s) => (s ?? string.Empty).Trim();

        // Dedupe by "what the user sees": title + detail/body.
        // Keep the highest-confidence version.
        return input
            .GroupBy(s => $"{Safe(s.Title)}|{Safe(s.Detail ?? s.Body)}")
            .Select(g => g.OrderByDescending(x => x.Confidence).First())
            .OrderByDescending(s => s.Confidence)
            .ToList();
    }

    private static IEnumerable<string> ChunkBySentence(string text, int maxChunk)
    {
        if (string.IsNullOrWhiteSpace(text)) yield break;
        var parts = Regex.Split(text, @"(?<=[\.\?!])\s+|\n+");
        foreach (var p in parts)
        {
            var s = p.Trim();
            if (s.Length == 0) continue;
            for (int i = 0; i < s.Length; i += maxChunk)
            {
                var take = Math.Min(maxChunk, s.Length - i);
                yield return s.Substring(i, take);
            }
        }
    }

    // wrap-up phrases to keep with current event
    private static readonly string[] WrapUpPhrases =
    {
        "next case", "next call", "call the next case",
        "matter continued", "status is continued", "case dismissed",
        "court accepts the plea", "court accepts the plea.",
        "case is set for", "set review", "set for review",
        "set status review"
    };

    private static bool IsWrapUpSentence(string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return false;
        foreach (var p in WrapUpPhrases)
            if (text.IndexOf(p, StringComparison.OrdinalIgnoreCase) >= 0)
                return true;
        return false;
    }

    private static IReadOnlyList<TranscriptSegment> TextToSegments(string text)
    {
        var list = new List<TranscriptSegment>();
        if (!string.IsNullOrWhiteSpace(text))
        {
            foreach (var s in ChunkBySentence(text, maxChunk: 400))
                list.Add(new TranscriptSegment(null, 0, 0, s));
        }
        return list;
    }

    // ---------- Mapping helpers (Shared -> Server model for storage) ----------
    private static List<ServerSuggestion> MapToServer(IReadOnlyList<SharedSuggestion> src)
    {
        if (src is null || src.Count == 0) return new List<ServerSuggestion>();

        var list = new List<ServerSuggestion>(src.Count);
        foreach (var s in src)
        {
            var ss = CreateServerSuggestion(s);
            if (ss is not null) list.Add(ss);
        }
        return list;
    }

    private static ServerSuggestion? CreateServerSuggestion(SharedSuggestion s)
    {
        var t = typeof(ServerSuggestion);
        var ctors = t.GetConstructors();

        // Try common primary-ctor shapes first (positional record)
        foreach (var ctor in ctors)
        {
            var ps = ctor.GetParameters();
            try
            {
                // (kind, title, detail, confidence)
                if (ps.Length == 4 &&
                    ps[0].Name!.Equals("kind", StringComparison.OrdinalIgnoreCase) &&
                    ps[1].Name!.Equals("title", StringComparison.OrdinalIgnoreCase) &&
                    ps[2].Name!.Equals("detail", StringComparison.OrdinalIgnoreCase) &&
                    ps[3].Name!.Equals("confidence", StringComparison.OrdinalIgnoreCase))
                {
                    return (ServerSuggestion)ctor.Invoke(new object?[]
                    {
                    "AI",                          // Kind
                    s.Title ?? string.Empty,       // Title
                    s.Detail ?? s.Body,            // Detail (prefer Detail; fallback Body)
                    s.Confidence                   // Confidence
                    });
                }

                // (kind, title, detail, body, confidence)
                if (ps.Length == 5 &&
                    ps[0].Name!.Equals("kind", StringComparison.OrdinalIgnoreCase) &&
                    ps[1].Name!.Equals("title", StringComparison.OrdinalIgnoreCase) &&
                    ps[2].Name!.Equals("detail", StringComparison.OrdinalIgnoreCase) &&
                    ps[3].Name!.Equals("body", StringComparison.OrdinalIgnoreCase) &&
                    ps[4].Name!.Equals("confidence", StringComparison.OrdinalIgnoreCase))
                {
                    return (ServerSuggestion)ctor.Invoke(new object?[]
                    {
                    "AI",
                    s.Title ?? string.Empty,
                    s.Detail,
                    s.Body,
                    s.Confidence
                    });
                }

                // (title, detail, confidence)
                if (ps.Length == 3 &&
                    ps[0].Name!.Equals("title", StringComparison.OrdinalIgnoreCase) &&
                    ps[1].Name!.Equals("detail", StringComparison.OrdinalIgnoreCase) &&
                    ps[2].Name!.Equals("confidence", StringComparison.OrdinalIgnoreCase))
                {
                    return (ServerSuggestion)ctor.Invoke(new object?[]
                    {
                    s.Title ?? string.Empty,
                    s.Detail ?? s.Body,
                    s.Confidence
                    });
                }
            }
            catch
            {
                // try next ctor
            }
        }

        // Fallback: try parameterless + init (or set via reflection if allowed)
        try
        {
            var obj = Activator.CreateInstance(t);
            if (obj is ServerSuggestion ss2)
            {
                // Set any of these that exist (ignores if property missing)
                TrySet(ss2, "Kind", "AI");
                TrySet(ss2, "Title", s.Title);
                TrySet(ss2, "Detail", s.Detail ?? s.Body);
                TrySet(ss2, "Body", s.Body);
                TrySet(ss2, "Confidence", s.Confidence);
                return ss2;
            }
        }
        catch
        {
            // swallow and return null
        }

        return default;
    }

    private static void TrySet(object target, string propName, object? value)
    {
        var p = target.GetType().GetProperty(propName);
        if (p?.CanWrite == true)
        {
            try { p.SetValue(target, value); } catch { /* ignore */ }
        }
    }
}

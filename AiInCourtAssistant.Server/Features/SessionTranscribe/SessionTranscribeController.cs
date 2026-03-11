using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Queue;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Staging;
using AiInCourtAssistant.Server.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using System.Collections.Concurrent;
using System.Text.RegularExpressions;
using static AiInCourtAssistant.Client.Pages.AdvancedEventSearchPage;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe
{
    [ApiController]
    [Route("api/session-transcribe")]
    [AllowAnonymous] // DEV ONLY; switch to [Authorize] later
    public sealed class SessionTranscribeController : ControllerBase
    {
        private readonly IConfiguration _cfg;
        private readonly ISessionTranscribeRepo _repo;
        private readonly ISessionTranscribeQueue _queue;
        private readonly IWebHostEnvironment _env;
        private readonly IEventService _events;
        private readonly ILogger<SessionTranscribeController> _log;
        private readonly SessionContextStore _ctxStore;   // used by the worker

        // ── Client DTOs for context posting (avoid name clash with Models.DocketHint) ──
        public sealed record DocketHintRow(int EventId, int CaseId, string? CaseNumber, string? CaseName);
        public sealed record DocketContext(int? ActiveEventId, List<DocketHintRow> Rows);

        // Preview-only, keeps the last context posted by the client
        private static readonly ConcurrentDictionary<Guid, DocketContext> _context =
            new ConcurrentDictionary<Guid, DocketContext>();

        // case-number helper
        private static readonly Regex _caseRx = new(@"\b\d{2}[-\s]?[A-Z]{1,3}[-\s]?\d{3,6}\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static string? ExtractCaseNo(string? text)
        {
            if (string.IsNullOrWhiteSpace(text)) return null;
            var m = _caseRx.Match(text);
            return m.Success ? m.Value : null;
        }

        private static string Canon(string s) =>
            new string(s.Where(char.IsLetterOrDigit).ToArray()).ToUpperInvariant();

        public SessionTranscribeController(
            IConfiguration cfg,
            ISessionTranscribeRepo repo,
            ISessionTranscribeQueue queue,
            IWebHostEnvironment env,
            IEventService events,
            SessionContextStore ctxStore,
            ILogger<SessionTranscribeController> log)
        {
            _cfg = cfg;
            _repo = repo;
            _queue = queue;
            _env = env;
            _events = events;
            _ctxStore = ctxStore;
            _log = log;
        }

        private bool Enabled => _cfg.GetValue<bool>("Features:SessionTranscription");

        // ───────────────────────────────── upload ─────────────────────────────────
        [HttpPost("upload")]
        [RequestSizeLimit(long.MaxValue)]
        public async Task<ActionResult<StartSessionResponse>> Upload(
            [FromQuery] Guid tenantId,
            [FromQuery] string? courtroom,
            [FromQuery] DateOnly docketDate,
            [FromQuery] int[]? eventIds = null,
            [FromQuery] int[]? caseIds = null,
            CancellationToken ct = default)
        {
            if (!Enabled) return Forbid();
            if (!Request.HasFormContentType) return BadRequest("multipart/form-data required.");

            var form = await Request.ReadFormAsync(ct);
            var file = form.Files.GetFile("file");
            if (file is null || file.Length == 0) return BadRequest("Missing file.");

            // 1) Create a new staged session
            var session = await _repo.CreateSessionAsync(tenantId, courtroom, docketDate, ct);

            // 2) Save the uploaded file once to disk
            var uploads = Path.Combine(_repo.GetSessionsRoot(), "uploads");
            Directory.CreateDirectory(uploads);
            var localPath = Path.Combine(uploads, $"{session.SessionId}_{Path.GetFileName(file.FileName)}");
            await using (var fs = System.IO.File.Create(localPath))
                await file.CopyToAsync(fs, ct);

            // 3) Optional: seed minimal rows for preview (CaseNumber/Name can arrive later via /context)
            if (eventIds is { Length: > 0 })
            {
                var rows = new List<DocketHintRow>(eventIds.Length);
                for (var i = 0; i < eventIds.Length; i++)
                {
                    var evId = eventIds[i];
                    var csId = (caseIds is not null && i < caseIds.Length) ? caseIds[i] : 0;
                    rows.Add(new DocketHintRow(evId, csId, null, null));
                }

                // preview dictionary (client-side helper)
                _context[session.SessionId] = new DocketContext(
                    ActiveEventId: rows.FirstOrDefault()?.EventId,
                    Rows: rows
                );

                // store model hints for the worker
                var modelHints = rows.Select(r =>
                    new Models.DocketHint(r.EventId, r.CaseId, r.CaseNumber, /* Label */ r.CaseName)
                ).ToList();

                _ctxStore.Set(session.SessionId, modelHints);
            }

            // 4) Enqueue ONE job; worker will fan-out
            var userId = User?.Identity?.Name;
            var eidLog = eventIds is null ? "null" : string.Join(",", eventIds);
            var cidLog = caseIds is null ? "null" : string.Join(",", caseIds ?? Array.Empty<int>());
            _log.LogInformation("Upload(ctx): session={SessionId} eventIds=[{E}] caseIds=[{C}] file={File}",
                session.SessionId, eidLog, cidLog, file.FileName);

            await _queue.EnqueueAsync(new StartTranscriptionJob(
                session.SessionId, tenantId, courtroom, docketDate, localPath, file.FileName, userId,
                eventIds: eventIds, caseIds: caseIds));

            return Ok(new StartSessionResponse(session.SessionId));
        }

        // ─────────────────────────────── context ────────────────────────────────
        // CLIENT POSTS: { activeEventId, rows: [...] }
        [HttpPost("{sessionId:guid}/context")]
        public IActionResult SetContext([FromRoute] Guid sessionId, [FromBody] DocketContext ctx)
        {
            // keep for preview logic
            _context[sessionId] = new DocketContext(ctx.ActiveEventId, ctx.Rows ?? new());

            // also persist hints for the worker
            var modelHints = (ctx.Rows ?? new()).Select(r =>
                new Models.DocketHint(r.EventId, r.CaseId, r.CaseNumber, /* Label: */ r.CaseName)
            ).ToList();

            _ctxStore.Set(sessionId, modelHints);

            return Ok(new { ok = true, count = modelHints.Count, active = ctx.ActiveEventId });
        }

        // ─────────────────────────────── preview ────────────────────────────────
        [HttpGet("{sessionId:guid}/preview")]
        public async Task<ActionResult<SessionPreviewResponse>> Preview([FromRoute] Guid sessionId, CancellationToken ct)
        {
            // Guid.Empty → return an empty preview so the UI doesn't hard-fail while wiring up
            if (sessionId == Guid.Empty)
            {
                return Ok(new SessionPreviewResponse(sessionId, new List<SessionPreviewItem>()));
            }

            if (!Enabled) return Forbid();

            var s = await _repo.GetAsync(sessionId, ct);
            if (s is null) return NotFound();

            // Try to attach EventId/CaseId to chunks using the client-sent context
            if ((s.Chunks?.Count ?? 0) > 0 &&
                _context.TryGetValue(sessionId, out var ctx) &&
                (ctx.Rows?.Count ?? 0) > 0)
            {
                var rows = ctx.Rows;

                var byCase = rows
                    .Where(h => !string.IsNullOrWhiteSpace(h.CaseNumber))
                    .GroupBy(h => Canon(h.CaseNumber!))
                    .ToDictionary(g => g.Key, g => g.First());

                static string? LastNameFromCaseName(string? caseName)
                {
                    if (string.IsNullOrWhiteSpace(caseName)) return null;
                    var part = caseName.Split('~').Last().Trim();
                    var last = part.Split(',', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
                    return string.IsNullOrWhiteSpace(last) ? null : last.Trim();
                }

                var nameMap = new Dictionary<string, List<DocketHintRow>>(StringComparer.OrdinalIgnoreCase);
                foreach (var h in rows)
                {
                    var ln = LastNameFromCaseName(h.CaseName);
                    if (!string.IsNullOrWhiteSpace(ln))
                    {
                        if (!nameMap.TryGetValue(ln!, out var list))
                        {
                            list = new List<DocketHintRow>();
                            nameMap[ln!] = list;
                        }
                        list.Add(h);
                    }
                }

                var changed = false;

                for (int i = 0; i < s.Chunks!.Count; i++)
                {
                    var c = s.Chunks[i];
                    if (c.EventId > 0) continue;

                    // Gather all text we can to maximize matching
                    var all = new System.Text.StringBuilder();
                    if (!string.IsNullOrWhiteSpace(c.TranscriptExcerpt)) all.AppendLine(c.TranscriptExcerpt);
                    if (!string.IsNullOrWhiteSpace(c.NotesSummary)) all.AppendLine(c.NotesSummary);
                    if (c.Segments is not null)
                    {
                        foreach (var seg in c.Segments)
                        {
                            if (!string.IsNullOrWhiteSpace(seg.Text))
                                all.Append(' ').Append(seg.Text);
                        }
                    }
                    var text = all.ToString();

                    // 1) Case number match
                    var caseNo = ExtractCaseNo(text);
                    if (caseNo is not null && byCase.TryGetValue(Canon(caseNo), out var caseHit))
                    {
                        s.Chunks[i] = c with { EventId = caseHit.EventId, CaseId = caseHit.CaseId };
                        changed = true;
                        continue;
                    }

                    // 2) Unique last-name fallback
                    var nameHits = new List<DocketHintRow>();
                    foreach (var kv in nameMap)
                    {
                        if (text.IndexOf(kv.Key, StringComparison.OrdinalIgnoreCase) >= 0 && kv.Value.Count == 1)
                            nameHits.Add(kv.Value[0]);
                    }
                    if (nameHits.Count == 1)
                    {
                        var h = nameHits[0];
                        s.Chunks[i] = c with { EventId = h.EventId, CaseId = h.CaseId };
                        changed = true;
                        continue;
                    }

                    // 3) Active row fallback
                    if (ctx.ActiveEventId is int active && active > 0)
                    {
                        var activeRow = rows.FirstOrDefault(r => r.EventId == active);
                        if (activeRow is not null)
                        {
                            s.Chunks[i] = c with { EventId = activeRow.EventId, CaseId = activeRow.CaseId };
                            changed = true;
                            continue;
                        }
                    }

                    // 4) Dev fallback: first row
                    var first = rows.FirstOrDefault();
                    if (first is not null)
                    {
                        s.Chunks[i] = c with { EventId = first.EventId, CaseId = first.CaseId };
                        changed = true;
                    }
                }

                if (changed) await _repo.SaveAsync(s, ct);
            }

            // Build response (null-safe)
            var items = (s.Chunks ?? new())
     .Select(c => new SessionPreviewItem(
         c.EventId,
         c.NotesSummary,
         (IReadOnlyList<AiInCourtAssistant.Server.Features.SessionTranscribe.Models.SuggestionDto>)
             (c.Suggestions ?? Array.Empty<AiInCourtAssistant.Server.Features.SessionTranscribe.Models.SuggestionDto>()),
         c.TranscriptExcerpt,
         c.Confidence))
     .ToList();


            return Ok(new SessionPreviewResponse(s.SessionId, items));
        }

        // ───────────────────────────── save-all ─────────────────────────────
        [HttpPost("{sessionId:guid}/save-all")]
        public async Task<IActionResult> SaveAll(
            [FromRoute] Guid sessionId,
            [FromQuery] Guid tenantId,
            [FromQuery] int? defaultEventId = null,
            [FromQuery] int? defaultCaseId = null,
            CancellationToken ct = default)
        {
            if (!Enabled) return Forbid();

            var s = await _repo.GetAsync(sessionId, ct);
            if (s is null) return NotFound();
            if (s.Status != "Complete" && s.Status != "Committed")
                return BadRequest("Session not complete.");

            var notes = new List<AiInCourtAssistant.Shared.Models.NoteUpdateDto>();
            foreach (var c in s.Chunks)
            {
                var evId = c.EventId > 0 ? c.EventId : (defaultEventId ?? 0);
                var caseId = c.CaseId > 0 ? c.CaseId : (defaultCaseId ?? 0);
                if (evId <= 0) continue;
                notes.Add(new AiInCourtAssistant.Shared.Models.NoteUpdateDto(evId, Math.Max(caseId, 0), c.NotesSummary));
            }

            var updatedCount = notes.Count > 0 ? await _events.SaveBulkNotesAsync(notes, ct) : 0;

            // Upload .txt transcript to Media per event
            var uploads = 0;
            foreach (var c in s.Chunks)
            {
                var evId = c.EventId > 0 ? c.EventId : (defaultEventId ?? 0);
                if (evId <= 0) continue;

                var transcript = (c.Segments is { Count: > 0 })
                    ? string.Join("\n", c.Segments.Where(seg => !string.IsNullOrWhiteSpace(seg.Text)).Select(seg => seg.Text))
                    : c.TranscriptExcerpt ?? string.Empty;

                if (string.IsNullOrWhiteSpace(transcript)) continue;

                var fileName = $"Transcript_{DateTime.UtcNow:yyyyMMdd_HHmmss}_{evId}.txt";
                try
                {
                    await _events.UploadTranscriptAsync(evId, fileName, transcript, ct);
                    uploads++;
                }
                catch
                {
                    // optional: log error
                }
            }

            await _repo.MarkCommittedAsync(sessionId, ct);
            return Ok(new { ok = true, updated = updatedCount, assigned = notes.Count, transcriptsUploaded = uploads });
        }

        // ───────────────────────────── debug ─────────────────────────────
        [HttpGet("_debug/where")]
        public IActionResult Where()
        {
            var root = _repo.GetSessionsRoot();
            var uploads = Path.Combine(root, "uploads");
            Directory.CreateDirectory(uploads);
            var files = Directory.GetFiles(uploads).Select(p => new { name = Path.GetFileName(p), size = new FileInfo(p).Length }).ToList();
            return Ok(new { root, uploads, count = files.Count, files });
        }

        [HttpGet("{sessionId:guid}/_debug/state")]
        public async Task<IActionResult> State(Guid sessionId, CancellationToken ct)
        {
            var s = await _repo.GetAsync(sessionId, ct);
            if (s is null) return NotFound(new { message = "session not found" });
            return Ok(new { s.SessionId, s.Status, chunkCount = s.Chunks?.Count ?? 0, firstChunk = s.Chunks?.FirstOrDefault() });
        }

        [HttpGet("{sessionId:guid}/_debug/context")]
        public IActionResult GetContext(Guid sessionId)
        {
            if (_context.TryGetValue(sessionId, out var ctx))
                return Ok(new { has = true, active = ctx.ActiveEventId, count = ctx.Rows.Count, list = ctx.Rows });
            return Ok(new { has = false, count = 0, list = Array.Empty<object>() });
        }

        // ───────────────────────── sandbox preview fallback ─────────────────────────
        private SessionPreviewResponse BuildSandboxPreviewResponse()
        {
            var folder = Path.Combine(AppContext.BaseDirectory, "App_Data", "SessionTranscripts");
            if (!Directory.Exists(folder))
                return new SessionPreviewResponse(Guid.Empty, new List<SessionPreviewItem>());

            var items = new List<SessionPreviewItem>();

            // group by eventId from any file that starts with "<eventId>-"
            var byEvent = Directory
                .GetFiles(folder)
                .Select(f => new { path = f, name = Path.GetFileName(f) })
                .Select(x =>
                {
                    var dash = x.name.IndexOf('-');
                    if (dash <= 0) return (ok: false, eventId: 0, x.path, x.name);
                    return (ok: int.TryParse(x.name[..dash], out var id), eventId: id, x.path, x.name);
                })
                .Where(t => t.ok && t.eventId > 0)
                .GroupBy(t => t.eventId);

            foreach (var g in byEvent)
            {
                string? notes = null;
                string? excerpt = null;

                // latest NFC notes
                var nfcFile = g
                    .Where(t =>
                        t.name.IndexOf("-nfc-", StringComparison.OrdinalIgnoreCase) >= 0 ||
                        t.name.EndsWith("-nfc.txt", StringComparison.OrdinalIgnoreCase) ||
                        t.name.Equals($"{g.Key}-nfc-latest.txt", StringComparison.OrdinalIgnoreCase))
                    .OrderByDescending(t => System.IO.File.GetLastWriteTimeUtc(t.path))
                    .FirstOrDefault();

                if (nfcFile.path is not null && System.IO.File.Exists(nfcFile.path))
                {
                    try { notes = System.IO.File.ReadAllText(nfcFile.path); } catch { }
                }

                // transcript excerpt (first ~500 chars)
                var txFile = g
                    .Where(t => t.name.IndexOf("-transcript", StringComparison.OrdinalIgnoreCase) >= 0)
                    .OrderByDescending(t => System.IO.File.GetLastWriteTimeUtc(t.path))
                    .FirstOrDefault();

                if (txFile.path is not null && System.IO.File.Exists(txFile.path))
                {
                    try
                    {
                        var txt = System.IO.File.ReadAllText(txFile.path);
                        excerpt = string.IsNullOrWhiteSpace(txt) ? null :
                                  (txt.Length <= 500 ? txt : txt[..500] + "…");
                    }
                    catch { }
                }

                items.Add(new SessionPreviewItem(
    g.Key,
    notes,
    Array.Empty<AiInCourtAssistant.Server.Features.SessionTranscribe.Models.SuggestionDto>(),
    excerpt,
    0d
));
            }

            return new SessionPreviewResponse(Guid.Empty, items);
        }
    }
}

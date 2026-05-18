<script>
  // Renderer side. Talks to the Electron main process exclusively
  // through ``window.api`` (defined in electron/preload.cjs). No
  // Node, no fs — everything that touches the disk or spawns a
  // process is brokered through the typed IPC surface.

  import SettingsDialog  from "./SettingsDialog.svelte";
  import MappingsDialog  from "./MappingsDialog.svelte";
  import HelpDialog      from "./HelpDialog.svelte";

  /** @type {string} The model id surfaced as the toolbar status. */
  let model = $state("gpt-5.5");
  /** @type {boolean} Whether an API key has been saved in settings. */
  let hasApiKey = $state(false);
  /** @type {boolean} Settings dialog open flag. */
  let settingsOpen  = $state(false);
  /** @type {boolean} Mappings dialog open flag. */
  let mappingsOpen  = $state(false);
  /** @type {boolean} Help dialog open flag. */
  let helpOpen      = $state(false);

  /**
   * @type {null | {
   *   source: { path: string, rtf: string },
   *   converted: { rtf: string },
   *   template_name: string | null,
   *   audience: string | null,
   *   org: string,
   *   totals: {
   *     jda_tokens: number, pine_tokens: number,
   *     pattern_segments: number, llm_segments: number,
   *     unmatched_segments: number, edit_segments: number,
   *   },
   *   segments: Array<object>,
   *   issues: Array<object>,
   * }}
   * Current conversion bundle, or null when nothing's loaded.
   */
  let result = $state(null);

  /** Async work indicator for the Convert action. */
  let busy = $state(false);

  /** Last error string (any failure path). Cleared on next action. */
  let error = $state("");

  /** Path of the currently-loaded RTF (so Save knows what to suggest). */
  let sourcePath = $state(null);
  /** Path the converted RTF was last explicitly saved to (enables silent Save). */
  let savedPath = $state(null);

  // Which chip is currently being edited. Replaces the inline
  // contenteditable with a popover anchored to the chip's bounding rect.
  /** @type {null | { partIndex: number, rect: DOMRect }} */
  let editingChip = $state(null);

  // Hover-info card. Coordinates are viewport-relative (position: fixed).
  let hoverInfo = $state(null);

  // The segment whose JDA chips on the legacy side should glow as the
  // "source" of whatever Pine chip is currently being hovered or
  // edited. Drives the ``.linked`` class on legacy chips.
  /** @type {number | null} */
  let highlightedSegmentIndex = $state(null);

  // Which (segmentIndex, tokenIndex) chips have been edited in this
  // session, plus their persistence state.
  let editedKeys = $state({});  // key → { status: "saved"|"pending"|"error", error?: string }

  // Undo/redo stacks — each entry is a snapshot of { rtf, editedKeys }
  // taken just before a chip edit is applied.
  let undoStack = $state([]);
  let redoStack = $state([]);

  // Auto-save / crash-recovery state. The snapshot is written to a
  // hidden file under app userData/recovery/<sha1(sourcePath)>.json
  // every ~500ms while edits are in flight. An explicit Save clears
  // the snapshot. Opening the same source RTF later re-loads the
  // snapshot and offers to restore.
  /** @type {"idle"|"saving"|"saved"|"error"} */
  let autoSaveStatus = $state("idle");
  /** @type {number | null} ms epoch of the last successful auto-save. */
  let lastAutoSavedAt = $state(null);
  /** @type {null | { snapshot: object, savedAt: number }} Pending restore prompt. */
  let recoveryOffer = $state(null);
  /** Timer handle for the debounce. Not reactive — plain JS. */
  let autoSaveTimer = null;

  // Hydrate the toolbar model pill and API key presence from saved settings on mount.
  $effect(() => {
    window.api.getSettings().then((s) => {
      if (s.model) model = s.model;
      hasApiKey = !!s.api_key;
    }).catch(() => { /* ignore — toolbar just shows defaults */ });
  });

  // Open the Settings dialog when the user picks it from the
  // application menu (File → Settings on Linux/Win, ⌘ → Preferences
  // on macOS, or the Ctrl/Cmd+, accelerator).
  $effect(() => {
    window.api.onOpenSettings(() => { settingsOpen  = true; });
    window.api.onOpenMappings(() => { mappingsOpen  = true; });
  });

  // Same for the Help menu (F1 accelerator).
  $effect(() => {
    window.api.onOpenHelp(() => { helpOpen = true; });
  });

  // File → Open… / Ctrl+O, Save / Ctrl+S, Save As… / Ctrl+Shift+S from the app menu.
  $effect(() => {
    window.api.onOpenFile(() => { handleOpen(); });
    window.api.onSaveFile(() => { handleSave(); });
    window.api.onSaveFileAs(() => { handleSaveAs(); });
    window.api.onUndo(() => { handleUndo(); });
    window.api.onRedo(() => { handleRedo(); });
  });

  // Global Ctrl+Z / Ctrl+Shift+Z keyboard shortcuts for undo/redo.
  // Skipped when a contenteditable chip is focused so the browser's
  // native text-level undo still works while typing inside a chip.
  $effect(() => {
    function onKeydown(e) {
      if (e.target.isContentEditable) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && !e.shiftKey && e.key === "z") {
        e.preventDefault();
        handleUndo();
      } else if (mod && (e.key === "y" || (e.shiftKey && e.key === "z"))) {
        e.preventDefault();
        handleRedo();
      }
    }
    window.addEventListener("keydown", onKeydown);
    return () => window.removeEventListener("keydown", onKeydown);
  });

  // ── actions ─────────────────────────────────────────────────────

  async function handleOpen() {
    error = "";
    const picked = await window.api.openRtf();
    if (!picked) return;
    sourcePath = picked.path;
    savedPath = null;
    editedKeys = {};
    undoStack = [];
    redoStack = [];
    autoSaveStatus = "idle";
    lastAutoSavedAt = null;
    recoveryOffer = null;
    // Stub the panes immediately so the user sees the legacy content
    // even before the converter finishes. The converted side stays
    // empty until Convert runs.
    result = {
      source: { path: picked.path, rtf: picked.rtf },
      converted: { rtf: "" },
      template_name: picked.path.split(/[\\/]/).pop(),
      audience: null,
      org: "oba",
      totals: {
        jda_tokens: 0, pine_tokens: 0,
        pattern_segments: 0, llm_segments: 0,
        unmatched_segments: 0, edit_segments: 0,
      },
      segments: [], issues: [],
    };
    // Look for a previous auto-save and surface a restore prompt. A
    // recovery file is only worth offering if it has a converted RTF —
    // a bare stub from a prior session that never ran Convert isn't
    // worth reviving.
    try {
      const rec = await window.api.loadRecovery({ sourcePath: picked.path });
      if (rec?.payload?.result?.converted?.rtf) {
        recoveryOffer = { snapshot: rec.payload, savedAt: rec.savedAt };
      }
    } catch { /* no recovery — fine */ }
  }

  async function handleConvert() {
    if (!sourcePath) {
      error = "Open an RTF first.";
      return;
    }
    error = "";
    busy = true;
    editingChip = null;
    hoverInfo = null;
    editedKeys = {};
    undoStack = [];
    redoStack = [];
    try {
      result = await window.api.convertRtf({ path: sourcePath });
      // A fresh conversion supersedes any pending restore offer for
      // this source — running Convert is an implicit "discard recovery".
      if (recoveryOffer) {
        recoveryOffer = null;
        window.api.clearRecovery({ sourcePath }).catch(() => {});
      }
    } catch (e) {
      error = String(e?.message || e);
    } finally {
      busy = false;
    }
  }

  /**
   * Collect current Pine token strings and regenerate the CreateVar
   * prelude to reflect any edits made since the last conversion. Returns
   * the updated RTF string, or the original if regeneration fails (so a
   * save error is never fatal to the user's work).
   */
  async function buildFreshRtf() {
    const currentPineTokens = pineParts
      .filter((p) => p.kind === "pine")
      .map((p) => p.text);
    const r = await window.api.refreshPrelude({
      rtf: result.converted.rtf,
      pine_tokens: currentPineTokens,
      org: result.org || "oba",
      prelude_count: result.prelude_pine_token_count || 0,
    });
    if (r?.ok && typeof r.rtf === "string") {
      return r.rtf;
    }
    if (r?.error) console.warn("refreshPrelude failed:", r.error);
    return result.converted.rtf;
  }

  async function handleSaveAs() {
    if (!result?.converted?.rtf) {
      error = "Nothing to save. Convert first.";
      return;
    }
    error = "";
    const freshRtf = await buildFreshRtf();
    const defaultName = savedPath
      ? savedPath.split(/[\\/]/).pop()
      : sourcePath
        ? sourcePath.replace(/(\.rtf)?$/i, ".pine.rtf").split(/[\\/]/).pop()
        : "converted.pine.rtf";
    const saved = await window.api.saveRtf({
      defaultPath: defaultName,
      rtf: freshRtf,
    });
    if (saved) {
      savedPath = saved.path;
      if (sourcePath) window.api.clearRecovery({ sourcePath }).catch(() => {});
      autoSaveStatus = "idle";
      lastAutoSavedAt = null;
    }
  }

  async function handleSave() {
    if (!result?.converted?.rtf) {
      error = "Nothing to save. Convert first.";
      return;
    }
    // If we've already saved once this session, write silently to the same path.
    if (savedPath) {
      error = "";
      try {
        const freshRtf = await buildFreshRtf();
        await window.api.writeRtf({ path: savedPath, rtf: freshRtf });
        if (sourcePath) window.api.clearRecovery({ sourcePath }).catch(() => {});
        autoSaveStatus = "idle";
        lastAutoSavedAt = null;
      } catch (e) {
        error = String(e?.message || e);
      }
    } else {
      await handleSaveAs();
    }
  }

  function handleUndo() {
    if (!undoStack.length) return;
    const prev = undoStack[undoStack.length - 1];
    redoStack = [...redoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys } }];
    undoStack = undoStack.slice(0, -1);
    result.converted.rtf = prev.rtf;
    editedKeys = prev.editedKeys;
  }

  function handleRedo() {
    if (!redoStack.length) return;
    const next = redoStack[redoStack.length - 1];
    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys } }];
    redoStack = redoStack.slice(0, -1);
    result.converted.rtf = next.rtf;
    editedKeys = next.editedKeys;
  }

  function acceptRecovery() {
    if (!recoveryOffer?.snapshot) return;
    const snap = recoveryOffer.snapshot;
    if (snap.result) result = snap.result;
    if (snap.editedKeys) editedKeys = snap.editedKeys;
    recoveryOffer = null;
    // The snapshot we just loaded is identical to what's on disk;
    // auto-save will re-trigger from the $effect but write the same
    // bytes back, which is harmless.
    autoSaveStatus = "saved";
    lastAutoSavedAt = snap?.savedAt || Date.now();
  }

  function dismissRecovery() {
    recoveryOffer = null;
    if (sourcePath) {
      window.api.clearRecovery({ sourcePath }).catch(() => {});
    }
  }

  // Debounced auto-save. The effect below tracks reads of the bits
  // that matter (converted RTF + editedKeys) and re-arms this timer
  // on every change. The 500ms window coalesces burst edits — typing
  // in a chip can fire many small mutations, but only the last one
  // hits disk.
  function scheduleAutoSave() {
    if (!sourcePath) return;
    if (!result?.converted?.rtf) return;
    if (recoveryOffer) return;  // Don't write over a snapshot the user hasn't decided about yet.
    if (autoSaveTimer) clearTimeout(autoSaveTimer);
    autoSaveTimer = setTimeout(async () => {
      autoSaveTimer = null;
      autoSaveStatus = "saving";
      try {
        // JSON-roundtrip strips Svelte 5 $state proxies; structured-
        // clone over the IPC bridge refuses them otherwise.
        const payload = JSON.parse(JSON.stringify({
          result,
          editedKeys,
        }));
        const r = await window.api.saveRecovery({ sourcePath, payload });
        if (r?.ok) {
          autoSaveStatus = "saved";
          lastAutoSavedAt = Date.now();
        } else {
          autoSaveStatus = "error";
        }
      } catch {
        autoSaveStatus = "error";
      }
    }, 500);
  }

  // Reactive hook: any change to the converted RTF or the edited-keys
  // map re-arms the debounced save. Reading the deep properties here
  // is what tells Svelte 5 to subscribe to them.
  $effect(() => {
    result?.converted?.rtf;
    editedKeys;
    scheduleAutoSave();
  });

  function onSettingsSaved(next) {
    model = next?.model || "gpt-5.5";
    hasApiKey = !!next?.api_key;
  }

  // ── token rendering ─────────────────────────────────────────────

  // Header groups (and their nested content) that should be dropped
  // wholesale when stripping RTF for display.
  const RTF_SKIP_HEADERS = new Set([
    "fonttbl", "filetbl", "colortbl", "stylesheet", "info",
    "generator", "listtable", "listoverridetable", "rsidtbl",
    "themedata", "latentstyles", "datastore", "pict", "object",
    "shppict", "nonshppict", "background", "pgptbl", "wgrffmtfilter",
    "xmlnstbl",
  ]);

  const RTF_CONTROL_TO_TEXT = {
    par: "\n", line: "\n", tab: "\t",
    lquote: "\u2018", rquote: "\u2019",
    ldblquote: "\u201c", rdblquote: "\u201d",
    endash: "\u2013", emdash: "\u2014",
    bullet: "\u2022", tilde: "~",
    enspace: "\u2002", emspace: "\u2003",
    nbsp: "\u00a0",
  };

  /**
   * Parse RTF section geometry. Returns one entry per RTF section, each
   * with the page height and width in CSS pixels (twips / 15 at 96 DPI).
   * Falls back to letter (816 × 1056) when no RTF dimension tags are found.
   */
  function parseSections(rtf) {
    if (!rtf) return [{ heightPx: 1056, widthPx: 816 }];
    const paperH = rtf.match(/\\paperh(\d+)/);
    const paperW = rtf.match(/\\paperw(\d+)/);
    const defaultH = paperH ? Math.round(parseInt(paperH[1], 10) / 15) : 1056;
    const defaultW = paperW ? Math.round(parseInt(paperW[1], 10) / 15) : 816;
    // Split on \sect (not \sectd, \section, etc.)
    const chunks = rtf.split(/\\sect(?![a-zA-Z])/);
    if (chunks.length === 1) return [{ heightPx: defaultH, widthPx: defaultW }];
    return chunks.map((chunk) => {
      const h = chunk.match(/\\pghsxn(\d+)/);
      const w = chunk.match(/\\pgwsxn(\d+)/);
      return {
        heightPx: h ? Math.round(parseInt(h[1], 10) / 15) : defaultH,
        widthPx:  w ? Math.round(parseInt(w[1], 10) / 15) : defaultW,
      };
    });
  }

  function stripRtf(s) {
    if (!s) return "";
    const len = s.length;
    let out = "";
    let i = 0;
    let depth = 0;
    let skipUntilDepth = -1;

    while (i < len) {
      const c = s[i];

      if (c === "{") {
        depth++;
        let j = i + 1;
        if (s[j] === "\\") {
          j++;
          if (s[j] === "*") {
            if (skipUntilDepth < 0) skipUntilDepth = depth;
          } else {
            let name = "";
            while (j < len && /[a-zA-Z]/.test(s[j])) { name += s[j]; j++; }
            if (RTF_SKIP_HEADERS.has(name) && skipUntilDepth < 0) {
              skipUntilDepth = depth;
            }
          }
        }
        i++;
        continue;
      }
      if (c === "}") {
        if (skipUntilDepth === depth) skipUntilDepth = -1;
        depth--;
        i++;
        continue;
      }
      if (skipUntilDepth >= 0) { i++; continue; }

      if (c === "\\") {
        const next = s[i + 1];
        if (next === "\\" || next === "{" || next === "}") {
          out += next; i += 2; continue;
        }
        if (next === "'") {
          const hex = s.substr(i + 2, 2);
          if (/^[0-9a-fA-F]{2}$/.test(hex)) {
            out += String.fromCharCode(parseInt(hex, 16));
            i += 4; continue;
          }
          i += 2; continue;
        }
        if (next === "u") {
          let j = i + 2;
          let sign = "";
          if (s[j] === "-") { sign = "-"; j++; }
          let num = "";
          while (j < len && /\d/.test(s[j])) { num += s[j]; j++; }
          if (num) {
            let code = parseInt(sign + num, 10);
            if (code < 0) code += 0x10000;
            out += String.fromCharCode(code);
            if (s[j] === "?") j++;
            else if (s[j] === " ") j++;
            else if (s[j] && !/[\\{}]/.test(s[j])) j++;
            i = j; continue;
          }
        }
        if (/[a-zA-Z]/.test(next)) {
          let j = i + 1;
          let name = "";
          while (j < len && /[a-zA-Z]/.test(s[j])) { name += s[j]; j++; }
          if (s[j] === "-" || /\d/.test(s[j])) {
            if (s[j] === "-") j++;
            while (j < len && /\d/.test(s[j])) j++;
          }
          if (s[j] === " ") j++;
          const repl = RTF_CONTROL_TO_TEXT[name];
          if (repl !== undefined) out += repl;
          i = j; continue;
        }
        i += 2; continue;
      }
      if (c === "\r" || c === "\n") { i++; continue; }
      out += c;
      i++;
    }
    return out;
  }

  function tokenize(text) {
    if (!text) return [];
    const parts = [];
    const len = text.length;
    let i = 0;
    let proseStart = 0;

    while (i < len) {
      const ch = text[i];

      // RTF section break: \sect but not \sectd, \section, etc.
      if (ch === "\\" && text.slice(i + 1, i + 5) === "sect" && !/[a-zA-Z]/.test(text[i + 5] || "")) {
        if (i > proseStart) {
          parts.push({ kind: "prose", text: stripRtf(text.slice(proseStart, i)), start: proseStart, end: i });
        }
        const end = i + 5 + (text[i + 5] === " " ? 1 : 0);
        parts.push({ kind: "section-break", start: i, end });
        i = end;
        proseStart = i;
        continue;
      }

      if ((ch === "%" || ch === "@") && text[i + 1] === "[") {
        let depth = 1;
        let j = i + 2;
        while (j < len) {
          const c = text[j];
          if (c === "[") depth++;
          else if (c === "]") {
            depth--;
            if (depth === 0) { j++; break; }
          }
          j++;
        }
        if (depth === 0) {
          if (i > proseStart) {
            parts.push({
              kind: "prose",
              text: stripRtf(text.slice(proseStart, i)),
              start: proseStart, end: i,
            });
          }
          parts.push({
            kind: ch === "%" ? "legacy" : "pine",
            text: stripRtf(text.slice(i, j)),
            start: i, end: j,
          });
          i = j;
          proseStart = j;
          continue;
        }
      }
      i++;
    }
    if (proseStart < len) {
      parts.push({
        kind: "prose",
        text: stripRtf(text.slice(proseStart)),
        start: proseStart, end: len,
      });
    }
    return parts;
  }

  let legacyParts = $derived(tokenize(result?.source?.rtf || ""));
  let pineParts   = $derived(tokenize(result?.converted?.rtf || ""));

  /**
   * Group the flat parts list into paragraphs so we can render each
   * one as a real ``<p>`` and get consistent paragraph rhythm instead
   * of relying on ``pre-wrap`` newlines. Each paragraph is a list of
   * inline items, either a chip (``{ kind: "chip", idx }`` referencing
   * the flat ``parts[idx]``) or a prose string (``{ kind: "prose", text }``).
   *
   * Splitting prose runs on ``\n`` is safe because chip splicing uses
   * the flat array's stored byte offsets — those only matter for chips,
   * which keep their original ``idx`` here.
   */
  function groupParagraphs(parts) {
    if (!parts.length) return [];
    const paragraphs = [[]];
    let cur = paragraphs[0];
    parts.forEach((part, idx) => {
      if (part.kind === "section-break") {
        paragraphs.push({ __sectionBreak: true });
        cur = [];
        paragraphs.push(cur);
        return;
      }
      if (part.kind === "prose") {
        const lines = (part.text || "").split("\n");
        lines.forEach((line, i) => {
          if (i > 0) {
            cur = [];
            paragraphs.push(cur);
          }
          if (line.length > 0) cur.push({ kind: "prose", text: line });
        });
      } else {
        cur.push({ kind: "chip", idx });
      }
    });
    // Drop dangling empty paragraphs at the end so we don't paint
    // ghost vertical whitespace below the last real line.
    while (paragraphs.length > 1 && paragraphs[paragraphs.length - 1].length === 0) {
      paragraphs.pop();
    }
    return paragraphs;
  }

  /**
   * Split a flat groupParagraphs output at ``__sectionBreak`` sentinels
   * and pair each run with the corresponding section geometry.
   */
  function groupIntoSections(paragraphs, sections) {
    const result = [];
    let sIdx = 0;
    let cur = [];
    for (const para of paragraphs) {
      if (para.__sectionBreak) {
        result.push({ ...(sections[sIdx] || { heightPx: 1056, widthPx: 816 }), paragraphs: cur });
        sIdx++;
        cur = [];
      } else {
        cur.push(para);
      }
    }
    result.push({ ...(sections[sIdx] || { heightPx: 1056, widthPx: 816 }), paragraphs: cur });
    return result;
  }

  let legacySections = $derived(groupIntoSections(
    groupParagraphs(legacyParts),
    parseSections(result?.source?.rtf || ""),
  ));
  let pineSections = $derived(groupIntoSections(
    groupParagraphs(pineParts),
    parseSections(result?.converted?.rtf || ""),
  ));

  // ── Pagination ──────────────────────────────────────────────────
  // CSS alone can't break content at page boundaries (without
  // measuring laid-out text), so any horizontal page-break visual
  // that sits at a fixed Y inside .paper is doomed to overlap real
  // text. Instead, after each render we measure every <p>, and for
  // any paragraph whose box would cross a page-content-area boundary
  // we push it down (via marginTop) to start at the top of the next
  // page. The page-break band visual then falls into the empty gap
  // between pages, never crossing a line of prose.
  //
  // Numbers below match the .paper CSS — keep them in sync.

  const PAGE_HEIGHT       = 1056; // letter at 96 DPI
  const PAGE_TOP_MARGIN   = 96;   // paper padding-top (1 in)
  const PAGE_BOT_MARGIN   = 96;   // paper padding-bottom (1 in)
  const PAGE_GAP          = 16;   // visible inter-page gap
  const PAGE_CYCLE        = PAGE_HEIGHT + PAGE_GAP; // 1072
  const CONTENT_PER_PAGE  = PAGE_HEIGHT - PAGE_TOP_MARGIN - PAGE_BOT_MARGIN; // 864

  function adjustPagination(paper) {
    if (!paper) return;
    const sectionH = parseInt(paper.dataset.sectionHeight || "1056", 10);
    const ps = paper.querySelectorAll(":scope > .prose > p");
    paper.style.minHeight = "";
    for (const p of ps) p.style.marginTop = "";
    const paperRect = paper.getBoundingClientRect();
    // Short pages (e.g. CreateVar prelude boxes) don't paginate — just
    // enforce the section's own height.
    if (sectionH < PAGE_HEIGHT || !ps.length) {
      paper.style.minHeight = `${sectionH}px`;
      return;
    }
    // Walk top-to-bottom. Each push cascades to subsequent paragraphs
    // (their measured top moves accordingly), so a single forward pass
    // is enough. We compute the new marginTop relative to the PREVIOUS
    // paragraph's box bottom — not the current paragraph's measured
    // top — because CSS margin-collapse between adjacent <p>s would
    // otherwise eat the prev margin-bottom (~8pt) and leave the
    // pushed paragraph that much short of where we wanted it.
    for (let i = 0; i < ps.length; i++) {
      const p = ps[i];
      const r = p.getBoundingClientRect();
      const topY    = r.top    - paperRect.top;
      const bottomY = r.bottom - paperRect.top;
      const height  = bottomY - topY;
      // Paragraphs taller than a full page can't be helped — let them
      // overflow whichever page they start on rather than chasing them
      // forever.
      if (height >= CONTENT_PER_PAGE) continue;
      const pageIdx = Math.max(0, Math.floor((topY - PAGE_TOP_MARGIN) / PAGE_CYCLE));
      const contentBottom = pageIdx * PAGE_CYCLE + PAGE_TOP_MARGIN + CONTENT_PER_PAGE;
      if (bottomY > contentBottom) {
        const nextPageStart = (pageIdx + 1) * PAGE_CYCLE + PAGE_TOP_MARGIN;
        // Reference: previous <p>'s box bottom (or the paper's
        // padding-top when this is the first <p>). margin-top is
        // computed so that ``refBottom + marginTop = nextPageStart``,
        // and since this margin is always much larger than the prev
        // margin-bottom, collapse picks our value and lands the
        // paragraph exactly at nextPageStart.
        const refBottom = i > 0
          ? (ps[i - 1].getBoundingClientRect().bottom - paperRect.top)
          : PAGE_TOP_MARGIN;
        const newMarginTop = nextPageStart - refBottom;
        if (newMarginTop > 0) p.style.marginTop = `${newMarginTop}px`;
      }
    }
    // Pad the paper so the last page is always a full letter page,
    // matching how Word renders documents (no half-height final page).
    const lastP = ps[ps.length - 1];
    const lastBottom = lastP.getBoundingClientRect().bottom - paper.getBoundingClientRect().top;
    const pageCount = Math.max(1, Math.ceil(lastBottom / PAGE_CYCLE));
    paper.style.minHeight = `${pageCount * PAGE_CYCLE - PAGE_GAP}px`;
  }

  /**
   * Svelte use-action: attach to each .paper element. Runs adjustPagination
   * after mount and re-runs whenever the bound ``data`` value changes (i.e.,
   * when section content changes after a file open or convert).
   *
   * A ResizeObserver on the inner .prose div catches cases where layout
   * settles after the initial double-rAF (e.g., system font metrics that
   * differ from what the browser computed on first insertion). The observer
   * coalesces via ``rafId !== null`` guard so it won't cascade: if a run
   * is already scheduled it skips; the post-run DOM change is idempotent so
   * one extra observation cycle terminates cleanly.
   */
  function paginateAction(node, data) {
    let rafId = null;
    let obs = null;

    function paginate() {
      rafId = null;
      // If the element isn't in the document yet, getBoundingClientRect()
      // returns zeros and pagination produces wrong results. Retry next frame.
      if (!document.contains(node)) {
        rafId = requestAnimationFrame(paginate);
        return;
      }
      adjustPagination(node);
      // Reveal after pagination so the user never sees the unpaginated state.
      node.style.opacity = "";
    }

    // Called by ResizeObserver. Only schedules if nothing is already pending
    // so our own DOM writes (margin-top changes) don't cascade endlessly.
    // Fonts are guaranteed loaded by this point (resize fired after layout),
    // so no fonts.ready wait needed here.
    function scheduleResize() {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(paginate);
    }

    function run() {
      if (rafId !== null) cancelAnimationFrame(rafId);
      // First rAF lets Svelte flush and the browser insert the element.
      // Then we wait for document.fonts.ready so Google Fonts (Carlito,
      // JetBrains Mono, etc.) are applied before we measure text heights —
      // font load reflows text and can push content past the page-break band
      // if we measure with fallback metrics. On cache-hit fonts.ready
      // resolves in the next microtask (no visible delay). Second rAF after
      // fonts ensures layout is computed before getBoundingClientRect().
      rafId = requestAnimationFrame(() => {
        document.fonts.ready.then(() => {
          rafId = requestAnimationFrame(paginate);
        });
      });
    }

    const prose = node.querySelector(":scope > .prose");
    if (prose && typeof ResizeObserver !== "undefined") {
      obs = new ResizeObserver(scheduleResize);
      obs.observe(prose);
    }

    // Hide until pagination runs to prevent the clipping flash.
    node.style.opacity = "0";
    run();
    return {
      update(newData) {
        // Always hide briefly on content change so the user never sees the
        // un-paginated layout. The double-rAF (~33ms) is imperceptible.
        node.style.opacity = "0";
        run();
      },
      destroy() {
        if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
        if (obs) { obs.disconnect(); obs = null; }
      },
    };
  }

  // Flat list of (segment, tokenIndex) entries — one per Pine output
  // token in the segments stream, in document order. Position k in
  // this list corresponds to the k-th Pine chip in the BODY of the
  // converted RTF (post-prelude). Use ``segmentForPinePart`` rather
  // than indexing this directly: it handles the prelude offset.
  let pineChipInfo = $derived.by(() => {
    if (!result?.segments) return [];
    const flat = [];
    for (const seg of result.segments) {
      const toks = seg.pine_tokens || [];
      for (let i = 0; i < toks.length; i++) {
        flat.push({ segment: seg, tokenIndex: i });
      }
    }
    return flat;
  });

  // Same shape for the legacy/JDA side. Each segment contributes one
  // entry per source JDA token; the flat order matches document order
  // on the legacy pane.
  let legacyChipInfo = $derived.by(() => {
    if (!result?.segments) return [];
    const flat = [];
    for (const seg of result.segments) {
      const toks = seg.jda_tokens || [];
      for (let i = 0; i < toks.length; i++) {
        flat.push({ segment: seg, tokenIndex: i });
      }
    }
    return flat;
  });

  // Number of Pine chips at the start of the converted RTF that came
  // from the CreateVar prelude. These chips have no backing segment —
  // skip them when mapping a chip to its source.
  let preludePineCount = $derived(result?.prelude_pine_token_count || 0);

  // Pre-walk pineParts / legacyParts so we can ask "what is the chip
  // index of the part at position i?" without re-walking on every
  // hover. Non-chip positions get -1.
  let pineChipIndexByPartIdx = $derived.by(() => {
    const out = new Array(pineParts.length).fill(-1);
    let k = -1;
    for (let i = 0; i < pineParts.length; i++) {
      if (pineParts[i].kind === "pine") {
        k++;
        out[i] = k;
      }
    }
    return out;
  });

  let legacyChipIndexByPartIdx = $derived.by(() => {
    const out = new Array(legacyParts.length).fill(-1);
    let k = -1;
    for (let i = 0; i < legacyParts.length; i++) {
      if (legacyParts[i].kind === "legacy") {
        k++;
        out[i] = k;
      }
    }
    return out;
  });

  /**
   * Resolve a Pine chip's rendered ``partIdx`` to its source segment /
   * token. Returns null for prelude chips (which have no segment) and
   * for any chip beyond the segment count.
   */
  function segmentForPinePart(partIdx) {
    const chipIdx = pineChipIndexByPartIdx[partIdx];
    if (chipIdx < 0) return null;
    const offset = chipIdx - preludePineCount;
    if (offset < 0) return null;
    return pineChipInfo[offset] || null;
  }

  function formatClock(ms) {
    if (!ms) return "";
    try {
      return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  }

  function formatRecoveryTimestamp(ms) {
    if (!ms) return "";
    try {
      return new Date(ms).toLocaleString();
    } catch {
      return "";
    }
  }

  let autoSaveLabel = $derived.by(() => {
    if (autoSaveStatus === "saving") return "Auto-saving…";
    if (autoSaveStatus === "error") return "Auto-save failed";
    if (autoSaveStatus === "saved" && lastAutoSavedAt) {
      return `Auto-saved · ${formatClock(lastAutoSavedAt)}`;
    }
    return "";
  });

  let statsLine = $derived.by(() => {
    if (!result) return "Open an RTF to begin.";
    const t = result.totals;
    return `${t.jda_tokens} JDA → ${t.pine_tokens} Pine  ·  ${t.pattern_segments} pattern  ·  ${t.llm_segments} LLM  ·  ${t.unmatched_segments} unmatched  ·  ${result.issues.length} issue${result.issues.length === 1 ? "" : "s"}`;
  });

  // ── inline editing ──────────────────────────────────────────────

  function startEdit(partIndex, event) {
    const info = segmentForPinePart(partIndex);
    if (info) {
      // Pin the legacy-side highlight to this segment while editing.
      highlightedSegmentIndex = info.segment.index;
      // Show the hover info card anchored to the chip, so the
      // converter can see provenance / JDA source while typing. No
      // dwell delay here — the user has committed to editing.
      const target = event?.currentTarget;
      if (target?.getBoundingClientRect) {
        hoverInfo = buildHoverPayload(target.getBoundingClientRect(), info);
      }
    } else {
      // Prelude chip or other no-segment chip — no source info to show.
      hoverInfo = null;
      highlightedSegmentIndex = null;
    }
    editingChip = { partIndex };
  }

  function cancelEdit() {
    editingChip = null;
    hoverInfo = null;
    highlightedSegmentIndex = null;
  }

  function arraysEqual(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  /**
   * Commit a Pine-side inline edit. ``newText`` is the chip's
   * post-edit innerText; the scope auto-resolves to the narrowest
   * available (template if known, then audience, then global) so the
   * inline flow doesn't need a picker.
   */
  function commitEdit(innerText) {
    if (!editingChip) return;
    const partIndex = editingChip.partIndex;
    const cleaned = (innerText || "").replace(/ /g, " ").trim();
    // The contenteditable holds only the chip's *inner* text — the
    // literal "@[" and "]" delimiters are rendered as non-editable
    // bracket spans, so the user can't accidentally delete them.
    // Strip defensively in case a paste smuggled them in, then
    // re-wrap so the chip survives the next tokenize pass.
    let inner = cleaned.replace(/@\[([^\]]*)\]/g, "$1").replace(/^@\[/, "").replace(/\]+$/, "");
    const part = pineParts[partIndex];
    if (!part) { editingChip = null; return; }
    if (!inner) { editingChip = null; return; }
    const wrapped = `@[${inner}]`;
    if (wrapped === part.text) { editingChip = null; return; }
    applyPineEdit(partIndex, part, wrapped, defaultScope());
    editingChip = null;
    hoverInfo = null;
    highlightedSegmentIndex = null;
  }

  function applyPineEdit(partIndex, part, newText, scope) {
    // Snapshot before mutating so undo can restore.
    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys } }];
    redoStack = [];

    const sourceInfo = segmentForPinePart(partIndex);

    // Without a segment mapping or a usable source JDA, just splice
    // the one chip in place — no propagation, no persist.
    if (!sourceInfo || !sourceInfo.segment.jda_tokens?.length) {
      const rtf = result.converted.rtf;
      result.converted.rtf = rtf.slice(0, part.start) + newText + rtf.slice(part.end);
      return;
    }

    const sourceSeg = sourceInfo.segment;
    const originalPine = sourceSeg.pine_tokens?.[sourceInfo.tokenIndex] ?? part.text;

    const matches = [];
    for (let i = 0; i < pineParts.length; i++) {
      const p = pineParts[i];
      if (p.kind !== "pine") continue;
      if (i === partIndex) {
        matches.push({
          start: p.start, end: p.end,
          segIdx: sourceSeg.index, tokIdx: sourceInfo.tokenIndex,
        });
        continue;
      }
      const info = segmentForPinePart(i);
      if (!info) continue;
      if (!arraysEqual(info.segment.jda_tokens, sourceSeg.jda_tokens)) continue;
      if (info.segment.pine_tokens?.[info.tokenIndex] !== originalPine) continue;
      if (p.text !== originalPine) continue;
      matches.push({
        start: p.start, end: p.end,
        segIdx: info.segment.index, tokIdx: info.tokenIndex,
      });
    }

    let rtf = result.converted.rtf;
    for (let i = matches.length - 1; i >= 0; i--) {
      const m = matches[i];
      rtf = rtf.slice(0, m.start) + newText + rtf.slice(m.end);
    }
    result.converted.rtf = rtf;

    const matchKeys = matches.map((m) => `${m.segIdx}/${m.tokIdx}`);
    const baseKeys = { ...editedKeys };
    for (const k of matchKeys) baseKeys[k] = { status: "pending" };
    editedKeys = baseKeys;

    // Resolve scope: caller-supplied wins, else default to template if
    // we know the template name, else audience, else global.
    const effectiveScope = scope || defaultScope();
    if (!effectiveScope) return; // nothing to persist against

    const newPineTokens = [...(sourceSeg.pine_tokens || [])];
    if (sourceInfo.tokenIndex < newPineTokens.length) {
      newPineTokens[sourceInfo.tokenIndex] = newText;
    } else {
      newPineTokens.push(newText);
    }
    const propagatedCount = matches.length - 1;
    const noteBits = [`inline edit from converter_app (scope=${effectiveScope.kind})`];
    if (propagatedCount > 0) {
      noteBits.push(`propagated to ${propagatedCount} other chip${propagatedCount === 1 ? "" : "s"}`);
    }

    // Unwrap Svelte 5 reactive proxies before crossing the IPC bridge.
    // Electron's structured-clone refuses Svelte's $state proxies with
    // "An object could not be cloned." JSON-roundtrip strips them for
    // the array fields (newPineTokens is already plain JS).
    const payload = {
      org: result.org || "oba",
      scope: { kind: effectiveScope.kind, value: effectiveScope.value ?? "" },
      jda_tokens: Array.from(sourceSeg.jda_tokens || []),
      pine_tokens: Array.from(newPineTokens),
      source_template: result.template_name,
      segment_index: sourceSeg.index,
      note: noteBits.join("; "),
    };
    window.api.persistEdit(payload).then((r) => {
      const update = { ...editedKeys };
      const status = r?.ok
        ? { status: "saved" }
        : { status: "error", error: r?.error || "unknown" };
      for (const k of matchKeys) update[k] = status;
      editedKeys = update;
      if (!r?.ok) console.warn("persist failed:", r);
    }).catch((e) => {
      const update = { ...editedKeys };
      for (const k of matchKeys) {
        update[k] = { status: "error", error: String(e?.message || e) };
      }
      editedKeys = update;
      console.warn("persist threw:", e);
    });
  }

  function defaultScope() {
    if (result?.template_name) return { kind: "template", value: result.template_name };
    if (result?.audience) return { kind: "audience", value: result.audience };
    return { kind: "global", value: "" };
  }

  function editStateForPart(partIndex) {
    const info = segmentForPinePart(partIndex);
    if (!info) return null;
    const key = `${info.segment.index}/${info.tokenIndex}`;
    return editedKeys[key] || null;
  }

  // Compute the (x, y) for the hover card and its data payload given
  // a chip element's bounding rect + the segment info to show. Shared
  // between showInfoPine (dwell-triggered) and startEdit (pinned).
  function buildHoverPayload(rect, info) {
    const CARD_W = 460;
    const CARD_H_EST = 260;
    const margin = 12;
    let x = Math.min(rect.left, window.innerWidth - CARD_W - margin);
    x = Math.max(margin, x);
    let y;
    if (rect.bottom + 6 + CARD_H_EST > window.innerHeight) {
      y = Math.max(margin, rect.top - CARD_H_EST - 6);
    } else {
      y = rect.bottom + 6;
    }
    const key = `${info.segment.index}/${info.tokenIndex}`;
    return {
      x, y,
      segment: info.segment,
      tokenIndex: info.tokenIndex,
      editState: editedKeys[key] || null,
    };
  }

  // ── hover info ──────────────────────────────────────────────────
  // The info card is gated by a small dwell timer so it only appears
  // when the cursor actually stops on a chip — sweeping the mouse
  // across the prose no longer flashes the card over every chip it
  // passes.

  const HOVER_DELAY_MS = 400;
  let hoverTimer = null;

  function showInfoPine(partIndex, event) {
    if (editingChip) return;
    const info = segmentForPinePart(partIndex);
    if (!info) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const payload = buildHoverPayload(rect, info);
    if (hoverTimer) clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      hoverInfo = payload;
      highlightedSegmentIndex = info.segment.index;
      hoverTimer = null;
    }, HOVER_DELAY_MS);
  }

  function hideInfo() {
    // The editing flow pins the hover card and the highlight; don't
    // let a mouseleave from the underlying chip tear them down.
    if (editingChip) return;
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
    hoverInfo = null;
    highlightedSegmentIndex = null;
  }

  function provLabel(prov) {
    if (prov === "pattern") return "Pattern";
    if (prov === "llm")     return "LLM";
    if (prov === "unmatched") return "Unmatched";
    if (prov === "edit")    return "Edited";
    return prov || "—";
  }

  /**
   * Action: focus the chip's contenteditable span on mount and put
   * the caret at the end of its contents. Select-all would be too
   * destructive on a long Pine token where the user usually wants to
   * tweak one field, not retype the whole token.
   */
  function autofocus(node) {
    queueMicrotask(() => {
      try {
        node.focus();
        const range = document.createRange();
        range.selectNodeContents(node);
        range.collapse(false); // caret at end
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      } catch { /* ignore — non-editable target */ }
    });
  }
</script>

<div class="app">
  <!-- ── Header ───────────────────────────────────────────── -->
  <header class="toolbar">
    <!-- Left: all action buttons in one tight group -->
    <div class="actions">
      <button class="btn-icon" onclick={handleOpen} disabled={busy} title="Open (Ctrl+O)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>
        Open
      </button>
      <button class="btn-icon" onclick={handleSave} disabled={busy || !result?.converted?.rtf} title="Save (Ctrl+S)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
          <polyline points="17 21 17 13 7 13 7 21"/>
          <polyline points="7 3 7 8 15 8"/>
        </svg>
        Save
      </button>
      <button class="btn-icon" onclick={handleSaveAs} disabled={busy || !result?.converted?.rtf} title="Save As (Ctrl+Shift+S)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
          <polyline points="17 21 17 13 7 13 7 21"/>
          <polyline points="7 3 7 8 15 8"/>
          <line x1="12" y1="17" x2="15" y2="14"/>
          <polyline points="13 13 15 13 15 15"/>
        </svg>
        Save As
      </button>
      <span class="btn-sep"></span>
      <button class="btn-icon" onclick={handleUndo} disabled={!undoStack.length} title="Undo (Ctrl+Z)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 14 4 9 9 4"/>
          <path d="M20 20v-7a4 4 0 0 0-4-4H4"/>
        </svg>
        Undo
      </button>
      <button class="btn-icon" onclick={handleRedo} disabled={!redoStack.length} title="Redo (Ctrl+Y)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 14 20 9 15 4"/>
          <path d="M4 20v-7a4 4 0 0 1 4-4h12"/>
        </svg>
        Redo
      </button>
      <span class="btn-sep"></span>
      <span
        class="convert-wrap"
        title={!hasApiKey ? "Set your OpenAI API key in Settings before converting" : ""}
      >
        <button class="btn-primary" onclick={handleConvert} disabled={busy || !sourcePath || !hasApiKey}>
          {busy ? "Converting…" : "Convert"}
        </button>
      </span>
    </div>

    <!-- Right: file context + model -->
    <div class="toolbar-right">
      <div class="file-info">
        <span class="file-name" class:placeholder={!result?.template_name}>
          {result?.template_name || "No file loaded"}
        </span>
        {#if result?.audience}
          <span class="audience-pill">{result.audience}</span>
        {/if}
      </div>
      <span class="model-pill" title="LLM model — change under Settings">
        <span class="dot"></span>
        {model || "gpt-5.5"}
      </span>
    </div>
  </header>

  {#if error}
    <div class="error-bar">⚠ {error}</div>
  {/if}

  {#if recoveryOffer}
    <div class="recovery-bar">
      <span class="recovery-msg">
        ♻ Previous conversion found from
        <strong>{formatRecoveryTimestamp(recoveryOffer.savedAt)}</strong>.
        Restore your work?
      </span>
      <div class="recovery-actions">
        <button class="btn-ghost" onclick={dismissRecovery}>Discard</button>
        <button class="btn-primary" onclick={acceptRecovery}>Restore</button>
      </div>
    </div>
  {/if}

  <!-- ── Two-pane document split ──────────────────────────── -->
  <main>
    <section class="pane">
      <div class="pane-label">Legacy · JDA</div>
      <div class="pane-stage">
        {#if legacyParts.length === 0}
          <article class="paper paginated" data-section-height={PAGE_HEIGHT} style="max-width: 816px">
            <div class="empty">Open a legacy template file to begin.</div>
          </article>
        {:else}
          {#each legacySections as section}
            <article
              class="paper"
              class:paginated={section.heightPx >= PAGE_HEIGHT}
              data-section-height={section.heightPx}
              style="max-width: {section.widthPx}px"
              use:paginateAction={section.paragraphs}
            >
              <div class="prose">
                {#each section.paragraphs as paragraph}
                  <p>
                    {#each paragraph as item}
                      {#if item.kind === "chip"}
                        {@const part = legacyParts[item.idx]}
                        {#if part.kind === "legacy"}
                          {@const legChipIdx = legacyChipIndexByPartIdx[item.idx]}
                          {@const legInfo = legChipIdx >= 0 ? legacyChipInfo[legChipIdx] : null}
                          <span
                            class="token-legacy"
                            class:linked={legInfo && legInfo.segment.index === highlightedSegmentIndex}
                            title="JDA source token"
                          >{part.text}</span>
                        {:else}
                          <span class="token-pine static">{part.text}</span>
                        {/if}
                      {:else}
                        <span>{item.text}</span>
                      {/if}
                    {/each}
                  </p>
                {/each}
              </div>
            </article>
          {/each}
        {/if}
      </div>
    </section>
    <div class="pane-divider"></div>
    <section class="pane">
      <div class="pane-label">Converted · Pine</div>
      {#if busy}
        <div class="progress-bar" role="progressbar" aria-label="Converting"></div>
      {/if}
      <div class="pane-stage">
        {#if pineParts.length === 0}
          <article class="paper paginated" data-section-height={PAGE_HEIGHT} style="max-width: 816px">
            <div class="empty">
              {#if busy}
                <div class="spinner" aria-hidden="true"></div>
                <div>Converting…</div>
                <div class="empty-sub">Larger templates take a few minutes.</div>
              {:else if sourcePath}
                Click <strong>Convert</strong> to run the v2 pipeline.
              {:else}
                The converted output will appear here.
              {/if}
            </div>
          </article>
        {:else}
          {#each pineSections as section}
            <article
              class="paper"
              class:paginated={section.heightPx >= PAGE_HEIGHT}
              data-section-height={section.heightPx}
              style="max-width: {section.widthPx}px"
              use:paginateAction={section.paragraphs}
            >
              <div class="prose">
                {@render renderPineParagraphs(section.paragraphs)}
              </div>
            </article>
          {/each}
        {/if}
      </div>
    </section>
  </main>

  {#snippet renderPineParagraphs(paragraphs)}
    {#each paragraphs as paragraph}
      <p>
        {#each paragraph as item}
          {#if item.kind === "chip"}
            {@const i = item.idx}
            {@const part = pineParts[i]}
            {#if part.kind === "pine"}
              {#if editingChip?.partIndex === i}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <span class="token-pine editing"><span class="chip-bracket" aria-hidden="true">@[</span><span
                    class="chip-inner"
                    contenteditable="true"
                    spellcheck="false"
                    use:autofocus
                    onblur={(e) => commitEdit(e.currentTarget.innerText)}
                    onkeydown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        e.currentTarget.blur();
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        cancelEdit();
                      }
                    }}
                    onpaste={(e) => {
                      e.preventDefault();
                      const raw = e.clipboardData?.getData("text/plain") || "";
                      const text = raw.replace(/@\[([^\]]*)\]/g, "$1").trim();
                      document.execCommand("insertText", false, text);
                    }}
                  >{part.text.replace(/^@\[/, "").replace(/\]+$/, "")}</span><span class="chip-bracket" aria-hidden="true">]</span></span>
              {:else}
                {@const editState = editStateForPart(i)}
                <span
                  class="token-pine"
                  class:edited={editState?.status === "saved"}
                  class:saving={editState?.status === "pending"}
                  class:save-error={editState?.status === "error"}
                  role="button"
                  tabindex="0"
                  onclick={(e) => startEdit(i, e)}
                  onkeydown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      startEdit(i, e);
                    }
                  }}
                  onmouseenter={(e) => showInfoPine(i, e)}
                  onmouseleave={hideInfo}
                  onfocus={(e) => showInfoPine(i, e)}
                  onblur={hideInfo}
                >{part.text}</span>
              {/if}
            {:else}
              <span class="token-legacy" title="JDA source token (read-only)">{part.text}</span>
            {/if}
          {:else}
            <span>{item.text}</span>
          {/if}
        {/each}
      </p>
    {/each}
  {/snippet}

  <!-- ── Hover info card ─────────────────────────────────── -->
  {#if hoverInfo}
    <div class="info-card" style="left: {hoverInfo.x}px; top: {hoverInfo.y}px;">
      <div class="info-row">
        <span class="info-label">Provenance</span>
        <span class="info-value prov-{hoverInfo.segment.provenance}">
          {provLabel(hoverInfo.segment.provenance)}
        </span>
      </div>
      {#if hoverInfo.segment.pattern_id}
        <div class="info-row">
          <span class="info-label">Pattern</span>
          <span class="info-value mono">{hoverInfo.segment.pattern_id}</span>
        </div>
      {/if}
      {#if hoverInfo.segment.jda_tokens?.length}
        <div class="info-row">
          <span class="info-label">Source</span>
          <span class="info-value mono">{hoverInfo.segment.jda_tokens.join("  ")}</span>
        </div>
      {/if}
      {#if hoverInfo.segment.issues?.length}
        <div class="info-row info-issues">
          <span class="info-label">Issues</span>
          <span class="info-value">
            {#each hoverInfo.segment.issues as issue}
              <div class="issue-line">⚠ {issue.message || issue}</div>
            {/each}
          </span>
        </div>
      {/if}
      {#if hoverInfo.editState}
        <div class="info-row info-edit">
          <span class="info-label">Edit</span>
          <span class="info-value">
            {#if hoverInfo.editState.status === "saved"}
              <span class="status-saved">✓ persisted as suggestion</span>
            {:else if hoverInfo.editState.status === "pending"}
              <span class="status-pending">saving…</span>
            {:else}
              <span class="status-error">⚠ not saved: {hoverInfo.editState.error}</span>
            {/if}
          </span>
        </div>
      {/if}
    </div>
  {/if}

  <!-- ── Status bar ───────────────────────────────────────── -->
  <footer>
    <span class="hint">Click any <code>@[…]</code> chip to edit.</span>
    <span
      class="autosave"
      class:saving={autoSaveStatus === "saving"}
      class:saved={autoSaveStatus === "saved"}
      class:error={autoSaveStatus === "error"}
    >{autoSaveLabel}</span>
    <span class="stats">{statsLine}</span>
  </footer>

  <SettingsDialog bind:open={settingsOpen} onSaved={onSettingsSaved} />
  <MappingsDialog bind:open={mappingsOpen} />
  <HelpDialog     bind:open={helpOpen} />
</div>

<style>
  :global(*, *::before, *::after) { box-sizing: border-box; }
  :global(html, body, #app) { height: 100%; margin: 0; padding: 0; }
  :global(body) {
    /* Soft slate canvas so the white paper has space to "float"
     * without the harsh near-black contrast of pure #0a0a0f. */
    background:
      radial-gradient(1200px 600px at 20% -10%, #232841 0%, #161a2b 55%, #12141f 100%);
    color: #e6e8ef;
    font-family: "Inter", "SF Pro Text", system-ui, sans-serif;
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
  }

  .app {
    height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* ── Toolbar ─────────────────────────────────────────────
   * Single tight row: file context (left) — actions (center) —
   * model status (right). No app-name / brand mark — the OS menu
   * bar already shows the app title. */
  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 16px;
    background: rgba(17, 18, 26, 0.92);
    backdrop-filter: blur(6px);
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  }

  .actions {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 2px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 7px;
  }

  .convert-wrap {
    display: inline-flex;
    cursor: default;
  }

  .btn-sep {
    width: 1px;
    align-self: stretch;
    background: rgba(255, 255, 255, 0.08);
    margin: 3px 2px;
  }

  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
    min-width: 0;
  }

  .file-info {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .file-name {
    font-size: 13px;
    font-weight: 600;
    color: #e6e8ef;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 48ch;
  }
  .file-name.placeholder {
    color: #6b7488;
    font-weight: 400;
    font-style: italic;
  }
  .audience-pill {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 999px;
    background: rgba(94, 234, 212, 0.08);
    color: #5eead4;
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.4px;
    text-transform: lowercase;
  }

  .btn-icon,
  .btn-ghost,
  .btn-primary {
    border: none;
    padding: 5px 10px;
    border-radius: 5px;
    font-size: 12.5px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.12s, color 0.12s;
    line-height: 1.4;
  }
  .btn-icon {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: transparent;
    color: #9ca3b8;
    padding: 5px 10px;
  }
  .btn-icon svg { flex-shrink: 0; }
  .btn-icon:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.06);
    color: #e6e8ef;
  }
  .btn-icon:disabled { color: #3a3f4b; cursor: not-allowed; }
  .btn-ghost {
    background: transparent;
    color: #9ca3b8;
  }
  .btn-ghost:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.06);
    color: #e6e8ef;
  }
  .btn-ghost:disabled { color: #3a3f4b; cursor: not-allowed; }
  .btn-primary {
    background: #5eead4;
    color: #0a0a0f;
    font-weight: 600;
    padding: 5px 14px;
  }
  .btn-primary:hover:not(:disabled) { background: #2dd4bf; }
  .btn-primary:disabled {
    background: #1f3d3a;
    color: #5eead466;
    cursor: not-allowed;
  }

  .model-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 9px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.06);
    color: #9ca3b8;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    line-height: 1;
  }
  .model-pill .dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #5eead4;
    box-shadow: 0 0 6px rgba(94, 234, 212, 0.7);
  }
  .error-bar {
    background: rgba(248, 113, 113, 0.10);
    color: #f87171;
    border-bottom: 1px solid rgba(248, 113, 113, 0.30);
    padding: 8px 24px;
    font-size: 12.5px;
    font-family: "JetBrains Mono", monospace;
    white-space: pre-wrap;
  }

  /* Restore-prior-work prompt. Same vertical rhythm as the error bar
   * but in an amber/neutral tone — it's an offer, not a problem. */
  .recovery-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 24px;
    background: rgba(251, 191, 36, 0.08);
    color: #fde68a;
    border-bottom: 1px solid rgba(251, 191, 36, 0.28);
    font-size: 12.5px;
  }
  .recovery-msg { flex: 1; }
  .recovery-msg strong { color: #fbbf24; font-weight: 600; }
  .recovery-actions { display: inline-flex; gap: 6px; }
  .recovery-actions .btn-ghost { padding: 4px 10px; font-size: 12px; }
  .recovery-actions .btn-primary { padding: 4px 12px; font-size: 12px; }

  /* ── Two-pane stage ───────────────────────────────────── */
  main {
    flex: 1;
    display: flex;
    min-height: 0;
  }
  .pane { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .pane-divider {
    width: 1px;
    background: rgba(255, 255, 255, 0.04);
  }
  .pane-label {
    padding: 10px 28px 8px;
    font-family: "Inter", system-ui, sans-serif;
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 2.4px;
    text-transform: uppercase;
    color: #6b7488;
  }
  .pane-stage {
    flex: 1;
    overflow-y: auto;
    padding: 16px 16px 24px;
    gap: 16px;
    display: flex;
    flex-direction: column;
    align-items: center;
    scroll-behavior: smooth;
    /* Slim, theme-matched scrollbar so the dark frame stays clean
     * around the white paper. */
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.12) transparent;
  }
  .pane-stage::-webkit-scrollbar { width: 10px; }
  .pane-stage::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.10);
    border-radius: 999px;
    border: 2px solid transparent;
    background-clip: padding-box;
  }
  .pane-stage::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.20);
    background-clip: padding-box;
  }
  /* Real letter-paper proportions (8.5 × 11). The paper fills the pane
   * stage width up to letter-at-96dpi (816px); ``min-height`` makes
   * an empty document still look like a full letter page, and content
   * past that grows the paper rather than spilling onto the slate
   * background underneath. Page-boundary lines are painted via a
   * repeating gradient so a long doc reads as multiple "pages" the
   * way Google Docs does. */
  .paper {
    color: #1f2532;
    width: 100%;
    flex-shrink: 0;              /* never compress below content height */
    /* max-width and min-height are set as inline styles per section */
    padding: 0;
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.4) inset,
      0 2px 6px rgba(0, 0, 0, 0.22),
      0 26px 60px rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(0, 0, 0, 0.10);
    position: relative;
    background-color: #fbfaf6;
  }
  /* Page-break band only on letter-height sections (paginated class).
   * Short sections (e.g. CreateVar prelude boxes) have no page breaks.
   * min-height ensures the paper always fills at least one letter page
   * before adjustPagination() runs (avoids the text-clipping flash on
   * first render where the paper would otherwise collapse to content
   * height, placing the band inside the text). */
  .paper.paginated {
    min-height: 1056px;
    background-image: repeating-linear-gradient(
      to bottom,
      transparent 0,
      transparent 1056px,
      rgba(15, 18, 35, 0.92) 1056px,
      rgba(15, 18, 35, 0.92) 1072px);
  }

  .empty {
    color: #9a9a92;
    font-style: italic;
    font-family: "Crimson Pro", Georgia, serif;
    font-size: 16px;
    text-align: center;
    margin-top: 30%;
    padding: 0 24px;
    user-select: none;
  }
  .empty strong { color: #14b8a6; font-weight: 600; font-style: normal; }
  .empty-sub {
    margin-top: 6px;
    font-size: 13px;
    color: #b8b8ad;
  }

  /* Indeterminate progress bar that slides across the top of the
   * converted pane while a Convert is in flight. The bar lives between
   * the pane-label and the pane-stage so it can't be scrolled away. */
  .progress-bar {
    position: relative;
    height: 2px;
    margin: 0 16px 6px;
    background: rgba(94, 234, 212, 0.10);
    overflow: hidden;
    border-radius: 2px;
  }
  .progress-bar::after {
    content: "";
    position: absolute;
    top: 0; bottom: 0;
    width: 30%;
    background: linear-gradient(90deg,
      rgba(94, 234, 212, 0),
      #5eead4,
      rgba(94, 234, 212, 0));
    animation: progress-slide 1.25s cubic-bezier(0.4, 0, 0.6, 1) infinite;
  }
  @keyframes progress-slide {
    0%   { left: -35%; }
    100% { left: 100%; }
  }

  /* Spinner shown inside the converted paper while the pipeline runs
   * and no prior content exists to keep on screen. CSS-only, no
   * dependency. */
  .spinner {
    width: 36px;
    height: 36px;
    margin: 0 auto 12px;
    border: 3px solid rgba(20, 184, 166, 0.18);
    border-top-color: #14b8a6;
    border-radius: 50%;
    animation: spinner-rotate 0.9s linear infinite;
  }
  @keyframes spinner-rotate {
    to { transform: rotate(360deg); }
  }
  /* Respect reduced-motion preference — kill the animation but keep
   * the visual so users still see something is in progress. */
  @media (prefers-reduced-motion: reduce) {
    .progress-bar::after { animation: none; left: 0; width: 100%; opacity: 0.5; }
    .spinner { animation: none; border-top-color: rgba(20, 184, 166, 0.6); }
  }

  .prose {
    /* Letter-style margins now live here (not on .paper) so the
     * prelude header can sit flush at the paper's top edge and the
     * body content begins immediately after — instead of being
     * pushed down by inline prelude paragraphs. Top margin is small
     * (24px) so the body starts near the top of the page; sides and
     * bottom keep the standard 1in. */
    padding: 24px 96px 96px;
    /* Word 365 default body — Calibri 11pt where licensed (Windows),
     * Carlito (Google Fonts, Calibri-metric-compatible) everywhere
     * else, then OS fallbacks. Matching metrics across platforms is
     * what keeps the pagination math (and the rendered text width)
     * consistent. */
    font-family: "Calibri", "Carlito", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;                     /* Word default body */
    line-height: 1.08;                   /* Word default line spacing */
    color: #1f2532;
    font-feature-settings: "kern", "liga";
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    cursor: text;
    hyphens: auto;
    overflow-wrap: anywhere;
    word-break: normal;
  }
  .prose p {
    margin: 0 0 8pt 0;                   /* Word default: 8pt after, 0 before */
    white-space: pre-wrap;
    min-height: 1.08em;                  /* empty paragraphs render as one
                                            blank line, matching the body
                                            line-height */
    orphans: 2;
    widows: 2;
  }
  .prose p:last-child { margin-bottom: 0; }
  /* Cyan-tinted selection on the paper — matches the app accent and
   * reads cleanly against the warm white background. */
  .prose ::selection {
    background: rgba(94, 234, 212, 0.32);
    color: #0a1f1d;
  }

  /* ── Token chips (Docs-style inline highlights) ─────────────
   * Chips are plain inline elements — no border, no outline ring,
   * no inline-block box. They paint a tinted background highlight
   * over monospace colored text so they read as a marked-up token
   * in the prose flow instead of a chunky widget sitting on top
   * of the line. */
  .token-legacy, .token-pine {
    font-family: "JetBrains Mono", "SF Mono", monospace;
    font-size: 0.92em;          /* close to body so chips don't shrink
                                   below the surrounding prose */
    font-weight: 600;
    padding: 0;                 /* No padding — chip background is
                                   exactly its text width, so a chip
                                   at end-of-line can't extend past
                                   the right page margin. */
    border-radius: 2px;
    border: none;
    box-shadow: none;
    /* Pure inline so the prose line-box drives the layout — chip
     * padding doesn't grow the line. A long chip (e.g., a deeply
     * nested Pine token with no whitespace) wraps inside itself
     * rather than punching past the right margin: ``overflow-wrap:
     * anywhere`` forces a break at any character when overflow
     * would otherwise occur. ``white-space: normal`` is needed so
     * that overflow-wrap actually takes effect (it's a no-op under
     * ``nowrap`` or the inherited ``pre-wrap``). */
    white-space: normal;
    overflow-wrap: anywhere;
    transition: background-color 0.12s, color 0.12s;
  }
  .token-legacy {
    color: #9a3412;
    background: rgba(251, 146, 60, 0.18);
    cursor: default;
    transition: background 0.12s, color 0.12s, box-shadow 0.12s;
  }
  /* Glow applied to JDA-source chips whose segment matches the Pine
   * chip currently being hovered or edited on the converted side.
   * Makes the source-to-target mapping legible at a glance. */
  .token-legacy.linked {
    background: rgba(251, 146, 60, 0.45);
    color: #7c2d12;
    box-shadow: 0 0 0 1px rgba(251, 146, 60, 0.7);
  }
  .token-pine {
    color: #0f766e;
    background: rgba(94, 234, 212, 0.22);
    cursor: pointer;
  }
  .token-pine.static { cursor: default; }
  .token-pine:not(.static):hover {
    background: rgba(94, 234, 212, 0.38);
    color: #115e59;
  }
  .token-pine.edited {
    color: #92400e;
    background: rgba(251, 191, 36, 0.30);
  }
  .token-pine.edited:hover {
    background: rgba(251, 191, 36, 0.45);
  }
  .token-pine.saving {
    color: #b08512;
    background: rgba(252, 211, 77, 0.22);
  }
  .token-pine.save-error {
    color: #b91c1c;
    background: rgba(248, 113, 113, 0.22);
  }
  .token-pine.save-error:hover {
    background: rgba(248, 113, 113, 0.34);
  }

  /* ── Inline edit chip state ───────────────────────────── */
  .token-pine.editing {
    background: rgba(20, 184, 166, 0.18);
    color: #134e4a;
    outline: 1px solid #14b8a6;
    outline-offset: 1px;
    /* Browser caret + selection styles propagate into the inner
     * editable span; surface them at the chip level too so clicking
     * the chip body lands the user in the editable area. */
    cursor: text;
  }
  /* The "@[" and "]" delimiters are rendered as non-editable spans
   * on either side of the inner contenteditable so the user can
   * tweak the token contents but never accidentally delete the
   * brackets that keep the chip recognised by tokenize() on commit. */
  .chip-bracket {
    user-select: none;
    pointer-events: none;
    opacity: 0.7;
  }
  .chip-inner {
    outline: none;
    caret-color: #14b8a6;
    /* Inherits font, size, color, weight from .token-pine.editing. */
  }
  .chip-inner::selection {
    background: rgba(20, 184, 166, 0.40);
    color: #042f2e;
  }

  /* ── Hover info card ──────────────────────────────────── */
  .info-card {
    position: fixed;
    z-index: 30;
    pointer-events: none;
    background: #11121a;
    border: 1px solid #2a2d3a;
    border-radius: 6px;
    padding: 10px 12px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
    font-size: 12px;
    width: 460px;
    max-width: calc(100vw - 24px);
    max-height: calc(100vh - 80px);
    /* The card itself is pointer-events:none (so it doesn't intercept
     * hover state on the chip underneath), but very tall content can
     * still scroll if the user hovers a chip-with-many-issues.
     * pointer-events:auto on the body lets the scrollbar work. */
    overflow-y: auto;
    line-height: 1.5;
    color: #e6e8ef;
  }
  .info-row {
    display: flex;
    gap: 10px;
    margin-bottom: 4px;
    /* min-width on the row + value cell so long unbreakable mono
     * tokens wrap inside the value column instead of stretching
     * the whole card past max-width. */
    min-width: 0;
  }
  .info-row:last-child { margin-bottom: 0; }
  .info-label {
    color: #6b7488;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    font-size: 10px;
    font-weight: 600;
    flex: 0 0 70px;
    padding-top: 1px;
  }
  .info-value {
    color: #e6e8ef;
    flex: 1 1 auto;
    min-width: 0;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .info-value.mono {
    font-family: "JetBrains Mono", "SF Mono", monospace;
    font-size: 11.5px;
    color: #9ca3b8;
    /* Mono tokens commonly have no whitespace; allow any character to
     * be a break opportunity so a single JDA path doesn't push the
     * card past its max-width. */
    overflow-wrap: anywhere;
    word-break: break-word;
    white-space: normal;
  }
  .prov-pattern  { color: #5eead4; }
  .prov-llm      { color: #c084fc; }
  .prov-unmatched { color: #f87171; }
  .prov-edit     { color: #fbbf24; }
  .info-issues .info-value { color: #f87171; }
  .issue-line { font-size: 11.5px; margin-bottom: 2px; }
  .status-saved   { color: #fbbf24; }
  .status-pending { color: #9ca3b8; font-style: italic; }
  .status-error   { color: #f87171; }

  /* ── Footer ───────────────────────────────────────────── */
  footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 9px 24px;
    background: rgba(17, 18, 26, 0.92);
    border-top: 1px solid rgba(255, 255, 255, 0.06);
  }
  .hint { color: #9ca3b8; font-size: 12px; }
  .hint code {
    background: rgba(94, 234, 212, 0.12);
    color: #5eead4;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
  }
  .stats {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: #6b7488;
  }

  /* Auto-save status. Sits centered in the footer; reserves no space
   * when idle (empty string from autoSaveLabel) so the layout doesn't
   * jump as state changes. */
  .autosave {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: #6b7488;
    transition: color 0.12s;
  }
  .autosave.saving { color: #9ca3b8; font-style: italic; }
  .autosave.saved  { color: #5eead4; }
  .autosave.error  { color: #f87171; }
</style>

<script>
  // Renderer side. Talks to the Electron main process exclusively
  // through ``window.api`` (defined in electron/preload.cjs). No
  // Node, no fs — everything that touches the disk or spawns a
  // process is brokered through the typed IPC surface.

  import SettingsDialog  from "./SettingsDialog.svelte";
  import MappingsDialog  from "./MappingsDialog.svelte";
  import AgenciesDialog  from "./AgenciesDialog.svelte";
  import HelpDialog      from "./HelpDialog.svelte";

  /** @type {string} The model id surfaced as the toolbar status. */
  let model = $state("gpt-5.5");
  /** @type {boolean} Whether an API key has been saved in settings. */
  let hasApiKey = $state(false);

  /** @type {Array<{ id: string, description: string }>} Agencies for the picker. */
  let agencies = $state([]);
  /** @type {string} Which agency new conversions are run against. Persisted.
   * Empty until the user picks/adds one — there is no default agency. */
  let selectedAgency = $state("");
  /** @type {string} Last real agency selection — restored when the user opens
   * the "Manage agencies…" sentinel so the picker doesn't lose its value. */
  let prevAgency = $state("");
  /** @type {boolean} Manage Agencies dialog open flag. */
  let agenciesOpen = $state(false);
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
   *   agency: string,
   *   totals: {
   *     jda_tokens: number, pine_tokens: number,
   *     pattern_segments: number, llm_segments: number,
   *     unmatched_segments: number, edit_segments: number,
   *   },
   *   segments: Array<object>,
   * }}
   * Current conversion bundle, or null when nothing's loaded.
   */
  let result = $state(null);

  /** Async work indicator for the Convert action. */
  let busy = $state(false);

  /** Transient confirmation shown after a CreateVar correction is learned. */
  let learnedNote = $state("");

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

  // The "add a new mapping" dialog. Null when closed; otherwise holds
  // the in-progress form (pine var text + the legacy var to tie it to)
  // and a persist status.
  /** @type {null | { pineText: string, legacyToken: string, status: null|"saving"|"error", error: string }} */
  let addingVar = $state(null);

  // Where a right-click chose to insert a new Pine var. ``rtfOffset`` is
  // the splice point in the converted RTF; x/y/height position the
  // visible insertion caret (viewport coords). Null when no point is set.
  /** @type {null | { rtfOffset: number, x: number, y: number, height: number }} */
  let insertAt = $state(null);

  // The right-click context menu in the Pine pane. Null when closed.
  /** @type {null | { x: number, y: number }} */
  let pineContextMenu = $state(null);

  // Chip→segment remap. ``segmentForPinePart`` maps the k-th rendered
  // Pine chip to ``pineChipInfo[k]``, which assumes the rendered chips
  // line up 1:1 with the conversion's segments. Structural edits
  // (insert / delete) break that, so this array overrides the mapping:
  // ``chipMap[r]`` is the pineChipInfo index for the r-th post-prelude
  // chip, or null for a manually-inserted chip with no segment. Null
  // here means "no structural edits yet — use the identity mapping".
  /** @type {null | Array<number|null>} */
  let chipMap = $state(null);

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

  // Hydrate the toolbar model pill, API key presence, and last-used agency
  // from saved settings on mount. Also load the list of configured agencies
  // for the picker. Only restore the saved agency if it still exists.
  $effect(() => {
    window.api.listAgencies().then((list) => {
      if (Array.isArray(list)) agencies = list;
      return window.api.getSettings();
    }).then((s) => {
      if (!s) return;
      if (s.model) model = s.model;
      hasApiKey = !!s.api_key;
      if (s.agency && agencies.some((a) => a.id === s.agency)) {
        selectedAgency = s.agency;
        prevAgency = s.agency;
      }
    }).catch(() => { /* ignore — toolbar just shows defaults, picker empty */ });
  });

  // Persist the picker choice so it's the default next launch. Fire-and-forget.
  function onAgencyChange() {
    if (selectedAgency) window.api.setSettings({ agency: selectedAgency }).catch(() => {});
  }

  // The picker doubles as the entry point to agency management: choosing the
  // sentinel "Manage agencies…" option opens the dialog and reverts the
  // <select> to whatever was selected before (it's not a real choice).
  function onAgencyPick() {
    if (selectedAgency === "__manage__") {
      selectedAgency = prevAgency;
      agenciesOpen = true;
      return;
    }
    prevAgency = selectedAgency;
    onAgencyChange();
  }

  // Called by the Manage Agencies dialog after any add/rename/delete. Refresh
  // the picker list and reconcile the active selection.
  function onAgenciesChanged({ agencies: list, deleted, renamed } = {}) {
    if (Array.isArray(list)) agencies = list;
    if (deleted && selectedAgency === deleted) {
      selectedAgency = "";
      prevAgency = "";
      window.api.setSettings({ agency: "" }).catch(() => {});
    }
    if (renamed && selectedAgency === renamed.from) {
      selectedAgency = renamed.to;
      prevAgency = renamed.to;
      onAgencyChange();
    }
  }

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
    chipMap = null;
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
      agency: selectedAgency,
      totals: {
        jda_tokens: 0, pine_tokens: 0,
        suggestion_segments: 0, llm_segments: 0,
        unmatched_segments: 0, edit_segments: 0,
      },
      segments: [],
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
    if (!selectedAgency) {
      error = "Select an agency before converting.";
      return;
    }
    error = "";
    busy = true;
    editingChip = null;
    hoverInfo = null;
    editedKeys = {};
    undoStack = [];
    redoStack = [];
    chipMap = null;
    try {
      result = await window.api.convertRtf({ path: sourcePath, agency: selectedAgency });
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
    flushPineSync();   // fold any pending editor keystrokes into the RTF first
    const currentPineTokens = pineParts
      .filter((p) => p.kind === "pine")
      .map((p) => p.text);
    const r = await window.api.refreshPrelude({
      rtf: result.converted.rtf,
      pine_tokens: currentPineTokens,
      agency: result.agency || selectedAgency,
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
    redoStack = [...redoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
    undoStack = undoStack.slice(0, -1);
    result.converted.rtf = prev.rtf;
    editedKeys = prev.editedKeys;
    chipMap = prev.chipMap ?? null;
  }

  function handleRedo() {
    if (!redoStack.length) return;
    const next = redoStack[redoStack.length - 1];
    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
    redoStack = redoStack.slice(0, -1);
    result.converted.rtf = next.rtf;
    editedKeys = next.editedKeys;
    chipMap = next.chipMap ?? null;
  }

  function acceptRecovery() {
    if (!recoveryOffer?.snapshot) return;
    const snap = recoveryOffer.snapshot;
    if (snap.result) result = snap.result;
    if (snap.editedKeys) editedKeys = snap.editedKeys;
    chipMap = snap.chipMap ?? null;
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
          chipMap,
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
    return stripRtfWithMap(s).text;
  }

  /**
   * Like ``stripRtf`` but also returns ``map`` — an array where
   * ``map[k]`` is the index in ``s`` of the source token that produced
   * the k-th output character, and ``map[text.length]`` is ``s.length``.
   * Used to translate a caret position in the rendered (stripped) prose
   * back to a safe insertion offset in the raw RTF. Every emit below is
   * a single character, so each push corresponds to one output char.
   */
  function stripRtfWithMap(s) {
    if (!s) return { text: "", map: [0] };
    const len = s.length;
    let out = "";
    const map = [];
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
          out += next; map.push(i); i += 2; continue;
        }
        if (next === "'") {
          const hex = s.substr(i + 2, 2);
          if (/^[0-9a-fA-F]{2}$/.test(hex)) {
            out += String.fromCharCode(parseInt(hex, 16));
            map.push(i); i += 4; continue;
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
            map.push(i);
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
          if (repl !== undefined) { out += repl; map.push(i); }
          i = j; continue;
        }
        i += 2; continue;
      }
      if (c === "\r" || c === "\n") { i++; continue; }
      out += c; map.push(i);
      i++;
    }
    map.push(len);   // sentinel: caret at end of text → end of slice
    return { text: out, map };
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

      // RTF hard page break: \page but not \pagebb, \pard, etc. Without
      // this, \page is swallowed by stripRtf as an unknown control word
      // and the author's page break never appears in the canvas.
      if (ch === "\\" && text.slice(i + 1, i + 5) === "page" && !/[a-zA-Z]/.test(text[i + 5] || "")) {
        if (i > proseStart) {
          parts.push({ kind: "prose", text: stripRtf(text.slice(proseStart, i)), start: proseStart, end: i });
        }
        const end = i + 5 + (text[i + 5] === " " ? 1 : 0);
        parts.push({ kind: "page-break", start: i, end });
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
    // A pending hard page break (\page): applied to the next paragraph
    // that actually receives content (so blank lines between the break
    // and the next text don't soak it up). A section break starts a new
    // paper, which is itself a page boundary, so it cancels any pending
    // \page to avoid an extra blank page (e.g. the \page \par \sect run
    // that legacy templates commonly emit).
    let pendingBreak = false;
    const applyPending = () => { if (pendingBreak) { cur.__breakBefore = true; pendingBreak = false; } };
    parts.forEach((part, idx) => {
      if (part.kind === "section-break") {
        paragraphs.push({ __sectionBreak: true });
        cur = [];
        paragraphs.push(cur);
        pendingBreak = false;
        return;
      }
      if (part.kind === "page-break") { pendingBreak = true; return; }
      if (part.kind === "prose") {
        const lines = (part.text || "").split("\n");
        // ``lineStart`` is the offset of this line within the part's
        // stripped text (counting the \n separators consumed). It lets
        // a caret in this rendered line map back to a raw-RTF offset.
        let acc = 0;
        lines.forEach((line, i) => {
          if (i > 0) {
            cur = [];
            paragraphs.push(cur);
            acc += 1;   // the "\n" that split() removed
          }
          if (line.length > 0) {
            applyPending();
            cur.push({ kind: "prose", text: line, partIdx: idx, lineStart: acc });
          } else if (i > 0) {
            // A blank line. Anchor the empty paragraph to its raw-RTF
            // position so the mapper can click it and type — text is
            // inserted at this offset (no reconstruction of existing
            // content, so formatting elsewhere is never disturbed). If a
            // later chip lands in this paragraph it's no longer empty and
            // the anchor simply goes unused.
            cur.__anchor = { partIdx: idx, lineStart: acc };
          }
          acc += line.length;
        });
      } else {
        applyPending();
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

  // The converted pane is a SINGLE paper holding every section (the legacy
  // pane gets one paper per section instead). So it must paginate at the
  // document's dominant page size — the tallest/widest section — not
  // ``pineSections[0]``. Many templates open with a short letterhead/title
  // section (e.g. heightPx 396); keying off section 0 would wrongly mark the
  // whole multi-page document "short" and skip pagination entirely.
  let pinePageHeight = $derived(
    pineSections.length
      ? Math.max(...pineSections.map((s) => s.heightPx || 0))
      : PAGE_HEIGHT,
  );
  let pinePageWidth = $derived(
    pineSections.length
      ? Math.max(...pineSections.map((s) => s.widthPx || 0))
      : 816,
  );

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
      const pageIdx = Math.max(0, Math.floor((topY - PAGE_TOP_MARGIN) / PAGE_CYCLE));
      // Authored hard page / section break: force this paragraph to the top
      // of the next page (unless it already sits at a page top, so we never
      // emit a gratuitous blank page). Checked before the tall-paragraph
      // skip so an over-long block still *starts* on a fresh page.
      if (p.dataset && p.dataset.breakBefore === "1") {
        const pageTop = pageIdx * PAGE_CYCLE + PAGE_TOP_MARGIN;
        if (topY > pageTop + 1) {
          const nextPageStart = (pageIdx + 1) * PAGE_CYCLE + PAGE_TOP_MARGIN;
          const refBottom = i > 0
            ? (ps[i - 1].getBoundingClientRect().bottom - paperRect.top)
            : PAGE_TOP_MARGIN;
          const newMarginTop = nextPageStart - refBottom;
          if (newMarginTop > 0) p.style.marginTop = `${newMarginTop}px`;
        }
        continue;
      }
      // Paragraphs taller than a full page can't be helped — let them
      // overflow whichever page they start on rather than chasing them
      // forever.
      if (height >= CONTENT_PER_PAGE) continue;
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
    // With no structural edits the mapping is the identity; once a chip
    // has been inserted/deleted, chipMap re-aligns positions to segments.
    const mapped = chipMap
      ? (offset < chipMap.length ? chipMap[offset] : null)
      : offset;
    if (mapped == null) return null;
    return pineChipInfo[mapped] || null;
  }

  // A mutable copy of the current chip→segment map, materialised to the
  // identity mapping the first time a structural edit needs one.
  function materializeChipMap() {
    if (chipMap) return [...chipMap];
    return Array.from({ length: pineChipInfo.length }, (_, k) => k);
  }

  // Post-prelude flat chip index for an RTF offset: how many body Pine
  // chips start before ``off``. Used to keep chipMap aligned with splices.
  function postPreludeIndexForOffset(off) {
    let pineBefore = 0;
    for (const p of pineParts) {
      if (p.kind === "pine" && p.start < off) pineBefore++;
    }
    return Math.max(0, pineBefore - preludePineCount);
  }

  // Distinct legacy (JDA) tokens present in this document, in first-seen
  // order. Feeds the "tie to a legacy var" picker in the add-mapping
  // dialog so the converter can pick a real source rather than retype it.
  let legacyVarOptions = $derived.by(() => {
    const seen = new Set();
    const out = [];
    for (const seg of result?.segments || []) {
      for (const t of seg.jda_tokens || []) {
        if (t && !seen.has(t)) { seen.add(t); out.push(t); }
      }
    }
    return out;
  });

  // Best-guess legacy var for an insertion offset: the legacy source of
  // the Pine chip nearest the insertion point. Used to pre-select the
  // dropdown — a var added next to existing content most likely shares
  // that content's source. Returns null if nothing nearby has a source.
  function mostLikelyLegacyForOffset(off) {
    let best = null;
    let bestDist = Infinity;
    for (let i = 0; i < pineParts.length; i++) {
      const p = pineParts[i];
      if (p.kind !== "pine") continue;
      const jda = segmentForPinePart(i)?.segment?.jda_tokens;
      if (!jda?.length) continue;
      const dist = Math.min(Math.abs(p.start - off), Math.abs(p.end - off));
      if (dist < bestDist) { bestDist = dist; best = jda[0]; }
    }
    return best;
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
    return `${t.jda_tokens} JDA → ${t.pine_tokens} Pine  ·  ${t.suggestion_segments} saved  ·  ${t.llm_segments} LLM  ·  ${t.unmatched_segments} unmatched`;
  });

  // ── inline editing ──────────────────────────────────────────────

  function startEdit(partIndex, event) {
    const info = segmentForPinePart(partIndex);
    // Pin the legacy-side highlight to this segment while editing (only
    // if there is one — manually-added chips have no backing segment).
    highlightedSegmentIndex = info ? info.segment.index : null;
    // Anchor the info card to the chip so the converter sees the source
    // while typing, and so Delete is reachable. No dwell delay — the
    // user has committed to editing. For no-segment chips this is a bare
    // card carrying only the Delete control.
    const target = event?.currentTarget;
    hoverInfo = target?.getBoundingClientRect
      ? buildHoverPayload(target.getBoundingClientRect(), info)
      : null;
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
    // bracket spans. Preserve a full ``@[...]`` the mapper typed verbatim;
    // only wrap bare inner text. (We used to strip any brackets and
    // re-wrap, which silently deleted ``@[]`` a mapper deliberately added.)
    const part = pineParts[partIndex];
    if (!part) { editingChip = null; return; }
    if (!cleaned) { editingChip = null; return; }
    const wrapped = /^@\[[\s\S]*\]$/.test(cleaned) ? cleaned : `@[${cleaned}]`;
    if (wrapped === part.text) { editingChip = null; return; }
    // CreateVar (prelude) chips are not body fill-points — they have no JDA
    // source segment. A correction here teaches the agency's role config so
    // future preludes generate it correctly (and across templates), rather
    // than persisting a per-token suggestion.
    if (/CreateVar/.test(wrapped) || /CreateVar/.test(part.text)) {
      learnCreateVarEdit(partIndex, part, wrapped);
    } else {
      applyPineEdit(partIndex, part, wrapped, defaultScope());
    }
    editingChip = null;
    hoverInfo = null;
    highlightedSegmentIndex = null;
  }

  /**
   * Apply a corrected CreateVar chip: splice it in locally for immediate
   * feedback, then teach the agency config so the correction generalizes.
   * The config is the durable store — the next conversion (and the
   * save-time prelude regeneration) reflect what was learned here.
   */
  function learnCreateVarEdit(partIndex, part, newText) {
    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
    redoStack = [];
    const rtf = result.converted.rtf;
    result.converted.rtf = rtf.slice(0, part.start) + newText + rtf.slice(part.end);

    const agency = result.agency || selectedAgency;
    if (!agency) return;
    window.api.learnCreateVars({ agency, tokens: [newText] }).then((r) => {
      if (r?.error) {
        console.warn("learnCreateVars failed:", r.error);
      } else if (r?.entities?.length) {
        error = "";
        learnedNote = `Learned CreateVar for ${r.entities.join(", ")} — future templates will generate it.`;
      }
    }).catch((e) => console.warn("learnCreateVars failed:", e));
  }

  // ── Contenteditable editor for the converted (Pine) pane ─────────────
  // The pane is one contenteditable surface so editing feels like a real
  // text editor: click anywhere, native caret/selection, type and delete
  // freely. Chips (@[…] / %[…]) are rendered as atomic contenteditable=false
  // widgets. Edits are synced back into the RTF (the source of truth) by a
  // prefix/suffix diff over the strip map, so untouched content — including
  // all its RTF formatting — is preserved; only the changed span is spliced.

  /** @type {HTMLElement | null} The contenteditable surface. */
  let pineEditorEl = $state(null);
  /** @type {HTMLElement | null} The .paper page wrapping the editor (paginated). */
  let pinePaperEl = $state(null);
  /** Bump to force an imperative rebuild of the editor DOM from the model. */
  let editorVersion = $state(0);
  // Non-reactive editor bookkeeping.
  let pineDisplay = { text: "", map: [] };  // displayed text + per-char → RTF offset
  let pineTyping = false;                     // suppress rebuild while the user types
  let pineSyncTimer = null;
  let lastBuiltRtf = null;
  let lastBuiltVersion = -1;

  function bumpEditor() { editorVersion++; }

  /** Build (text, map): the displayed text and, per char, its RTF offset.
   *  Chips contribute their token text mapped to the chip's start offset;
   *  prose contributes its stripped text mapped through the per-part strip map. */
  function computePineDisplay(rtf) {
    const parts = tokenize(rtf);
    let text = "";
    const map = [];
    for (const part of parts) {
      if (part.kind === "pine" || part.kind === "legacy") {
        for (const ch of part.text) { map.push(part.start); text += ch; }
      } else if (part.kind === "prose") {
        const { map: pmap } = stripRtfWithMap(rtf.slice(part.start, part.end));
        for (let k = 0; k < part.text.length; k++) {
          map.push(part.start + (pmap[k] ?? 0));
          text += part.text[k];
        }
      }
    }
    map.push(rtf.length);   // boundary; clamped to the body on use
    return { text, map, parts };
  }

  function recomputePineDisplay() {
    const rtf = result?.converted?.rtf || "";
    const d = computePineDisplay(rtf);
    pineDisplay = { text: d.text, map: d.map };
  }

  function makeChipSpan(part, pi) {
    const span = document.createElement("span");
    span.className = part.kind === "pine" ? "token-pine" : "token-legacy";
    if (part.kind === "pine") {
      const es = editStateForPart(pi);
      if (es?.status === "saved") span.classList.add("edited");
      else if (es?.status === "pending") span.classList.add("saving");
      else if (es?.status === "error") span.classList.add("save-error");
    } else {
      span.classList.add("static");
    }
    span.contentEditable = "false";
    span.dataset.partIdx = String(pi);
    span.textContent = part.text;
    return span;
  }

  /** Imperatively (re)build the editor DOM from the current RTF as <p> block
   *  paragraphs (one per source paragraph) with chips as atomic widgets.
   *  Blocks let the legacy pagination logic push paragraphs off page gaps. */
  function buildPineEditor() {
    const el = pineEditorEl;
    if (!el) return;
    const rtf = result?.converted?.rtf || "";
    const d = computePineDisplay(rtf);
    pineDisplay = { text: d.text, map: d.map };

    el.replaceChildren();
    let block = document.createElement("p");
    el.appendChild(block);
    // This pane is a single paper, so a section OR page break can't start a
    // new <article> the way the legacy pane does — instead it forces a page
    // break before the next paragraph that holds content. ``adjustPagination``
    // honors ``data-break-before`` by pushing that paragraph to the next page,
    // leaving the gap the page-break band paints into. A blank block (the
    // <br>-only paragraphs below) doesn't consume the pending break, so the
    // common \page \par \sect run collapses to a single break.
    let pendingBreak = false;
    let blockHasContent = false;
    const ensureBreakBlock = () => {
      if (!pendingBreak) return;
      if (blockHasContent) { block = document.createElement("p"); el.appendChild(block); blockHasContent = false; }
      block.dataset.breakBefore = "1";
      pendingBreak = false;
    };
    d.parts.forEach((part, pi) => {
      if (part.kind === "section-break" || part.kind === "page-break") { pendingBreak = true; return; }
      if (part.kind === "pine" || part.kind === "legacy") {
        ensureBreakBlock();
        block.appendChild(makeChipSpan(part, pi));
        blockHasContent = true;
      } else if (part.kind === "prose") {
        const segs = part.text.split("\n");
        segs.forEach((seg, i) => {
          if (i > 0) { block = document.createElement("p"); el.appendChild(block); blockHasContent = false; }
          if (seg) { ensureBreakBlock(); block.appendChild(document.createTextNode(seg)); blockHasContent = true; }
        });
      }
    });
    // Empty paragraphs need a <br> so they have height and a caret target.
    el.querySelectorAll("p").forEach((p) => { if (!p.firstChild) p.appendChild(document.createElement("br")); });
    repaginatePine();
  }

  /** Serialize one block's inline content: text verbatim, chips as their
   *  token, <br> placeholders ignored. */
  function serializeInline(node) {
    let s = "";
    for (const c of node.childNodes) {
      if (c.nodeType === Node.TEXT_NODE) s += c.textContent;
      else if (c.nodeType === Node.ELEMENT_NODE) {
        if (c.dataset && c.dataset.partIdx !== undefined) s += c.textContent;
        else if (c.tagName === "BR") { /* placeholder — ignore */ }
        else s += serializeInline(c);
      }
    }
    return s;
  }

  /** Serialize the editor to the same coordinate space as ``pineDisplay.text``:
   *  each top-level block is a line, joined by "\n". */
  function serializePineEditor() {
    const el = pineEditorEl;
    if (!el) return "";
    const lines = [];
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) lines.push(node.textContent);
      else if (node.nodeType === Node.ELEMENT_NODE) lines.push(serializeInline(node));
    }
    return lines.join("\n");
  }

  /** Re-run the legacy pagination pass over the Pine page once layout + fonts
   *  have settled (mirrors paginateAction's timing so heights measure right). */
  function repaginatePine() {
    const paper = pinePaperEl;
    if (!paper) return;
    requestAnimationFrame(() => {
      const run = () => requestAnimationFrame(() => { try { adjustPagination(paper); } catch { /* layout not ready */ } });
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(run);
      else run();
    });
  }

  /** Convert typed plain text to RTF: escape control chars, newlines → \par. */
  function textToRtf(s) {
    let out = "";
    for (const ch of s) {
      if (ch === "\n") { out += "\\par\n"; continue; }
      const code = ch.codePointAt(0);
      if (ch === "\\") out += "\\\\";
      else if (ch === "{") out += "\\{";
      else if (ch === "}") out += "\\}";
      else if (code < 128) out += ch;
      else if (code <= 0xffff) out += `\\u${code > 32767 ? code - 65536 : code}?`;
      else out += "?";
    }
    return out;
  }

  /** Diff the live editor against the known display text and splice the one
   *  changed region into the RTF. Robust for the single contiguous edit a
   *  keystroke/selection-replace produces. */
  function syncPineEditor() {
    if (!pineEditorEl) return;
    const dom = serializePineEditor();
    const old = pineDisplay.text;
    if (dom === old) return;

    const minLen = Math.min(dom.length, old.length);
    let p = 0;
    while (p < minLen && dom[p] === old[p]) p++;
    let s = 0;
    while (s < minLen - p && dom[dom.length - 1 - s] === old[old.length - 1 - s]) s++;

    const oldStart = p;
    const oldEnd = old.length - s;
    const inserted = dom.slice(p, dom.length - s);

    const rtf = result.converted.rtf;
    const brace = rtf.lastIndexOf("}");
    const bodyEnd = brace < 0 ? rtf.length : brace;
    const map = pineDisplay.map;
    let rtfStart = Math.min(map[oldStart] ?? bodyEnd, bodyEnd);
    let rtfEnd = Math.min(map[oldEnd] ?? bodyEnd, bodyEnd);
    if (rtfEnd < rtfStart) rtfEnd = rtfStart;

    undoStack = [...undoStack, { rtf, editedKeys: { ...editedKeys }, chipMap }];
    redoStack = [];
    result.converted.rtf = rtf.slice(0, rtfStart) + textToRtf(inserted) + rtf.slice(rtfEnd);
    // The DOM already reflects the edit; refresh the display model so the
    // next diff is incremental — but DON'T rebuild the DOM (caret stays).
    recomputePineDisplay();
    lastBuiltRtf = result.converted.rtf;   // keep the rebuild effect quiet
    // Page reflow is driven by the ResizeObserver on the editor — no need
    // to paginate here (avoids double work on every keystroke).
  }

  function flushPineSync() {
    if (pineSyncTimer) { clearTimeout(pineSyncTimer); pineSyncTimer = null; }
    if (pineEditorEl && pineTyping) syncPineEditor();
  }

  function onEditorFocus() {
    pineTyping = true;
    // Make Enter produce <p> blocks (not <div>) so the structure stays
    // consistent with how buildPineEditor lays out paragraphs.
    try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch { /* unsupported */ }
  }
  function onEditorBlur() {
    flushPineSync();
    pineTyping = false;
    lastBuiltRtf = result?.converted?.rtf || "";   // DOM already matches; skip rebuild
  }
  function onEditorInput() {
    if (pineSyncTimer) clearTimeout(pineSyncTimer);
    pineSyncTimer = setTimeout(() => { pineSyncTimer = null; syncPineEditor(); }, 140);
  }
  function onEditorKeydown(e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "y")) {
      // Let the app-level undo/redo own history (it snapshots full RTF).
      e.preventDefault();
      flushPineSync();
      if (e.key === "y" || e.shiftKey) handleRedo(); else handleUndo();
      // Rebuild now even though the surface is focused — undo/redo is a
      // deliberate jump, so a caret reset is expected.
      buildPineEditor();
      lastBuiltRtf = result?.converted?.rtf || "";
    }
  }
  function onEditorPaste(e) {
    e.preventDefault();
    const txt = (e.clipboardData?.getData("text/plain") || "");
    document.execCommand("insertText", false, txt);
  }
  function onEditorClick(e) {
    const chip = e.target.closest?.("[data-part-idx]");
    if (!chip) return;
    const partIdx = Number(chip.dataset.partIdx);
    const part = pineParts[partIdx];
    if (!part || part.kind !== "pine") return;   // legacy chips are read-only
    const r = chip.getBoundingClientRect();
    editingChip = { partIndex: partIdx, x: r.left, y: r.bottom + 4, text: part.text };
  }
  function onEditorHover(e) {
    const chip = e.target.closest?.("[data-part-idx]");
    if (!chip) { hideInfo(); return; }
    const partIdx = Number(chip.dataset.partIdx);
    if (pineParts[partIdx]?.kind === "pine") showInfoPine(partIdx, e);
  }

  /** Commit the floating chip editor. Reuses the suggestion / CreateVar-learn
   *  paths so accepted edits still persist and generalize. */
  function commitChipEditor(raw) {
    const ec = editingChip;
    editingChip = null;
    if (!ec) return;
    const part = pineParts[ec.partIndex];
    if (!part) return;
    const cleaned = (raw ?? "").trim();
    if (!cleaned) return;
    const wrapped = /^@\[[\s\S]*\]$/.test(cleaned) ? cleaned : `@[${cleaned}]`;
    if (wrapped === part.text) return;
    if (/CreateVar/.test(wrapped) || /CreateVar/.test(part.text)) {
      learnCreateVarEdit(ec.partIndex, part, wrapped);
    } else {
      applyPineEdit(ec.partIndex, part, wrapped, defaultScope());
    }
    bumpEditor();
  }

  // Rebuild the editor DOM on external RTF changes (convert, undo/redo, chip
  // edit, add-var, prelude refresh) — but never mid-typing, so the caret is
  // only disturbed by deliberate non-typing actions.
  $effect(() => {
    editorVersion;                          // explicit rebuild signal
    const rtf = result?.converted?.rtf || "";   // track external changes
    if (pineTyping) return;
    queueMicrotask(() => {
      if (!pineEditorEl) return;
      if (result?.converted?.rtf === lastBuiltRtf && editorVersion === lastBuiltVersion) return;
      buildPineEditor();
      lastBuiltRtf = result?.converted?.rtf || "";
      lastBuiltVersion = editorVersion;
    });
  });

  // Keep the Pine page paginated as its content height settles — fonts
  // loading, the contenteditable reflowing, edits adding/removing lines.
  // Mirrors the legacy pane's ResizeObserver so the two paginate identically.
  // The rafId guard stops adjustPagination's own margin writes from cascading.
  $effect(() => {
    const paper = pinePaperEl;
    const prose = pineEditorEl;
    if (!paper || !prose || typeof ResizeObserver === "undefined") return;
    let rafId = null;
    const schedule = () => {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        try { adjustPagination(paper); } catch { /* not laid out yet */ }
      });
    };
    const obs = new ResizeObserver(schedule);
    obs.observe(prose);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
    else schedule();
    return () => { if (rafId !== null) cancelAnimationFrame(rafId); obs.disconnect(); };
  });

  /**
   * Escape a plain-text run for RTF: backslash and braces are control
   * characters; non-ASCII is emitted as a ``\uN?`` unicode escape (signed
   * 16-bit, per the RTF spec) with a ``?`` ASCII fallback char.
   */
  function escapeRtf(s) {
    let out = "";
    for (const ch of s) {
      const code = ch.codePointAt(0);
      if (ch === "\\") out += "\\\\";
      else if (ch === "{") out += "\\{";
      else if (ch === "}") out += "\\}";
      else if (code < 128) out += ch;
      else if (code <= 0xffff) out += `\\u${code > 32767 ? code - 65536 : code}?`;
      else out += "?";
    }
    return out;
  }

  /**
   * Commit a free-text edit of a prose run back into the converted RTF.
   * The rendered run is a stripped slice of the raw RTF; we use the
   * strip map to find the exact raw byte range the visible line occupies
   * and replace it with the re-escaped new text. Inline formatting inside
   * an edited run is not preserved (acceptable for plain body text), but
   * surrounding RTF structure is untouched.
   */
  function commitProseEdit(partIndex, lineStart, originalText, rawInner) {
    const newText = (rawInner ?? "").replace(/\u00a0/g, " ").replace(/\r/g, "");
    if (newText === originalText) return;
    const part = pineParts[partIndex];
    if (!part || part.kind !== "prose") return;

    const rawSlice = result.converted.rtf.slice(part.start, part.end);
    const { map } = stripRtfWithMap(rawSlice);
    const a = Math.min(lineStart, map.length - 1);
    const b = Math.min(lineStart + originalText.length, map.length - 1);
    const rtfStart = part.start + map[a];
    const rtfEnd = part.start + map[b];

    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
    redoStack = [];
    const rtf = result.converted.rtf;
    result.converted.rtf = rtf.slice(0, rtfStart) + escapeRtf(newText) + rtf.slice(rtfEnd);
  }

  /**
   * Insert free text typed on a blank line. Pure insertion at the line's
   * anchored raw-RTF offset — existing content (and its formatting) is
   * untouched. After commit the line re-tokenizes into a normal prose run.
   */
  function commitBlankInsert(anchor, rawText, el) {
    const text = (rawText ?? "").replace(/\u00a0/g, " ").replace(/[\r\n]/g, "");
    if (el) el.textContent = "";   // clear the editable span; the RTF is the source of truth
    if (!text) return;
    const part = pineParts[anchor.partIdx];
    if (!part || part.kind !== "prose") return;

    const { map } = stripRtfWithMap(result.converted.rtf.slice(part.start, part.end));
    const idx = Math.min(anchor.lineStart, map.length - 1);
    const off = part.start + map[idx];

    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
    redoStack = [];
    const rtf = result.converted.rtf;
    result.converted.rtf = rtf.slice(0, off) + escapeRtf(text) + rtf.slice(off);
  }

  /**
   * Every Pine chip in the current document that shares the chip at
   * ``partIndex``'s JDA source AND currently shows the same Pine text —
   * i.e. the chip plus its identical twins. Each entry is
   * ``{ start, end, segIdx, tokIdx }`` and the origin chip is always
   * first. Shared by inline edit and delete so a change to one
   * occurrence propagates to the rest.
   */
  function matchingPineChips(partIndex, sourceInfo) {
    const sourceSeg = sourceInfo.segment;
    const originalPine =
      sourceSeg.pine_tokens?.[sourceInfo.tokenIndex] ?? pineParts[partIndex]?.text;
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
    return matches;
  }

  function applyPineEdit(partIndex, part, newText, scope) {
    // Snapshot before mutating so undo can restore.
    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
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
    const matches = matchingPineChips(partIndex, sourceInfo);

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
      agency: result.agency || selectedAgency,
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

  /**
   * Delete a Pine var entirely: remove the chip (and its identical
   * twins) from the document and persist a *drop* — an empty-rewrite
   * suggestion tied to the chip's legacy var — so the same JDA source
   * produces nothing on the next conversion. Chips with no JDA source
   * (e.g. prelude) are spliced out locally but not persisted.
   */
  function deletePineVar(partIndex) {
    undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
    redoStack = [];

    const sourceInfo = segmentForPinePart(partIndex);
    const part = pineParts[partIndex];

    if (!sourceInfo || !sourceInfo.segment.jda_tokens?.length) {
      if (part) {
        // Drop this single chip from the map and the document.
        const m = materializeChipMap();
        const flat = postPreludeIndexForOffset(part.start);
        if (flat >= 0 && flat < m.length) { m.splice(flat, 1); chipMap = m; }
        const rtf = result.converted.rtf;
        result.converted.rtf = rtf.slice(0, part.start) + rtf.slice(part.end);
      }
      editingChip = null;
      hoverInfo = null;
      highlightedSegmentIndex = null;
      return;
    }

    const sourceSeg = sourceInfo.segment;
    const matches = matchingPineChips(partIndex, sourceInfo);

    // Re-align the chip→segment map: remove every deleted chip's slot,
    // descending so earlier indices stay valid as later ones are cut.
    const m = materializeChipMap();
    const flatIdxs = matches
      .map((mt) => postPreludeIndexForOffset(mt.start))
      .sort((a, b) => b - a);
    for (const fi of flatIdxs) {
      if (fi >= 0 && fi < m.length) m.splice(fi, 1);
    }
    chipMap = m;

    // Splice each occurrence out, right-to-left so earlier offsets stay
    // valid as later ones are removed.
    let rtf = result.converted.rtf;
    for (let i = matches.length - 1; i >= 0; i--) {
      const mt = matches[i];
      rtf = rtf.slice(0, mt.start) + rtf.slice(mt.end);
    }
    result.converted.rtf = rtf;

    const matchKeys = matches.map((m) => `${m.segIdx}/${m.tokIdx}`);
    const baseKeys = { ...editedKeys };
    for (const k of matchKeys) baseKeys[k] = { status: "pending" };
    editedKeys = baseKeys;

    editingChip = null;
    hoverInfo = null;
    highlightedSegmentIndex = null;

    const effectiveScope = defaultScope();
    if (!effectiveScope) return;

    const droppedCount = matches.length - 1;
    const noteBits = [`deleted via converter_app (drop, scope=${effectiveScope.kind})`];
    if (droppedCount > 0) {
      noteBits.push(`removed ${droppedCount} other occurrence${droppedCount === 1 ? "" : "s"}`);
    }

    const payload = {
      agency: result.agency || selectedAgency,
      scope: { kind: effectiveScope.kind, value: effectiveScope.value ?? "" },
      jda_tokens: Array.from(sourceSeg.jda_tokens || []),
      pine_tokens: [],   // empty rewrite = drop the mapping
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
      if (!r?.ok) console.warn("persist (drop) failed:", r);
    }).catch((e) => {
      const update = { ...editedKeys };
      for (const k of matchKeys) {
        update[k] = { status: "error", error: String(e?.message || e) };
      }
      editedKeys = update;
      console.warn("persist (drop) threw:", e);
    });
  }

  // ── add a new mapping (right-click → insert at caret) ───────────
  /**
   * Translate a viewport point into a safe insertion descriptor for the
   * converted RTF: ``{ rtfOffset, x, y, height }``. Resolves the caret
   * under the cursor, finds the rendered chip/prose element it lands in
   * (tagged with ``data-part-idx``), and maps it to a raw-RTF offset
   * that never lands inside an RTF control word. Returns null if the
   * point isn't over insertable content.
   */
  /** Serialized length a node contributes (mirrors serializeInline). */
  function serializedLen(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent.length;
    if (node.nodeType === Node.ELEMENT_NODE) {
      if (node.dataset && node.dataset.partIdx !== undefined) return node.textContent.length;
      if (node.tagName === "BR") return 0;
      let s = 0;
      for (const c of node.childNodes) s += serializedLen(c);
      return s;
    }
    return 0;
  }

  /** Displayed-text index of a (node, offset) caret in the editor — the same
   *  coordinate space as ``pineDisplay.text`` (top-level blocks joined by \n). */
  function displayedIndexOf(targetNode, targetOffset) {
    const el = pineEditorEl;
    if (!el) return null;
    let idx = 0;
    let found = null;

    const walk = (node) => {
      if (found != null) return;
      if (node === targetNode) {
        if (node.nodeType === Node.TEXT_NODE) { found = idx + targetOffset; return; }
        // element caret: sum serialized length of the first targetOffset children
        for (let k = 0; k < targetOffset && k < node.childNodes.length; k++) idx += serializedLen(node.childNodes[k]);
        found = idx; return;
      }
      if (node.nodeType === Node.TEXT_NODE) { idx += node.textContent.length; return; }
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.dataset && node.dataset.partIdx !== undefined) { idx += node.textContent.length; return; }
        if (node.tagName === "BR") return;
        for (const c of node.childNodes) { walk(c); if (found != null) return; }
      }
    };

    const blocks = el.childNodes;
    for (let b = 0; b < blocks.length && found == null; b++) {
      if (b > 0) idx += 1;            // the "\n" joining top-level blocks
      walk(blocks[b]);
    }
    return found;
  }

  /** Resolve a click point in the editor to an RTF insertion offset (+ caret
   *  pixel rect), via the native caret position mapped through pineDisplay. */
  function insertPointFromXY(clientX, clientY) {
    if (!pineEditorEl) return null;
    let node = null, offset = 0, range = null;
    if (document.caretRangeFromPoint) {
      range = document.caretRangeFromPoint(clientX, clientY);
      if (range) { node = range.startContainer; offset = range.startOffset; }
    } else if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(clientX, clientY);
      if (pos) { node = pos.offsetNode; offset = pos.offset; range = document.createRange(); range.setStart(node, offset); }
    }
    if (!node || !pineEditorEl.contains(node)) return null;

    const dispIdx = displayedIndexOf(node, offset);
    if (dispIdx == null) return null;
    const map = pineDisplay.map;
    const brace = (result.converted.rtf || "").lastIndexOf("}");
    const bodyEnd = brace < 0 ? (result.converted.rtf || "").length : brace;
    const rtfOffset = Math.min(map[Math.min(dispIdx, map.length - 1)] ?? bodyEnd, bodyEnd);

    let cx = clientX, cy = clientY, ch = 16;
    try {
      const cr = (range?.getClientRects?.()[0]) || range?.getBoundingClientRect?.();
      if (cr && cr.height) { cx = cr.left; cy = cr.top; ch = cr.height; }
    } catch { /* fall back to cursor coords */ }
    return { rtfOffset, x: cx, y: cy, height: ch };
  }

  function onPineContextMenu(e) {
    if (!result?.converted?.rtf || editingChip) return;
    flushPineSync();               // fold pending keystrokes so the map is current
    const point = insertPointFromXY(e.clientX, e.clientY);
    if (!point) return;            // not over insertable content
    e.preventDefault();
    insertAt = point;
    pineContextMenu = { x: e.clientX, y: e.clientY };
  }

  function closeContextMenu() {
    pineContextMenu = null;
    // Only drop the caret if the add dialog isn't taking over.
    if (!addingVar) insertAt = null;
  }

  function chooseAddVarHere() {
    pineContextMenu = null;
    if (!insertAt) return;
    addingVar = {
      pineText: "",
      legacyToken: mostLikelyLegacyForOffset(insertAt.rtfOffset) || legacyVarOptions[0] || "",
      status: null,
      error: "",
    };
  }

  function cancelAddVar() {
    addingVar = null;
    insertAt = null;
  }

  /**
   * Insert the new Pine var at the chosen caret offset and persist the
   * legacy→Pine mapping as a scoped suggestion for future conversions.
   */
  function confirmAddVar() {
    if (!addingVar || !insertAt) return;
    const legacy = (addingVar.legacyToken || "").trim();
    const raw = (addingVar.pineText || "").trim();

    if (!legacy) {
      addingVar = { ...addingVar, status: "error", error: "Pick a legacy variable to tie this to." };
      return;
    }
    if (!raw) {
      addingVar = { ...addingVar, status: "error", error: "Enter the Pine variable text." };
      return;
    }
    const effectiveScope = defaultScope();
    if (!effectiveScope) {
      addingVar = { ...addingVar, status: "error", error: "No scope available to save against." };
      return;
    }

    // Preserve a full ``@[...]`` verbatim; only wrap bare text.
    const chip = /^@\[[\s\S]*\]$/.test(raw) ? raw : `@[${raw}]`;
    const off = insertAt.rtfOffset;
    addingVar = { ...addingVar, status: "saving", error: "" };
    const payload = {
      agency: result.agency || selectedAgency,
      scope: { kind: effectiveScope.kind, value: effectiveScope.value ?? "" },
      jda_tokens: [legacy],
      pine_tokens: [chip],
      source_template: result.template_name,
      note: `new mapping inserted via converter_app (scope=${effectiveScope.kind})`,
    };
    // Persist first; only splice into the document once the mapping is
    // saved, so a persist failure leaves the form open to retry without
    // inserting the chip twice.
    window.api.persistEdit(payload).then((r) => {
      if (r?.ok) {
        undoStack = [...undoStack, { rtf: result.converted.rtf, editedKeys: { ...editedKeys }, chipMap }];
        redoStack = [];
        // Re-align the map: the new chip occupies a fresh slot with no
        // backing segment (null) at its post-prelude position.
        const m = materializeChipMap();
        const p = postPreludeIndexForOffset(off);
        m.splice(Math.min(Math.max(p, 0), m.length), 0, null);
        chipMap = m;
        const rtf = result.converted.rtf;
        result.converted.rtf = rtf.slice(0, off) + chip + rtf.slice(off);
        addingVar = null;
        insertAt = null;
        bumpEditor();   // rebuild the editor so the new chip renders
      } else {
        addingVar = { ...addingVar, status: "error", error: r?.error || "save failed" };
      }
    }).catch((e) => {
      addingVar = { ...addingVar, status: "error", error: String(e?.message || e) };
    });
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
    // A manually-added chip has no backing segment — return a bare card
    // (position only) so the editing flow can still offer Delete.
    if (!info) {
      return { x, y, segment: null, tokenIndex: -1, editState: null };
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
    if (prov === "suggestion") return "Saved";
    if (prov === "llm")        return "LLM";
    if (prov === "unmatched")  return "Unmatched";
    if (prov === "edit")       return "Edited";
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
      <select
        class="agency-select"
        class:placeholder={!selectedAgency}
        bind:value={selectedAgency}
        onchange={onAgencyPick}
        disabled={busy}
        title="Which agency this template belongs to"
      >
        <option value="" disabled>Select agency</option>
        {#each agencies as a (a.id)}
          <option value={a.id} title={a.description}>{a.description || a.id}</option>
        {/each}
        <option value="__manage__">⚙ Manage agencies…</option>
      </select>
      <span
        class="convert-wrap"
        title={!hasApiKey ? "Set your OpenAI API key in Settings before converting"
             : !selectedAgency ? "Select an agency first" : ""}
      >
        <button class="btn-primary" onclick={handleConvert} disabled={busy || !sourcePath || !hasApiKey || !selectedAgency}>
          {busy ? "Converting…" : "Convert"}
        </button>
      </span>
    </div>

    <!-- Right: mappings + file context + model -->
    <div class="toolbar-right">
      <button class="btn-icon" onclick={() => (mappingsOpen = true)} title="Saved mappings">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2"/>
          <line x1="3" y1="9" x2="21" y2="9"/>
          <line x1="3" y1="15" x2="21" y2="15"/>
          <line x1="12" y1="3" x2="12" y2="21"/>
        </svg>
        Mappings
      </button>
      <span class="btn-sep"></span>
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
  {#if learnedNote}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="learned-bar" onclick={() => (learnedNote = "")}>✓ {learnedNote}</div>
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
                  <p data-break-before={paragraph.__breakBefore ? "1" : null}>
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
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="pane-stage" oncontextmenu={onPineContextMenu}>
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
          <article
            class="paper"
            class:paginated={pinePageHeight >= PAGE_HEIGHT}
            data-section-height={pinePageHeight}
            style="max-width: {pinePageWidth}px"
            bind:this={pinePaperEl}
          >
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <div
              class="prose pine-editor"
              contenteditable="true"
              spellcheck="false"
              bind:this={pineEditorEl}
              onfocusin={onEditorFocus}
              onfocusout={onEditorBlur}
              oninput={onEditorInput}
              onkeydown={onEditorKeydown}
              onpaste={onEditorPaste}
              onclick={onEditorClick}
              onmouseover={onEditorHover}
              onmouseout={hideInfo}
            ></div>
          </article>
        {/if}
      </div>
    </section>
  </main>

  <!-- ── Floating chip editor ─────────────────────────────────
       Clicking a Pine chip in the editor opens this; editing the full
       @[…] token commits through the suggestion / CreateVar-learn paths. -->
  {#if editingChip}
    <!-- svelte-ignore a11y_autofocus -->
    <div class="chip-editor" style="left: {editingChip.x}px; top: {editingChip.y}px;">
      <input
        class="chip-editor-input mono"
        value={editingChip.text}
        spellcheck="false"
        autofocus
        onkeydown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commitChipEditor(e.currentTarget.value); }
          else if (e.key === "Escape") { e.preventDefault(); editingChip = null; }
        }}
        onblur={(e) => commitChipEditor(e.currentTarget.value)}
      />
    </div>
  {/if}

  <!-- ── Hover info card ─────────────────────────────────── -->
  {#if hoverInfo}
    <div class="info-card" class:interactive={!!editingChip} style="left: {hoverInfo.x}px; top: {hoverInfo.y}px;">
      {#if hoverInfo.segment}
        <div class="info-row">
          <span class="info-label">Origin</span>
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
      {:else if editingChip}
        <div class="info-row">
          <span class="info-label">Origin</span>
          <span class="info-value prov-edit">Added</span>
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
      {#if editingChip}
        <div class="info-row info-actions">
          <!-- mousedown + preventDefault so clicking this doesn't blur
               the contenteditable (which would commit an edit) before
               the delete lands. -->
          <button
            class="info-delete-btn"
            onmousedown={(e) => { e.preventDefault(); deletePineVar(editingChip.partIndex); }}
            title="Remove this Pine variable from the document"
          >✕ Delete</button>
        </div>
      {/if}
    </div>
  {/if}

  <!-- ── Insertion caret (shows where a new var will land) ── -->
  {#if insertAt}
    <div
      class="insert-caret"
      style="left: {insertAt.x}px; top: {insertAt.y}px; height: {insertAt.height}px;"
      aria-hidden="true"
    ></div>
  {/if}

  <!-- ── Pine pane right-click menu ────────────────────────── -->
  {#if pineContextMenu}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div class="context-backdrop" oncontextmenu={(e) => { e.preventDefault(); closeContextMenu(); }} onclick={closeContextMenu}>
      <div class="context-menu" style="left: {pineContextMenu.x}px; top: {pineContextMenu.y}px;">
        <button class="context-item" onclick={chooseAddVarHere}>Add variable here…</button>
      </div>
    </div>
  {/if}

  <!-- ── Add-mapping dialog ───────────────────────────────── -->
  {#if addingVar}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div class="modal-backdrop" onclick={cancelAddVar}>
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
      <div class="add-modal" onclick={(e) => e.stopPropagation()}>
        <h3 class="add-title">Add a Pine variable</h3>
        <p class="add-sub">
          Inserts at the cursor and ties it to a legacy variable. Also saved
          as a <strong>{defaultScope()?.kind}</strong> suggestion so the same
          mapping applies on future conversions.
        </p>

        <label class="add-field">
          <span class="add-label">Legacy variable</span>
          <select
            class="add-input add-select mono"
            bind:value={addingVar.legacyToken}
            onkeydown={(e) => { if (e.key === "Escape") cancelAddVar(); }}
          >
            {#if !legacyVarOptions.length}
              <option value="" disabled>No legacy variables in this document</option>
            {/if}
            {#each legacyVarOptions as opt}
              <option value={opt}>{opt}</option>
            {/each}
          </select>
        </label>

        <label class="add-field">
          <span class="add-label">Pine variable</span>
          <div class="add-pine-wrap">
            <span class="add-bracket">@[</span>
            <input
              class="add-input mono add-pine-input"
              placeholder="Complainant.first.NameFirst"
              spellcheck="false"
              bind:value={addingVar.pineText}
              onkeydown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); confirmAddVar(); }
                else if (e.key === "Escape") cancelAddVar();
              }}
            />
            <span class="add-bracket">]</span>
          </div>
        </label>

        {#if addingVar.status === "error"}
          <div class="add-error">⚠ {addingVar.error}</div>
        {/if}

        <div class="add-actions">
          <button class="btn-ghost" onclick={cancelAddVar} disabled={addingVar.status === "saving"}>Cancel</button>
          <button class="btn-primary" onclick={confirmAddVar} disabled={addingVar.status === "saving"}>
            {addingVar.status === "saving" ? "Saving…" : "Add mapping"}
          </button>
        </div>
      </div>
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
  <MappingsDialog bind:open={mappingsOpen} agency={selectedAgency} />
  <AgenciesDialog bind:open={agenciesOpen} selected={selectedAgency} onchange={onAgenciesChanged} />
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
  /* Agency picker — matches the toolbar button language (Inter, 12.5px,
     no uppercase/mono) rather than standing out. */
  .agency-select {
    appearance: none;
    cursor: pointer;
    padding: 5px 26px 5px 10px;
    border-radius: 5px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.06);
    color: #e6e8ef;
    font-family: inherit;
    font-size: 12.5px;
    font-weight: 500;
    line-height: 1.4;
    /* Custom caret so the native one doesn't clash with the dark chrome. */
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%239ca3b8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
    background-repeat: no-repeat;
    background-position: right 9px center;
  }
  .agency-select:hover:not(:disabled) { background-color: rgba(255, 255, 255, 0.06); }
  .agency-select:disabled { opacity: 0.5; cursor: default; }
  .agency-select option { background: #11121a; color: #e6e8ef; font-family: inherit; }
  /* Dim the control while it still shows the "Select agency" placeholder. */
  .agency-select.placeholder { color: #9ca3b8; }

  .error-bar {
    background: rgba(248, 113, 113, 0.10);
    color: #f87171;
    border-bottom: 1px solid rgba(248, 113, 113, 0.30);
    padding: 8px 24px;
    font-size: 12.5px;
    font-family: "JetBrains Mono", monospace;
    white-space: pre-wrap;
  }
  .learned-bar {
    background: rgba(94, 234, 212, 0.10);
    color: #5eead4;
    border-bottom: 1px solid rgba(94, 234, 212, 0.30);
    padding: 8px 24px;
    font-size: 12.5px;
    font-family: "JetBrains Mono", monospace;
    cursor: pointer;
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
  /* Free-text prose runs are editable — clicking gives a native caret.
   * Keep them visually flush with the document; the caret is the only
   * affordance, with a faint highlight while focused so the mapper sees
   * where typing will land. */
  .prose-run {
    outline: none;
    caret-color: #0a1f1d;
    border-radius: 2px;
  }
  .prose-run:focus {
    background: rgba(94, 234, 212, 0.10);
  }
  /* A blank line is an empty editable span stretched across the row so the
     whole line is a click target — click it to drop a caret and type. */
  .blank-line {
    display: block;
    min-height: 1.08em;
    width: 100%;
  }
  /* The converted pane is a single contenteditable surface. pre-wrap keeps
     the document's newlines as line breaks; the caret/selection are native
     so editing behaves like a normal text editor. */
  .pine-editor {
    outline: none;
    caret-color: #0a1f1d;
  }
  /* The editor's paragraphs and chips are created imperatively (in JS), so
     they don't carry Svelte's component scope — their styles must be
     :global. Scoped under .pine-editor so nothing leaks to the legacy pane. */
  :global(.pine-editor p) {
    margin: 0 0 8pt 0;
    white-space: pre-wrap;
    min-height: 1.08em;
    orphans: 2;
    widows: 2;
  }
  :global(.pine-editor p:last-child) { margin-bottom: 0; }
  :global(.pine-editor .token-pine),
  :global(.pine-editor .token-legacy) {
    font-family: "JetBrains Mono", "SF Mono", monospace;
    font-size: 0.92em;
    font-weight: 600;
    border-radius: 2px;
    white-space: normal;
    overflow-wrap: anywhere;
  }
  :global(.pine-editor .token-pine) {
    color: #0f766e;
    background: rgba(94, 234, 212, 0.28);
    cursor: pointer;
  }
  :global(.pine-editor .token-pine:hover) {
    background: rgba(94, 234, 212, 0.42);
    color: #115e59;
  }
  :global(.pine-editor .token-legacy) {
    color: #9a3412;
    background: rgba(251, 146, 60, 0.20);
  }
  :global(.pine-editor .token-pine.edited) {
    color: #92400e;
    background: rgba(251, 191, 36, 0.32);
  }
  :global(.pine-editor .token-pine.saving) {
    color: #b08512;
    background: rgba(252, 211, 77, 0.24);
  }
  :global(.pine-editor .token-pine.save-error) {
    color: #b91c1c;
    background: rgba(248, 113, 113, 0.24);
  }
  .chip-editor {
    position: fixed;
    z-index: 90;
  }
  .chip-editor-input {
    min-width: 240px;
    padding: 6px 9px;
    border-radius: 6px;
    background: #11121a;
    border: 1px solid #5eead4;
    color: #5eead4;
    font-size: 12px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.45);
    outline: none;
  }
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
     * still scroll if the user hovers a chip with a lot of detail.
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
  .prov-suggestion { color: #5eead4; }
  .prov-llm        { color: #c084fc; }
  .prov-unmatched  { color: #f87171; }
  .prov-edit       { color: #fbbf24; }
  .status-saved   { color: #fbbf24; }
  .status-pending { color: #9ca3b8; font-style: italic; }
  .status-error   { color: #f87171; }

  /* While editing, the card is pinned and needs to receive clicks
     (the Delete button). Hover-only cards stay pointer-events:none. */
  .info-card.interactive { pointer-events: auto; }
  .info-actions {
    margin-top: 6px;
    padding-top: 8px;
    border-top: 1px solid #2a2d3a;
  }
  .info-delete-btn {
    appearance: none;
    background: rgba(248, 113, 113, 0.10);
    color: #f87171;
    border: 1px solid rgba(248, 113, 113, 0.35);
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 600;
    cursor: pointer;
  }
  .info-delete-btn:hover {
    background: rgba(248, 113, 113, 0.18);
    border-color: rgba(248, 113, 113, 0.55);
  }

  /* ── Add-mapping dialog ────────────────────────────────── */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .add-modal {
    width: 440px;
    max-width: calc(100vw - 32px);
    background: #11121a;
    border: 1px solid #2a2d3a;
    border-radius: 8px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
    padding: 18px 20px 16px;
    color: #e6e8ef;
  }
  .add-title { margin: 0 0 6px; font-size: 15px; font-weight: 600; }
  .add-sub {
    margin: 0 0 16px;
    font-size: 12px;
    line-height: 1.5;
    color: #9ca3b8;
  }
  .add-sub strong { color: #5eead4; font-weight: 600; }
  .add-field { display: block; margin-bottom: 14px; }
  .add-label {
    display: block;
    margin-bottom: 5px;
    color: #6b7488;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    font-size: 10px;
    font-weight: 600;
  }
  .add-input {
    width: 100%;
    background: #0b0c12;
    border: 1px solid #2a2d3a;
    border-radius: 5px;
    padding: 7px 9px;
    color: #e6e8ef;
    font-size: 12.5px;
  }
  .add-input:focus {
    outline: none;
    border-color: #5eead4;
  }
  .add-input.mono { font-family: "JetBrains Mono", "SF Mono", monospace; }
  .add-select { cursor: pointer; }
  .add-select option { background: #0b0c12; color: #e6e8ef; }
  .add-pine-wrap {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .add-pine-input { flex: 1 1 auto; }
  .add-bracket {
    color: #5eead4;
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    font-weight: 600;
  }
  .add-error {
    margin: -4px 0 12px;
    font-size: 12px;
    color: #f87171;
  }
  .add-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 4px;
  }

  /* ── Insertion caret + right-click menu ────────────────── */
  .insert-caret {
    position: fixed;
    z-index: 35;
    width: 2px;
    background: #5eead4;
    box-shadow: 0 0 4px rgba(94, 234, 212, 0.7);
    pointer-events: none;
    animation: caret-blink 1s steps(1) infinite;
  }
  @keyframes caret-blink {
    50% { opacity: 0; }
  }
  .context-backdrop {
    position: fixed;
    inset: 0;
    z-index: 46;
  }
  .context-menu {
    position: fixed;
    min-width: 160px;
    background: #11121a;
    border: 1px solid #2a2d3a;
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
    padding: 4px;
  }
  .context-item {
    display: block;
    width: 100%;
    text-align: left;
    appearance: none;
    background: none;
    border: none;
    border-radius: 4px;
    padding: 7px 10px;
    color: #e6e8ef;
    font-size: 12.5px;
    cursor: pointer;
  }
  .context-item:hover { background: rgba(94, 234, 212, 0.14); color: #5eead4; }

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
    font-size: 12px;
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

<script>
  // Batch mode — folder → confirm unique mappings → folder.
  //
  //   setup   → pick input + output folders, LLM/persist toggles
  //   review  → editable table of the DEDUPLICATED unique mappings
  //   done    → per-file write summary
  //
  // The middle step is the point: every distinct %[...] token across the
  // whole folder is confirmed ONCE here, then applied to every file.
  let { open = $bindable(false), agency = "" } = $props();

  /** @type {"setup"|"collecting"|"review"|"applying"|"done"} */
  let step = $state("setup");
  let error = $state("");

  let inputDir = $state("");
  let outputDir = $state("");
  let useLlm = $state(true);
  let persist = $state(true);

  /** @type {Array<{jda,pine,pine_tokens,provenance,drop,count,files,edited}>} */
  let mappings = $state([]);
  let collectStats = $state(null);
  let applyResult = $state(null);
  let onlyUnresolved = $state(false);

  /** @type {null | { phase: string, done: number, total: number, label: string }} */
  let progress = $state(null);

  $effect(() => {
    if (open) reset();
  });

  // Live progress from the backend (NDJSON events forwarded by main).
  // One subscription for the dialog's lifetime; the returned unsubscribe
  // runs when the component unmounts.
  $effect(() => {
    return window.api.onBatchProgress((e) => { progress = e; });
  });

  function reset() {
    step = "setup";
    error = "";
    mappings = [];
    collectStats = null;
    applyResult = null;
    onlyUnresolved = false;
    progress = null;
  }

  // Don't let the × / backdrop / Esc dismiss the dialog while a batch is
  // running — that would orphan the child process. Use Cancel instead.
  function close() {
    if (step === "collecting" || step === "applying") return;
    open = false;
  }
  function onKeyDown(e) { if (e.key === "Escape") close(); }

  async function cancel() {
    await window.api.batchCancel();
  }

  async function pickInput() {
    const r = await window.api.pickFolder({ title: "Select input folder (JDA templates)" });
    if (r?.path) inputDir = r.path;
  }
  async function pickOutput() {
    const r = await window.api.pickFolder({ title: "Select output folder (Pine templates)" });
    if (r?.path) outputDir = r.path;
  }

  // ── Phase 1: collect ──────────────────────────────────────────
  async function scan() {
    if (!agency) { error = "Select an agency in the toolbar first."; return; }
    if (!inputDir) { error = "Choose an input folder."; return; }
    error = "";
    progress = null;
    step = "collecting";
    const r = await window.api.batchCollect({ inputDir, agency, useLlm });
    if (r?.cancelled) { error = "Scan cancelled."; step = "setup"; return; }
    if (!r || r.error) {
      error = r?.error || "Scan failed.";
      step = "setup";
      return;
    }
    collectStats = r.stats;
    mappings = (r.mappings || []).map((m) => ({
      ...m,
      pine: m.pine || "",
      drop: !!m.drop,
      edited: false,
    }));
    step = "review";
  }

  // ── Pine token parsing (mirror of the backend's list shape) ────
  // Split an edited Pine string into balanced @[...] tokens. Bare text
  // (no brackets) is wrapped as a single token. Empty → [] (unresolved).
  function splitPineTokens(s) {
    const t = (s || "").trim();
    if (!t) return [];
    const out = [];
    let i = 0;
    while (i < t.length) {
      if (t[i] === "@" && t[i + 1] === "[") {
        let depth = 1, j = i + 2;
        while (j < t.length && depth > 0) {
          if (t[j] === "[") depth++;
          else if (t[j] === "]") depth--;
          j++;
        }
        out.push(t.slice(i, j));
        i = j;
      } else i++;
    }
    return out.length ? out : [`@[${t}]`];
  }

  function onEdit(idx) {
    mappings[idx].edited = true;
    if (mappings[idx].drop) mappings[idx].drop = false;
  }

  function toggleDrop(idx) {
    mappings[idx].drop = !mappings[idx].drop;
    mappings[idx].edited = true;
  }

  // A row is "resolved" if it has Pine output OR is an explicit drop.
  function isResolved(m) {
    return m.drop || splitPineTokens(m.pine).length > 0;
  }

  // ── Derived counts ────────────────────────────────────────────
  let resolvedCount = $derived(mappings.filter(isResolved).length);
  let unresolvedCount = $derived(mappings.length - resolvedCount);
  let visibleMappings = $derived(
    onlyUnresolved ? mappings.filter((m) => !isResolved(m)) : mappings
  );

  // ── Phase 2: apply ────────────────────────────────────────────
  async function apply() {
    if (!outputDir) { error = "Choose an output folder."; return; }
    error = "";
    progress = null;
    step = "applying";
    const payload = mappings
      .map((m) => ({
        jda: m.jda,
        pine_tokens: m.drop ? [] : splitPineTokens(m.pine),
        drop: !!m.drop,
      }))
      .filter((m) => m.drop || m.pine_tokens.length > 0);
    const r = await window.api.batchApply({
      inputDir, outputDir, agency, mappings: payload, persist,
    });
    if (r?.cancelled) { error = "Conversion cancelled."; step = "review"; return; }
    if (!r || r.error) {
      error = r?.error || "Conversion failed.";
      step = "review";
      return;
    }
    applyResult = r;
    step = "done";
  }

  // Percentage for the determinate progress bar (0 when unknown).
  let progressPct = $derived(
    progress && progress.total > 0
      ? Math.round((progress.done / progress.total) * 100)
      : 0
  );

  function provLabel(p) {
    return p === "suggestion" ? "Saved" : p === "llm" ? "LLM" : "Unmatched";
  }
</script>

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="backdrop" onclick={close} onkeydown={onKeyDown} role="presentation">
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="batch-title"
      tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={onKeyDown}
    >
      <header>
        <div class="header-left">
          <h2 id="batch-title">Batch Convert</h2>
          <span class="count">{agency ? agency : "no agency"}</span>
        </div>
        <button class="close" onclick={close} aria-label="Close">×</button>
      </header>

      <div class="body">
        {#if error}
          <div class="inline-error">{error}</div>
        {/if}

        <!-- ── Step: setup ─────────────────────────────────────── -->
        {#if step === "setup"}
          <div class="setup">
            <div class="field">
              <span class="field-label">Input folder</span>
              <div class="path-row">
                <input class="text-input mono" bind:value={inputDir} placeholder="folder of JDA .rtf templates" spellcheck="false" />
                <button class="btn-ghost" onclick={pickInput}>Browse…</button>
              </div>
            </div>
            <div class="field">
              <span class="field-label">Output folder</span>
              <div class="path-row">
                <input class="text-input mono" bind:value={outputDir} placeholder="where Pine .rtf outputs are written" spellcheck="false" />
                <button class="btn-ghost" onclick={pickOutput}>Browse…</button>
              </div>
            </div>
            <label class="check">
              <input type="checkbox" bind:checked={useLlm} />
              <span>Use the LLM to propose mappings for unknown tokens (once per unique token)</span>
            </label>
            <p class="hint">
              Every template in the folder is scanned and its <code>%[…]</code> fillpoints are
              deduplicated into a single list of unique mappings for you to confirm.
            </p>
          </div>
        {/if}

        <!-- ── Step: collecting / applying progress ────────────── -->
        {#if step === "collecting" || step === "applying"}
          <div class="progress-wrap">
            <div class="progress-head">
              {#if step === "collecting"}
                {progress?.phase === "llm"
                  ? "Converting unique tokens with the LLM…"
                  : "Scanning folder and extracting fillpoints…"}
              {:else}
                Converting every file and writing outputs…
              {/if}
            </div>
            <div class="progress-track">
              <div
                class="progress-fill"
                class:indeterminate={progress?.phase === "llm" || !progress}
                style={progressPct ? `width:${progressPct}%` : ""}
              ></div>
            </div>
            <div class="progress-sub">
              {#if progress && progress.total > 0 && progress.phase !== "llm"}
                {progress.done} / {progress.total} · <span class="mono">{progress.label}</span>
              {:else if progress?.label}
                <span class="mono">{progress.label}</span>
              {:else}
                Starting…
              {/if}
            </div>
          </div>
        {/if}

        <!-- ── Step: review ────────────────────────────────────── -->
        {#if step === "review"}
          <div class="review-bar">
            <div class="stat-chips">
              <span class="stat">{collectStats?.files} files</span>
              <span class="stat">{mappings.length} unique</span>
              <span class="stat ok">{resolvedCount} resolved</span>
              <span class="stat warn">{unresolvedCount} unresolved</span>
            </div>
            <label class="check inline">
              <input type="checkbox" bind:checked={onlyUnresolved} />
              <span>Only unresolved</span>
            </label>
          </div>
          <table>
            <thead>
              <tr>
                <th class="col-jda">JDA Token</th>
                <th class="col-pine">Pine Token(s)</th>
                <th class="col-prov">Source</th>
                <th class="col-count">Uses</th>
                <th class="col-drop"></th>
              </tr>
            </thead>
            <tbody>
              {#each visibleMappings as m (m.jda)}
                {@const idx = mappings.indexOf(m)}
                <tr class:unresolved={!isResolved(m)} class:dropped={m.drop}>
                  <td class="col-jda"><span class="chip jda" title={m.jda}>{m.jda}</span></td>
                  <td class="col-pine">
                    {#if m.drop}
                      <span class="dropped-note">— dropped (emits nothing) —</span>
                    {:else}
                      <input
                        class="text-input mono cell"
                        bind:value={mappings[idx].pine}
                        oninput={() => onEdit(idx)}
                        placeholder="@[…]  ·  space-separate multiple tokens  ·  blank = skip"
                        spellcheck="false"
                      />
                    {/if}
                  </td>
                  <td class="col-prov">
                    <span class="prov-badge prov-{m.drop ? 'drop' : m.edited ? 'edit' : m.provenance}">
                      {m.drop ? "Drop" : m.edited ? "Edited" : provLabel(m.provenance)}
                    </span>
                  </td>
                  <td class="col-count" title={m.files.join('\n')}>{m.count}</td>
                  <td class="col-drop">
                    <button
                      class="drop-toggle"
                      class:on={m.drop}
                      title={m.drop ? "Undrop — convert this token" : "Drop — remove this token from the output"}
                      onclick={() => toggleDrop(idx)}
                    >⊘</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}

        <!-- ── Step: done ──────────────────────────────────────── -->
        {#if step === "done" && applyResult}
          <div class="done">
            <div class="stat-chips big">
              <span class="stat ok">{applyResult.stats.written} written</span>
              <span class="stat">{applyResult.stats.matched} matched</span>
              <span class="stat warn">{applyResult.stats.unmatched} unmatched</span>
              {#if applyResult.stats.persisted}<span class="stat">{applyResult.stats.persisted} saved</span>{/if}
              {#if applyResult.stats.errors}<span class="stat err">{applyResult.stats.errors} errors</span>{/if}
            </div>
            <p class="hint">Output written to <code>{applyResult.output_dir}</code></p>
            <table>
              <thead>
                <tr><th>File</th><th class="col-count">Matched</th><th class="col-count">Unmatched</th></tr>
              </thead>
              <tbody>
                {#each applyResult.files as f (f.name)}
                  <tr class:unresolved={f.unmatched > 0 || f.error}>
                    <td class="mono file" title={f.error || (f.unmatched_tokens || []).join('\n')}>{f.name}{#if f.error} — {f.error}{/if}</td>
                    <td class="col-count">{f.matched ?? "—"}</td>
                    <td class="col-count">{f.unmatched ?? "—"}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </div>

      <footer>
        {#if step === "setup"}
          <button class="btn-ghost" onclick={close}>Cancel</button>
          <button class="btn-primary" onclick={scan} disabled={!agency || !inputDir}>Scan folder →</button>
        {:else if step === "review"}
          <button class="btn-ghost" onclick={() => (step = "setup")}>← Back</button>
          <label class="check inline persist">
            <input type="checkbox" bind:checked={persist} />
            <span>Save mappings to this agency</span>
          </label>
          <button class="btn-primary" onclick={apply} disabled={resolvedCount === 0 || !outputDir}>
            Convert & write {resolvedCount} →
          </button>
        {:else if step === "done"}
          <button class="btn-ghost" onclick={() => (step = "review")}>← Back to mappings</button>
          <button class="btn-primary" onclick={close}>Done</button>
        {:else}
          <!-- collecting / applying -->
          <button class="btn-ghost" onclick={cancel}>Cancel</button>
        {/if}
      </footer>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed; inset: 0; z-index: 100;
    background: rgba(8, 10, 18, 0.55);
    backdrop-filter: blur(2px);
    display: grid; place-items: center;
    animation: fade 120ms ease;
  }
  @keyframes fade { from { opacity: 0; } to { opacity: 1; } }

  .modal {
    width: min(960px, 96vw);
    max-height: 88vh;
    display: flex; flex-direction: column;
    background: #11121a;
    border: 1px solid #2a2d3a;
    border-radius: 12px;
    box-shadow: 0 24px 64px rgba(0,0,0,0.55);
    color: #e6e8ef;
    overflow: hidden;
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 17px 24px; flex-shrink: 0;
    border-bottom: 1px solid #1f212d;
    background: linear-gradient(180deg, #15171f 0%, #11121a 100%);
  }
  .header-left { display: flex; align-items: baseline; gap: 12px; }
  h2 { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: -0.01em; }
  .count { font-size: 13px; color: #6b7488; text-transform: uppercase; letter-spacing: 1px; }
  .close {
    background: transparent; border: none; color: #6b7488;
    font-size: 26px; line-height: 1; cursor: pointer; padding: 0 6px; border-radius: 4px;
  }
  .close:hover { color: #e6e8ef; background: #1a1c26; }

  .body {
    flex: 1; overflow-y: auto; padding: 0;
    scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.08) transparent;
  }
  .inline-error {
    padding: 10px 24px; font-size: 14px; color: #f87171;
    background: rgba(248,113,113,0.07); border-bottom: 1px solid rgba(248,113,113,0.15);
  }
  .state-msg { padding: 44px 28px; text-align: center; font-size: 15px; color: #6b7488; }

  /* ── Progress ──────────────────────────────────────────────── */
  .progress-wrap { padding: 40px 40px 44px; display: flex; flex-direction: column; gap: 12px; }
  .progress-head { font-size: 15px; color: #c7ccd8; text-align: center; }
  .progress-track {
    height: 8px; border-radius: 999px; overflow: hidden;
    background: #1a1c26; border: 1px solid #2a2d3a;
  }
  .progress-fill {
    height: 100%; background: #5eead4; border-radius: 999px;
    transition: width 160ms ease; min-width: 2px;
  }
  .progress-fill.indeterminate {
    width: 40%; animation: slide 1.1s ease-in-out infinite;
  }
  @keyframes slide {
    0%   { margin-left: -40%; }
    100% { margin-left: 100%; }
  }
  .progress-sub { font-size: 12.5px; color: #6b7488; text-align: center; min-height: 16px; }
  .progress-sub .mono { font-family: "JetBrains Mono", monospace; color: #9aa0b0; }

  /* ── Setup ─────────────────────────────────────────────────── */
  .setup { padding: 22px 24px; display: flex; flex-direction: column; gap: 16px; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field-label {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: #6b7488;
  }
  .path-row { display: flex; gap: 8px; }
  .text-input {
    width: 100%; padding: 8px 11px; font-size: 14px;
    background: #1a1c26; border: 1px solid #2a2d3a;
    border-radius: 6px; color: #e6e8ef; outline: none; box-sizing: border-box;
  }
  .text-input:focus { border-color: #5eead4; }
  .text-input.mono { font-family: "JetBrains Mono", monospace; color: #5eead4; }
  .check { display: flex; align-items: center; gap: 9px; font-size: 14px; color: #c7ccd8; cursor: pointer; }
  .check.inline { font-size: 13px; color: #9aa0b0; }
  .check.persist { margin-right: auto; margin-left: 14px; }
  .hint { font-size: 13px; color: #6b7488; margin: 0; line-height: 1.5; }
  .hint code, code { font-family: "JetBrains Mono", monospace; color: #9aa0b0; background: #1a1c26; padding: 1px 5px; border-radius: 4px; }

  /* ── Review bar ────────────────────────────────────────────── */
  .review-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 20px; border-bottom: 1px solid #1f212d; background: #0e0f17;
    position: sticky; top: 0; z-index: 1;
  }
  .stat-chips { display: flex; gap: 8px; flex-wrap: wrap; }
  .stat-chips.big { padding: 18px 24px 4px; }
  .stat {
    font-size: 12.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
    background: rgba(148,163,184,0.10); color: #94a3b8;
  }
  .stat.ok   { background: rgba(94,234,212,0.12); color: #5eead4; }
  .stat.warn { background: rgba(251,191,36,0.12); color: #fbbf24; }
  .stat.err  { background: rgba(248,113,113,0.12); color: #f87171; }

  /* ── Table ─────────────────────────────────────────────────── */
  table { width: 100%; border-collapse: collapse; font-size: 14px; table-layout: fixed; }
  thead tr { background: #0e0f17; border-bottom: 1px solid #1f212d; }
  th {
    padding: 10px 16px; text-align: left; text-transform: uppercase;
    letter-spacing: 1px; font-size: 11.5px; font-weight: 700; color: #4a5060;
  }
  tbody tr { border-bottom: 1px solid rgba(255,255,255,0.06); }
  tbody tr:hover { background: #13141e; }
  tbody tr.unresolved { background: rgba(251,191,36,0.04); }
  td { padding: 8px 16px; vertical-align: middle; overflow: hidden; }
  .col-jda { width: 32%; }
  .col-pine { width: 42%; }
  .col-prov { width: 11%; }
  .col-count { width: 8%; text-align: right; color: #9aa0b0; font-variant-numeric: tabular-nums; }
  .col-drop { width: 7%; text-align: right; }
  tbody tr.dropped { opacity: 0.72; }
  .dropped-note {
    font-size: 13px; color: #fbbf24; font-style: italic;
    display: inline-block; padding: 4px 2px;
  }
  .drop-toggle {
    width: 26px; height: 26px; border-radius: 6px; cursor: pointer;
    background: transparent; border: 1px solid #2a2d3a; color: #6b7488;
    font-size: 15px; line-height: 1; transition: background 0.1s, color 0.1s, border-color 0.1s;
  }
  .drop-toggle:hover { color: #fbbf24; border-color: rgba(251,191,36,0.4); background: rgba(251,191,36,0.08); }
  .drop-toggle.on { color: #fbbf24; border-color: rgba(251,191,36,0.5); background: rgba(251,191,36,0.14); }

  .chip {
    display: block; font-family: "JetBrains Mono", monospace;
    font-size: 13.5px; font-weight: 600; padding: 3px 8px; border-radius: 4px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chip.jda { color: #c2410c; background: rgba(251,146,60,0.12); }
  .cell { padding: 5px 9px; font-size: 13.5px; }
  .file { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .prov-badge {
    display: inline-block; font-size: 11.5px; font-weight: 600;
    padding: 2px 9px; border-radius: 999px;
  }
  .prov-suggestion { background: rgba(94,234,212,0.10); color: #5eead4; }
  .prov-llm { background: rgba(192,132,252,0.10); color: #c084fc; }
  .prov-unmatched { background: rgba(251,191,36,0.10); color: #fbbf24; }
  .prov-edit { background: rgba(96,165,250,0.12); color: #60a5fa; }
  .prov-drop { background: rgba(148,163,184,0.14); color: #94a3b8; }

  .done { padding-bottom: 8px; }

  /* ── Footer ────────────────────────────────────────────────── */
  footer {
    display: flex; align-items: center; justify-content: flex-end; gap: 10px;
    padding: 14px 24px; flex-shrink: 0;
    border-top: 1px solid #1f212d; background: #0e0f17;
  }
  .btn-primary {
    background: #5eead4; color: #0a0a0f; border: none;
    padding: 8px 22px; border-radius: 6px; font-size: 14.5px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:hover:not(:disabled) { background: #2dd4bf; }
  .btn-primary:disabled { opacity: 0.5; cursor: default; }
  .btn-ghost {
    background: transparent; color: #9aa0b0;
    border: 1px solid #2a2d3a; border-radius: 6px;
    padding: 8px 18px; font-size: 14px; font-weight: 600; cursor: pointer;
  }
  .btn-ghost:hover:not(:disabled) { background: #1a1c26; color: #e6e8ef; }
  .btn-ghost:disabled { opacity: 0.5; cursor: default; }
</style>

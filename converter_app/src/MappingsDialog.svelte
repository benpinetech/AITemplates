<script>
  let { open = $bindable(false), agency = "" } = $props();

  /** @type {"loading"|"ready"|"error"} */
  let status = $state("loading");
  let mappings = $state([]);
  let loadError = $state("");

  // ── Add/Edit form ─────────────────────────────────────────────
  // One shared form drives both "add new" and "edit existing". A
  // mapping is one JDA token → one or more Pine tokens, plus a scope.
  /** @type {null|"add"|"edit"} */
  let formMode = $state(null);
  /** File path of the row being edited (edit mode only). */
  let editingFile = $state(null);
  let jdaText = $state("");
  /** @type {string[]} one input per Pine token; always ≥ 1 row. */
  let pineRows = $state([""]);
  let scopeKind = $state("global");
  let scopeValue = $state("");
  let formError = $state("");
  let saving = $state(false);

  $effect(() => {
    if (open) {
      load();
    } else {
      closeForm();
    }
  });

  async function load() {
    status = "loading";
    loadError = "";
    mappings = [];
    if (!agency) {
      loadError = "Select an agency to view its saved mappings.";
      status = "error";
      return;
    }
    const result = await window.api.listSuggestions({ agency });
    if (result?.error) {
      loadError = result.error;
      status = "error";
    } else {
      mappings = Array.isArray(result) ? result : [];
      status = "ready";
    }
  }

  function close() { open = false; }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      if (formMode) closeForm();
      else close();
    }
  }

  // ── Token helpers ─────────────────────────────────────────────
  function visibleTokens(tokens) {
    return (tokens || []).filter((t) => t.trim());
  }

  function scopeLabel(m) {
    if (m.scope_kind === "global") return "Agency";
    return m.scope_value || m.scope_kind;
  }

  /** Wrap bare text in the token's delimiters if the user omitted them. */
  function normalizePine(s) {
    const t = (s || "").trim();
    if (!t) return "";
    return /^@\[[\s\S]*\]$/.test(t) ? t : `@[${t}]`;
  }
  function normalizeJda(s) {
    const t = (s || "").trim();
    if (!t) return "";
    return /^%\[[\s\S]*\]$/.test(t) ? t : `%[${t}]`;
  }

  // ── Form open/close ───────────────────────────────────────────
  function openAdd() {
    formMode = "add";
    editingFile = null;
    jdaText = "";
    pineRows = [""];
    scopeKind = "global";
    scopeValue = "";
    formError = "";
  }

  function openEdit(m) {
    formMode = "edit";
    editingFile = m.file;
    jdaText = visibleTokens(m.jda_tokens).join(" ");
    const pine = visibleTokens(m.pine_tokens);
    pineRows = pine.length ? [...pine] : [""];
    scopeKind = m.scope_kind || "global";
    scopeValue = m.scope_kind === "global" ? "" : (m.scope_value || "");
    formError = "";
  }

  function closeForm() {
    formMode = null;
    editingFile = null;
    jdaText = "";
    pineRows = [""];
    scopeKind = "global";
    scopeValue = "";
    formError = "";
    saving = false;
  }

  // ── Pine token rows ───────────────────────────────────────────
  function addPineRow() {
    pineRows = [...pineRows, ""];
  }
  function removePineRow(i) {
    if (pineRows.length === 1) { pineRows = [""]; return; }
    pineRows = pineRows.filter((_, idx) => idx !== i);
  }

  // ── Save (add or edit) ────────────────────────────────────────
  async function saveForm() {
    const jda = normalizeJda(jdaText);
    const pine = pineRows.map(normalizePine).filter(Boolean);
    if (!jda) { formError = "Enter a JDA token."; return; }
    if (!pine.length) { formError = "Add at least one Pine token."; return; }
    if (scopeKind !== "global" && !scopeValue.trim()) {
      formError = `Enter a ${scopeKind} value (or choose Agency scope).`;
      return;
    }
    saving = true;
    formError = "";
    const scope = { kind: scopeKind, value: scopeKind === "global" ? "" : scopeValue.trim() };
    const payload = { agency, scope, jda_tokens: [jda], pine_tokens: pine };
    try {
      let r;
      if (formMode === "edit") {
        r = await window.api.updateSuggestion({ file: editingFile, payload });
      } else {
        r = await window.api.persistEdit({ ...payload, note: "added via Saved Mappings" });
      }
      if (r?.ok) {
        closeForm();
        await load();
      } else {
        formError = r?.error || "Save failed.";
      }
    } catch (e) {
      formError = `Couldn't save: ${e?.message || e}`;
    } finally {
      saving = false;
    }
  }

  // ── Delete ────────────────────────────────────────────────────
  let deletingFile = $state(null);
  async function deleteMapping(file) {
    if (deletingFile) return;
    deletingFile = file;
    loadError = "";
    try {
      const r = await window.api.deleteSuggestion({ file });
      if (r?.ok) {
        await load();   // reload from disk so the table reflects what's actually there
      } else {
        loadError = r?.error || "Delete failed.";
      }
    } catch (e) {
      // A rejected IPC (e.g. a stale backend) must surface, not fail silently.
      loadError = `Couldn't delete: ${e?.message || e}`;
    } finally {
      deletingFile = null;
    }
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
      aria-labelledby="mappings-title"
      tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={onKeyDown}
    >
      <header>
        <div class="header-left">
          <h2 id="mappings-title">Saved Mappings</h2>
          {#if status === "ready"}
            <span class="count">{mappings.length} {mappings.length === 1 ? "mapping" : "mappings"}</span>
          {/if}
        </div>
        <div class="header-right">
          {#if status === "ready" && !formMode}
            <button class="add-btn" onclick={openAdd}>+ Add mapping</button>
          {/if}
          <button class="close" onclick={close} aria-label="Close">×</button>
        </div>
      </header>

      <div class="body">
        {#if status === "loading"}
          <div class="state-msg">Loading…</div>
        {:else if status === "error"}
          <div class="state-msg error">{loadError}</div>
        {:else if mappings.length === 0}
          <div class="state-msg muted">
            No saved mappings yet. Use “+ Add mapping”, or edits you make while converting will appear here.
          </div>
        {:else if mappings.length > 0}
          {#if loadError}
            <div class="inline-error">{loadError}</div>
          {/if}

          <table>
            <thead>
              <tr>
                <th class="col-jda">JDA Token</th>
                <th class="col-pine">Pine Token(s)</th>
                <th class="col-scope">Scope</th>
                <th class="col-actions"></th>
              </tr>
            </thead>
            <tbody>
              {#each mappings as m (m.file)}
                <tr class:editing={editingFile === m.file}>
                  <td class="col-jda">
                    <div class="token-stack">
                      {#each visibleTokens(m.jda_tokens) as tok}
                        <span class="chip jda" title={tok}>{tok}</span>
                      {/each}
                    </div>
                  </td>
                  <td class="col-pine">
                    <div class="token-stack">
                      {#each visibleTokens(m.pine_tokens) as tok}
                        <span class="chip pine" title={tok}>{tok}</span>
                      {/each}
                    </div>
                  </td>
                  <td class="col-scope">
                    <span class="scope-badge scope-{m.scope_kind}">{scopeLabel(m)}</span>
                  </td>
                  <td class="col-actions">
                    <button class="act edit" onclick={() => openEdit(m)} disabled={deletingFile === m.file} title="Edit mapping">Edit</button>
                    <button class="act delete" onclick={() => deleteMapping(m.file)} disabled={deletingFile === m.file} title="Delete">
                      {deletingFile === m.file ? "…" : "×"}
                    </button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      <footer>
        <button class="btn-primary" onclick={close}>Close</button>
      </footer>
    </div>
  </div>

  {#if formMode}
    <!-- ── Add / Edit mapping — centered popup over the dialog ──────── -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="form-backdrop" onclick={() => { if (!saving) closeForm(); }} role="presentation">
      <div
        class="form-modal"
        role="dialog"
        aria-modal="true"
        tabindex="-1"
        onclick={(e) => e.stopPropagation()}
        onkeydown={onKeyDown}
      >
        <div class="form">
          <div class="form-title">{formMode === "edit" ? "Edit mapping" : "New mapping"}</div>

          <label class="field">
            <span class="field-label">JDA token</span>
            <input
              class="text-input mono"
              bind:value={jdaText}
              placeholder="%[Cust_Name]"
              spellcheck="false"
            />
          </label>

          <div class="field">
            <span class="field-label">Pine token(s)</span>
            <div class="pine-rows">
              {#each pineRows as _row, i (i)}
                <div class="pine-row">
                  <input
                    class="text-input mono"
                    bind:value={pineRows[i]}
                    placeholder="@[Complainant.first.NameFirst]"
                    spellcheck="false"
                    onkeydown={(e) => { if (e.key === "Enter" && i === pineRows.length - 1) { e.preventDefault(); addPineRow(); } }}
                  />
                  <button
                    class="row-btn remove"
                    title="Remove this Pine token"
                    onclick={() => removePineRow(i)}
                    disabled={pineRows.length === 1 && !pineRows[0]}
                  >−</button>
                </div>
              {/each}
            </div>
            <button class="add-row" onclick={addPineRow}>+ Add another Pine token</button>
          </div>

          <label class="field">
            <span
              class="field-label"
              title="How widely this mapping applies. Agency: every template in this agency. Audience: documents of one type. Template: only this one file. Narrower wins over broader."
            >Scope ⓘ</span>
            <div class="scope-row">
              <select
                class="text-input"
                bind:value={scopeKind}
                title="How widely this mapping applies — narrower scopes override broader ones."
              >
                <option value="global" title="Applies to every template in this agency.">Agency</option>
                <option value="audience" title="Applies to documents of one type (e.g. attorney letters).">Audience</option>
                <option value="template" title="Applies only to this one template file.">Template</option>
              </select>
              {#if scopeKind !== "global"}
                <input
                  class="text-input"
                  bind:value={scopeValue}
                  placeholder={scopeKind === "template" ? "template filename, e.g. 3A.rtf" : "audience, e.g. attorney"}
                  spellcheck="false"
                />
              {/if}
            </div>
          </label>

          {#if formError}
            <div class="inline-error">{formError}</div>
          {/if}

          <div class="form-actions">
            <button class="btn-primary" onclick={saveForm} disabled={saving}>
              {saving ? "Saving…" : formMode === "edit" ? "Save changes" : "Save mapping"}
            </button>
            <button class="btn-ghost" onclick={closeForm} disabled={saving}>Cancel</button>
          </div>
        </div>
      </div>
    </div>
  {/if}
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
    width: min(940px, 96vw);
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
  .header-right { display: flex; align-items: center; gap: 10px; }
  h2 { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: -0.01em; }
  .count { font-size: 14px; color: #6b7488; }
  .add-btn {
    background: rgba(94,234,212,0.12); color: #5eead4;
    border: 1px solid rgba(94,234,212,0.25); border-radius: 6px;
    padding: 6px 13px; font-size: 13px; font-weight: 600; cursor: pointer;
    transition: background 0.1s;
  }
  .add-btn:hover { background: rgba(94,234,212,0.22); }
  .close {
    background: transparent; border: none; color: #6b7488;
    font-size: 26px; line-height: 1; cursor: pointer;
    padding: 0 6px; border-radius: 4px;
  }
  .close:hover { color: #e6e8ef; background: #1a1c26; }

  .body {
    flex: 1; overflow-y: auto; padding: 0;
    scrollbar-width: thin;
    scrollbar-color: rgba(255,255,255,0.08) transparent;
  }

  .state-msg {
    padding: 38px 28px; text-align: center;
    font-size: 15px; color: #6b7488;
  }
  .state-msg.error  { color: #f87171; }
  .state-msg.muted  { font-style: italic; }
  .inline-error {
    padding: 10px 24px; font-size: 14px; color: #f87171;
    background: rgba(248,113,113,0.07);
    border-bottom: 1px solid rgba(248,113,113,0.15);
  }

  /* ── Add / Edit form ─────────────────────────────────────────── */
  /* Add / Edit mapping — a centered popup over the dialog. */
  .form-backdrop {
    position: fixed; inset: 0; z-index: 110;
    background: rgba(8, 10, 18, 0.6);
    backdrop-filter: blur(2px);
    display: grid; place-items: center;
    animation: fade 100ms ease;
  }
  .form-modal {
    width: min(560px, 92vw);
    max-height: 88vh;
    overflow-y: auto;
    background: #14151e;
    border: 1px solid #2a2d3a;
    border-radius: 12px;
    box-shadow: 0 24px 64px rgba(0,0,0,0.6);
  }
  .form {
    padding: 22px 24px;
    display: flex; flex-direction: column; gap: 14px;
  }
  .form-title { font-size: 16px; font-weight: 700; color: #e6e8ef; letter-spacing: 0.01em; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field-label {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: #6b7488;
  }
  .text-input {
    width: 100%; padding: 8px 11px;
    font-size: 14px;
    background: #1a1c26; border: 1px solid #2a2d3a;
    border-radius: 6px; color: #e6e8ef; outline: none;
    box-sizing: border-box;
  }
  .text-input:focus { border-color: #5eead4; }
  .text-input.mono { font-family: "JetBrains Mono", monospace; color: #5eead4; }
  select.text-input { cursor: pointer; }

  .pine-rows { display: flex; flex-direction: column; gap: 7px; }
  .pine-row { display: flex; gap: 8px; align-items: center; }
  .row-btn {
    flex-shrink: 0;
    width: 32px; height: 32px;
    border: 1px solid #2a2d3a; border-radius: 6px;
    background: #1a1c26; color: #9aa0b0;
    font-size: 18px; line-height: 1; cursor: pointer;
    transition: background 0.1s, color 0.1s;
  }
  .row-btn.remove:hover:not(:disabled) { background: rgba(248,113,113,0.12); color: #f87171; border-color: rgba(248,113,113,0.3); }
  .row-btn:disabled { opacity: 0.4; cursor: default; }
  .add-row {
    align-self: flex-start;
    background: transparent; color: #5eead4;
    border: none; padding: 2px 0; margin-top: 2px;
    font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .add-row:hover { color: #2dd4bf; }

  .scope-row { display: flex; gap: 8px; }
  .scope-row select.text-input { max-width: 160px; }

  .form-actions { display: flex; gap: 10px; margin-top: 4px; }
  .btn-ghost {
    background: transparent; color: #9aa0b0;
    border: 1px solid #2a2d3a; border-radius: 6px;
    padding: 8px 18px; font-size: 14px; font-weight: 600; cursor: pointer;
  }
  .btn-ghost:hover:not(:disabled) { background: #1a1c26; color: #e6e8ef; }
  .btn-ghost:disabled { opacity: 0.5; cursor: default; }

  /* ── Table (≈20% larger) ─────────────────────────────────────── */
  table {
    width: 100%; border-collapse: collapse;
    font-size: 15px;
    table-layout: fixed;
  }
  thead tr {
    background: #0e0f17;
    border-bottom: 1px solid #1f212d;
  }
  th {
    padding: 11px 17px;
    text-align: left;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-size: 12px;
    font-weight: 700;
    color: #4a5060;
    overflow: hidden;
  }
  tbody tr {
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    transition: background 0.1s;
  }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover, tbody tr.editing { background: #13141e; }

  td { padding: 11px 17px; vertical-align: middle; overflow: hidden; }

  .col-jda     { width: 30%; }
  .col-pine    { width: 40%; }
  .col-scope   { width: 16%; }
  .col-actions { width: 14%; }

  /* ── Token stacks — chips rendered one per line ──────────────── */
  .token-stack {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .chip {
    display: block;
    font-family: "JetBrains Mono", monospace;
    font-size: 14px; font-weight: 600;
    padding: 3px 8px; border-radius: 4px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chip.jda  { color: #c2410c; background: rgba(251,146,60,0.12); }
  .chip.pine { color: #0f766e; background: rgba(94,234,212,0.12); }

  /* ── Scope badges ────────────────────────────────────────────── */
  .scope-badge {
    display: inline-block;
    font-size: 12px; font-weight: 600;
    padding: 3px 10px; border-radius: 999px;
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .scope-global   { background: rgba(148,163,184,0.10); color: #94a3b8; }
  .scope-template { background: rgba(192,132,252,0.10); color: #c084fc; }
  .scope-audience { background: rgba(94,234,212,0.10);  color: #5eead4; }

  /* ── Row actions ─────────────────────────────────────────────── */
  .col-actions { text-align: right; white-space: nowrap; }
  .act {
    border: none; border-radius: 5px; cursor: pointer;
    font-size: 14px; font-weight: 500;
    padding: 4px 11px; margin-left: 5px;
    transition: background 0.1s, color 0.1s;
  }
  .act.edit   { background: transparent; color: #6b7488; }
  .act.edit:hover { background: #1f212d; color: #e6e8ef; }
  .act.delete {
    background: transparent; color: #6b7488;
    font-size: 18px; padding: 1px 10px; line-height: 1.3;
  }
  .act.delete:hover { background: rgba(248,113,113,0.10); color: #f87171; }

  /* ── Footer ──────────────────────────────────────────────────── */
  footer {
    display: flex; justify-content: flex-end;
    padding: 14px 24px; flex-shrink: 0;
    border-top: 1px solid #1f212d; background: #0e0f17;
  }
  .btn-primary {
    background: #5eead4; color: #0a0a0f; border: none;
    padding: 8px 24px; border-radius: 6px;
    font-size: 15px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:hover:not(:disabled) { background: #2dd4bf; }
  .btn-primary:disabled { opacity: 0.6; cursor: default; }
</style>

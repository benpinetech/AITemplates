<script>
  let { open = $bindable(false) } = $props();

  /** @type {"loading"|"ready"|"error"} */
  let status = $state("loading");
  let mappings = $state([]);
  let loadError = $state("");

  /** File path of the row being edited, or null. */
  let editingFile = $state(null);
  let editPineText = $state("");
  let editError = $state("");

  $effect(() => {
    if (open) {
      load();
    } else {
      editingFile = null;
      editError = "";
    }
  });

  async function load() {
    status = "loading";
    loadError = "";
    mappings = [];
    const result = await window.api.listSuggestions({ org: "oba" });
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
      if (editingFile) cancelEdit();
      else close();
    }
  }

  // ── Token helpers ─────────────────────────────────────────────
  function visibleTokens(tokens) {
    return (tokens || []).filter(t => t.trim());
  }

  function joinTokens(tokens) {
    return visibleTokens(tokens).join(" ");
  }

  function parsePineTokens(text) {
    return text.match(/@\[[^\]]*\]/g) || [];
  }

  function scopeLabel(m) {
    if (m.scope_kind === "global") return "Global";
    return m.scope_value || m.scope_kind;
  }

  // ── Edit ──────────────────────────────────────────────────────
  function startEdit(m) {
    editingFile  = m.file;
    editPineText = joinTokens(m.pine_tokens);
    editError    = "";
  }

  function cancelEdit() {
    editingFile  = null;
    editPineText = "";
    editError    = "";
  }

  async function commitEdit(m) {
    const newTokens = parsePineTokens(editPineText);
    if (!newTokens.length) {
      editError = "No valid Pine tokens found.";
      return;
    }
    const payload = {
      org: m.org,
      scope: { kind: m.scope_kind, value: m.scope_value },
      jda_tokens:  m.jda_tokens,
      pine_tokens: newTokens,
    };
    const r = await window.api.updateSuggestion({ file: m.file, payload });
    if (r?.ok) {
      cancelEdit();
      await load();
    } else {
      editError = r?.error || "Update failed.";
    }
  }

  // ── Delete ────────────────────────────────────────────────────
  async function deleteMapping(file) {
    const r = await window.api.deleteSuggestion({ file });
    if (r?.ok) {
      mappings = mappings.filter(m => m.file !== file);
    } else {
      loadError = r?.error || "Delete failed.";
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
        <button class="close" onclick={close} aria-label="Close">×</button>
      </header>

      <div class="body">
        {#if status === "loading"}
          <div class="state-msg">Loading…</div>
        {:else if status === "error"}
          <div class="state-msg error">{loadError}</div>
        {:else if mappings.length === 0}
          <div class="state-msg muted">
            No saved mappings yet. Edits you make while converting will appear here.
          </div>
        {:else}
          {#if loadError}
            <div class="inline-error">{loadError}</div>
          {/if}
          {#if editError}
            <div class="inline-error">{editError}</div>
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
                    {#if editingFile === m.file}
                      <input
                        class="pine-input"
                        type="text"
                        bind:value={editPineText}
                        spellcheck="false"
                        onkeydown={(e) => {
                          if (e.key === "Enter")  commitEdit(m);
                          if (e.key === "Escape") cancelEdit();
                        }}
                      />
                    {:else}
                      <div class="token-stack">
                        {#each visibleTokens(m.pine_tokens) as tok}
                          <span class="chip pine" title={tok}>{tok}</span>
                        {/each}
                      </div>
                    {/if}
                  </td>
                  <td class="col-scope">
                    <span class="scope-badge scope-{m.scope_kind}">{scopeLabel(m)}</span>
                  </td>
                  <td class="col-actions">
                    {#if editingFile === m.file}
                      <button class="act save"   onclick={() => commitEdit(m)}>Save</button>
                      <button class="act cancel" onclick={cancelEdit}>Cancel</button>
                    {:else}
                      <button class="act edit"   onclick={() => startEdit(m)}        title="Edit Pine tokens">Edit</button>
                      <button class="act delete" onclick={() => deleteMapping(m.file)} title="Delete">×</button>
                    {/if}
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
    width: min(780px, 95vw);
    max-height: 85vh;
    display: flex; flex-direction: column;
    background: #11121a;
    border: 1px solid #2a2d3a;
    border-radius: 10px;
    box-shadow: 0 24px 64px rgba(0,0,0,0.55);
    color: #e6e8ef;
    overflow: hidden;
  }

  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px; flex-shrink: 0;
    border-bottom: 1px solid #1f212d;
    background: linear-gradient(180deg, #15171f 0%, #11121a 100%);
  }
  .header-left { display: flex; align-items: baseline; gap: 10px; }
  h2 { margin: 0; font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
  .count { font-size: 11.5px; color: #6b7488; }
  .close {
    background: transparent; border: none; color: #6b7488;
    font-size: 22px; line-height: 1; cursor: pointer;
    padding: 0 6px; border-radius: 4px;
  }
  .close:hover { color: #e6e8ef; background: #1a1c26; }

  .body {
    flex: 1; overflow-y: auto; padding: 0;
    scrollbar-width: thin;
    scrollbar-color: rgba(255,255,255,0.08) transparent;
  }

  .state-msg {
    padding: 32px 24px; text-align: center;
    font-size: 13px; color: #6b7488;
  }
  .state-msg.error  { color: #f87171; }
  .state-msg.muted  { font-style: italic; }
  .inline-error {
    padding: 8px 20px; font-size: 12px; color: #f87171;
    background: rgba(248,113,113,0.07);
    border-bottom: 1px solid rgba(248,113,113,0.15);
  }

  /* ── Table ───────────────────────────────────────────────────── */
  table {
    width: 100%; border-collapse: collapse;
    font-size: 12.5px;
    table-layout: fixed;
  }
  thead tr {
    background: #0e0f17;
    border-bottom: 1px solid #1f212d;
  }
  th {
    padding: 9px 14px;
    text-align: left;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-size: 10px;
    font-weight: 700;
    color: #4a5060;
    overflow: hidden;
  }
  tbody tr {
    border-bottom: 1px solid #13141e;
    transition: background 0.1s;
  }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover, tbody tr.editing { background: #13141e; }

  td { padding: 9px 14px; vertical-align: middle; overflow: hidden; }

  .col-jda     { width: 32%; }
  .col-pine    { width: 38%; }
  .col-scope   { width: 16%; }
  .col-actions { width: 14%; }

  /* ── Token stacks — chips rendered one per line ──────────────── */
  .token-stack {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .chip {
    display: block;
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px; font-weight: 600;
    padding: 2px 6px; border-radius: 3px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chip.jda  { color: #c2410c; background: rgba(251,146,60,0.12); }
  .chip.pine { color: #0f766e; background: rgba(94,234,212,0.12); }

  .pine-input {
    width: 100%; padding: 4px 8px;
    font-family: "JetBrains Mono", monospace; font-size: 11.5px;
    background: #1a1c26; border: 1px solid #5eead4;
    border-radius: 4px; color: #5eead4; outline: none;
    box-sizing: border-box;
  }

  /* ── Scope badges ────────────────────────────────────────────── */
  .scope-badge {
    display: inline-block;
    font-size: 10px; font-weight: 600;
    padding: 2px 8px; border-radius: 999px;
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .scope-global   { background: rgba(148,163,184,0.10); color: #94a3b8; }
  .scope-template { background: rgba(192,132,252,0.10); color: #c084fc; }
  .scope-audience { background: rgba(94,234,212,0.10);  color: #5eead4; }

  /* ── Row actions ─────────────────────────────────────────────── */
  .col-actions { text-align: right; white-space: nowrap; }
  .act {
    border: none; border-radius: 4px; cursor: pointer;
    font-size: 11.5px; font-weight: 500;
    padding: 3px 9px; margin-left: 4px;
    transition: background 0.1s, color 0.1s;
  }
  .act.edit   { background: transparent; color: #6b7488; }
  .act.edit:hover { background: #1f212d; color: #e6e8ef; }
  .act.delete {
    background: transparent; color: #6b7488;
    font-size: 15px; padding: 1px 8px; line-height: 1.3;
  }
  .act.delete:hover { background: rgba(248,113,113,0.10); color: #f87171; }
  .act.save   { background: rgba(94,234,212,0.12); color: #5eead4; }
  .act.save:hover { background: rgba(94,234,212,0.22); }
  .act.cancel { background: transparent; color: #6b7488; }
  .act.cancel:hover { background: #1f212d; color: #e6e8ef; }

  /* ── Footer ──────────────────────────────────────────────────── */
  footer {
    display: flex; justify-content: flex-end;
    padding: 12px 20px; flex-shrink: 0;
    border-top: 1px solid #1f212d; background: #0e0f17;
  }
  .btn-primary {
    background: #5eead4; color: #0a0a0f; border: none;
    padding: 7px 20px; border-radius: 6px;
    font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:hover { background: #2dd4bf; }
</style>

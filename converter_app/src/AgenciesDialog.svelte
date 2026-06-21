<script>
  // Manage Agencies: list every configured agency and add / rename / delete.
  // The parent owns the active selection; we notify it of changes via
  // ``onchange({ agencies, deleted?, renamed? })`` so it can refresh the
  // picker and reconcile which agency is selected.
  let { open = $bindable(false), selected = "", onchange } = $props();

  /** @type {"loading"|"ready"|"error"} */
  let status = $state("loading");
  let agencies = $state([]);
  let loadError = $state("");

  // Add
  let newName = $state("");
  let addError = $state("");
  let adding = $state(false);

  // Rename (inline, one row at a time)
  let renamingId = $state(null);
  let renameText = $state("");
  let renameError = $state("");
  let renaming = $state(false);

  // Delete confirmation popup (the id of the agency awaiting confirmation)
  let confirmDeleteId = $state(null);
  let deleting = $state(false);
  let rowError = $state("");
  /** The agency object the confirm popup is asking about, or undefined. */
  let pendingDelete = $derived(agencies.find((a) => a.id === confirmDeleteId));

  $effect(() => {
    if (open) load();
    else resetTransient();
  });

  function resetTransient() {
    newName = ""; addError = ""; adding = false;
    renamingId = null; renameText = ""; renameError = ""; renaming = false;
    confirmDeleteId = null; deleting = false; rowError = "";
  }

  async function load() {
    status = "loading";
    loadError = "";
    const result = await window.api.listAgencies();
    if (Array.isArray(result)) {
      agencies = result;
      status = "ready";
    } else {
      loadError = result?.error || "Failed to load agencies.";
      status = "error";
    }
  }

  // Reload the list and tell the parent what changed.
  async function refresh(change = {}) {
    await load();
    onchange?.({ agencies, ...change });
  }

  function close() { open = false; }

  function onKeyDown(e) {
    if (e.key !== "Escape") return;
    if (renamingId) cancelRename();
    else if (confirmDeleteId) confirmDeleteId = null;
    else close();
  }

  // ── Add ───────────────────────────────────────────────────────
  async function addAgency() {
    const name = newName.trim();
    if (!name) { addError = "Enter an agency name."; return; }
    adding = true;
    addError = "";
    try {
      const r = await window.api.createAgency({ name });
      if (r?.error) {
        addError = r.code === "exists" ? "An agency with that name already exists."
                 : r.code === "invalid" ? "That name has no usable letters or numbers."
                 : r.error;
        return;
      }
      newName = "";
      await refresh();
    } catch (e) {
      addError = `Couldn't add agency: ${e?.message || e}`;
    } finally {
      adding = false;
    }
  }

  // ── Rename ────────────────────────────────────────────────────
  function startRename(a) {
    renamingId = a.id;
    renameText = a.description || a.id;
    renameError = "";
    confirmDeleteId = null;
  }
  function cancelRename() {
    renamingId = null; renameText = ""; renameError = "";
  }
  async function commitRename(a) {
    const name = renameText.trim();
    if (!name) { renameError = "Enter a name."; return; }
    if (name === a.description) { cancelRename(); return; }
    renaming = true;
    renameError = "";
    try {
      const r = await window.api.renameAgency({ slug: a.id, name });
      if (r?.error) {
        renameError = r.code === "exists" ? "An agency with that name already exists."
                    : r.code === "invalid" ? "That name has no usable letters or numbers."
                    : r.error;
        return;
      }
      cancelRename();
      await refresh({ renamed: { from: a.id, to: r.id } });
    } catch (e) {
      renameError = `Couldn't rename: ${e?.message || e}`;
    } finally {
      renaming = false;
    }
  }

  // ── Delete ────────────────────────────────────────────────────
  function askDelete(a) {
    confirmDeleteId = a.id;
    renamingId = null;
    rowError = "";
  }
  async function commitDelete(a) {
    deleting = true;
    rowError = "";
    try {
      const r = await window.api.deleteAgency({ slug: a.id });
      if (r?.error) { rowError = r.error; return; }   // keep the popup open
      confirmDeleteId = null;
      await refresh({ deleted: a.id });
    } catch (e) {
      rowError = `Couldn't delete: ${e?.message || e}`;
    } finally {
      deleting = false;
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
      aria-labelledby="agencies-title"
      tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={onKeyDown}
    >
      <header>
        <div class="header-left">
          <h2 id="agencies-title">Manage Agencies</h2>
          {#if status === "ready"}
            <span class="count">{agencies.length} {agencies.length === 1 ? "agency" : "agencies"}</span>
          {/if}
        </div>
        <button class="close" onclick={close} aria-label="Close">×</button>
      </header>

      <div class="body">
        <!-- Add -->
        <div class="add-row">
          <input
            class="text-input"
            placeholder="New agency name…"
            bind:value={newName}
            disabled={adding}
            onkeydown={(e) => { if (e.key === "Enter") addAgency(); }}
          />
          <button class="btn-primary" onclick={addAgency} disabled={adding}>
            {adding ? "Adding…" : "Add"}
          </button>
        </div>
        {#if addError}<div class="inline-error">{addError}</div>{/if}

        {#if status === "loading"}
          <div class="state-msg">Loading…</div>
        {:else if status === "error"}
          <div class="state-msg error">{loadError}</div>
        {:else if agencies.length === 0}
          <div class="state-msg muted">No agencies yet. Add one above.</div>
        {:else}
          <ul class="agency-list">
            {#each agencies as a (a.id)}
              <li class="agency" class:active={a.id === selected}>
                {#if renamingId === a.id}
                  <input
                    class="text-input rename-input"
                    bind:value={renameText}
                    disabled={renaming}
                    onkeydown={(e) => {
                      if (e.key === "Enter") commitRename(a);
                      else if (e.key === "Escape") cancelRename();
                    }}
                  />
                  <div class="agency-actions">
                    <button class="act save" onclick={() => commitRename(a)} disabled={renaming}>
                      {renaming ? "…" : "Save"}
                    </button>
                    <button class="act ghost" onclick={cancelRename} disabled={renaming}>Cancel</button>
                  </div>
                {:else}
                  <div class="agency-name">
                    <span class="name">{a.description || a.id}</span>
                    {#if a.id === selected}<span class="active-badge">Active</span>{/if}
                    <span class="slug">{a.id}</span>
                  </div>
                  <div class="agency-actions">
                    <button class="act" onclick={() => startRename(a)}>Rename</button>
                    <button class="act delete" onclick={() => askDelete(a)} title="Delete">×</button>
                  </div>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
        {#if renameError}<div class="inline-error">{renameError}</div>{/if}
      </div>

      <footer>
        <button class="btn-primary" onclick={close}>Close</button>
      </footer>
    </div>
  </div>

  {#if pendingDelete}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="confirm-backdrop" onclick={() => { if (!deleting) confirmDeleteId = null; }} role="presentation">
      <div
        class="confirm-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        tabindex="-1"
        onclick={(e) => e.stopPropagation()}
        onkeydown={onKeyDown}
      >
        <h3 id="confirm-title">Delete agency?</h3>
        <p>
          This permanently deletes <strong>{pendingDelete.description || pendingDelete.id}</strong>
          and all of its saved mappings. This can't be undone.
        </p>
        {#if rowError}<div class="inline-error">{rowError}</div>{/if}
        <div class="confirm-actions">
          <button class="act ghost" onclick={() => (confirmDeleteId = null)} disabled={deleting}>Cancel</button>
          <button class="btn-danger" onclick={() => commitDelete(pendingDelete)} disabled={deleting}>
            {deleting ? "Deleting…" : "Delete"}
          </button>
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
    width: min(620px, 95vw);
    max-height: 85vh;
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
  .count { font-size: 14px; color: #6b7488; }
  .close {
    background: transparent; border: none; color: #6b7488;
    font-size: 26px; line-height: 1; cursor: pointer;
    padding: 0 6px; border-radius: 4px;
  }
  .close:hover { color: #e6e8ef; background: #1a1c26; }

  .body {
    flex: 1; overflow-y: auto; padding: 18px 24px;
    scrollbar-width: thin;
    scrollbar-color: rgba(255,255,255,0.08) transparent;
  }

  .add-row { display: flex; gap: 10px; }
  .text-input {
    flex: 1; min-width: 0; padding: 9px 12px;
    font-size: 14px;
    background: #1a1c26; border: 1px solid #2a2d3a;
    border-radius: 7px; color: #e6e8ef; outline: none;
  }
  .text-input:focus { border-color: #5eead4; }

  .state-msg { padding: 28px 8px; text-align: center; font-size: 14px; color: #6b7488; }
  .state-msg.error { color: #f87171; }
  .state-msg.muted { font-style: italic; }
  .inline-error {
    margin-top: 10px; padding: 8px 12px; font-size: 13px; color: #f87171;
    background: rgba(248,113,113,0.08); border-radius: 6px;
  }

  /* ── Agency list ─────────────────────────────────────────────── */
  .agency-list { list-style: none; margin: 16px 0 0; padding: 0; }
  .agency {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 11px 12px; border-radius: 8px;
    border: 1px solid #1f212d; margin-bottom: 8px;
    background: #0e0f17;
  }
  .agency.active { border-color: rgba(94,234,212,0.35); }
  .agency-name { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
  .name { font-size: 15px; font-weight: 600; color: #e6e8ef; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .slug { font-family: "JetBrains Mono", monospace; font-size: 11.5px; color: #4a5060; }
  .active-badge {
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
    color: #5eead4; background: rgba(94,234,212,0.12);
    padding: 2px 7px; border-radius: 999px;
  }
  .rename-input { flex: 1; }

  .agency-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .act {
    border: 1px solid #2a2d3a; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 500; padding: 5px 12px;
    background: #1a1c26; color: #c7cad6;
    transition: background 0.1s, color 0.1s, border-color 0.1s;
  }
  .act:hover:not(:disabled) { background: #23263200; background-color: #23262f; color: #e6e8ef; }
  .act:disabled { opacity: 0.5; cursor: default; }
  .act.delete {
    background: transparent; border-color: transparent; color: #6b7488;
    font-size: 17px; padding: 2px 10px; line-height: 1.2;
  }
  .act.delete:hover:not(:disabled) { background: rgba(248,113,113,0.10); color: #f87171; }
  .act.save { background: rgba(94,234,212,0.12); border-color: rgba(94,234,212,0.25); color: #5eead4; }
  .act.save:hover:not(:disabled) { background: rgba(94,234,212,0.22); }
  .act.ghost { background: transparent; }

  /* ── Footer ──────────────────────────────────────────────────── */
  footer {
    display: flex; justify-content: flex-end;
    padding: 14px 24px; flex-shrink: 0;
    border-top: 1px solid #1f212d; background: #0e0f17;
  }
  .btn-primary {
    background: #5eead4; color: #0a0a0f; border: none;
    padding: 9px 22px; border-radius: 7px;
    font-size: 14px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:hover:not(:disabled) { background: #2dd4bf; }
  .btn-primary:disabled { opacity: 0.6; cursor: default; }

  /* ── Delete confirmation popup ───────────────────────────────── */
  .confirm-backdrop {
    position: fixed; inset: 0; z-index: 110;
    background: rgba(8, 10, 18, 0.6);
    backdrop-filter: blur(2px);
    display: grid; place-items: center;
    animation: fade 100ms ease;
  }
  .confirm-modal {
    width: min(420px, 92vw);
    background: #14151e;
    border: 1px solid #2a2d3a;
    border-radius: 12px;
    box-shadow: 0 24px 64px rgba(0,0,0,0.6);
    padding: 22px 24px 18px;
    color: #e6e8ef;
  }
  .confirm-modal h3 { margin: 0 0 8px; font-size: 16px; font-weight: 700; }
  .confirm-modal p { margin: 0; font-size: 13.5px; line-height: 1.5; color: #b6bac6; }
  .confirm-modal strong { color: #fca5a5; font-weight: 600; }
  .confirm-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
  .btn-danger {
    background: #ef4444; color: #fff; border: none;
    padding: 8px 18px; border-radius: 7px;
    font-size: 14px; font-weight: 600; cursor: pointer;
    transition: background 0.1s;
  }
  .btn-danger:hover:not(:disabled) { background: #dc2626; }
  .btn-danger:disabled { opacity: 0.6; cursor: default; }
</style>

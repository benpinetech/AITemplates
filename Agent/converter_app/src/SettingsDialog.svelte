<script>
  let { open = $bindable(false), onSaved = () => {} } = $props();

  const MODEL_OPTIONS = ["gpt-5.5", "gpt-5", "gpt-4.1", "gpt-4o", "gpt-4o-mini"];

  let apiKey = $state("");
  let model  = $state(MODEL_OPTIONS[0]);
  let revealKey = $state(false);
  let busy  = $state(false);
  let error = $state("");

  let loaded = $state({ api_key: "", model: "" });

  $effect(() => {
    if (open) hydrate();
  });

  async function hydrate() {
    error = "";
    revealKey = false;
    try {
      const s = await window.api.getSettings();
      loaded = s;
      apiKey = s.api_key || "";
      model  = s.model   || MODEL_OPTIONS[0];
    } catch (e) {
      error = String(e?.message || e);
    }
  }

  async function save() {
    busy = true;
    error = "";
    try {
      await window.api.setSettings({ api_key: apiKey, model: model.trim() });
      onSaved({ api_key: apiKey, model: model.trim() });
      open = false;
    } catch (e) {
      error = String(e?.message || e);
    } finally {
      busy = false;
    }
  }

  function cancel() {
    apiKey = loaded.api_key || "";
    model  = loaded.model   || MODEL_OPTIONS[0];
    open = false;
  }

  function onKeyDown(e) {
    if (e.key === "Escape") cancel();
    else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
  }

  function maskedHint() {
    if (!loaded.api_key) return "";
    const k = loaded.api_key;
    if (k.length <= 8) return "•".repeat(k.length);
    return `${k.slice(0, 3)}…${k.slice(-4)}`;
  }
</script>

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="backdrop" onclick={cancel} onkeydown={onKeyDown} role="presentation">
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-title"
      tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={onKeyDown}
    >
      <header>
        <h2 id="settings-title">LLM Settings</h2>
        <button class="close" onclick={cancel} aria-label="Close">×</button>
      </header>

      <div class="body">
        <label class="field">
          <span class="label">OpenAI API Key</span>
          <div class="input-row">
            <input
              type={revealKey ? "text" : "password"}
              bind:value={apiKey}
              placeholder={loaded.api_key ? `Saved (${maskedHint()})` : "sk-…"}
              autocomplete="off"
              spellcheck="false"
            />
            <button
              type="button"
              class="reveal"
              onclick={() => revealKey = !revealKey}
              aria-label={revealKey ? "Hide key" : "Show key"}
            >{revealKey ? "Hide" : "Show"}</button>
          </div>
        </label>

        <label class="field">
          <span class="label">Model</span>
          <select value={model} onchange={(e) => model = e.currentTarget.value}>
            {#each MODEL_OPTIONS as opt}
              <option value={opt}>{opt}</option>
            {/each}
          </select>
        </label>

        {#if error}
          <div class="error">⚠ {error}</div>
        {/if}
      </div>

      <footer>
        <button class="btn-ghost"   onclick={cancel} disabled={busy}>Cancel</button>
        <button class="btn-primary" onclick={save}   disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </footer>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(8, 10, 18, 0.55);
    backdrop-filter: blur(2px);
    display: grid;
    place-items: center;
    animation: fade 120ms ease;
  }
  @keyframes fade { from { opacity: 0; } to { opacity: 1; } }

  .modal {
    width: min(480px, 92vw);
    background: #11121a;
    border: 1px solid #2a2d3a;
    border-radius: 10px;
    box-shadow: 0 24px 64px rgba(0,0,0,0.55);
    color: #e6e8ef;
    overflow: hidden;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    border-bottom: 1px solid #1f212d;
    background: linear-gradient(180deg, #15171f 0%, #11121a 100%);
  }
  h2 { margin: 0; font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
  .close {
    background: transparent; border: none; color: #6b7488;
    font-size: 22px; line-height: 1; cursor: pointer;
    padding: 0 6px; border-radius: 4px;
  }
  .close:hover { color: #e6e8ef; background: #1a1c26; }

  .body { padding: 18px 20px 4px; display: grid; gap: 16px; }
  .field { display: grid; gap: 6px; }
  .label {
    text-transform: uppercase; letter-spacing: 1.2px;
    font-size: 10.5px; font-weight: 600; color: #6b7488;
  }
  input, select {
    background: #1a1c26; border: 1px solid #2a2d3a; border-radius: 6px;
    color: #e6e8ef; font: inherit; font-size: 13px;
    padding: 8px 10px; outline: none; width: 100%;
  }
  input:focus, select:focus { border-color: #5eead4; }
  input::placeholder { color: #4a5060; }
  select { cursor: pointer; appearance: auto; }

  .input-row { display: flex; gap: 6px; }
  .input-row input { flex: 1; }
  .reveal {
    background: #1a1c26; border: 1px solid #2a2d3a;
    color: #9ca3b8; border-radius: 6px;
    padding: 0 12px; font-size: 12px; cursor: pointer;
  }
  .reveal:hover { color: #e6e8ef; }

  .error {
    background: rgba(248,113,113,0.10); color: #f87171;
    border: 1px solid rgba(248,113,113,0.30); border-radius: 6px;
    padding: 8px 10px; font-size: 12.5px;
    font-family: "JetBrains Mono", monospace; white-space: pre-wrap;
  }

  footer {
    display: flex; justify-content: flex-end; gap: 8px;
    padding: 14px 20px; border-top: 1px solid #1f212d; background: #0e0f17;
  }
  .btn-ghost {
    background: transparent; color: #9ca3b8; border: 1px solid #2a2d3a;
    padding: 7px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer;
  }
  .btn-ghost:hover:not(:disabled) { background: #1a1c26; color: #e6e8ef; }
  .btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-primary {
    background: #5eead4; color: #0a0a0f; border: none;
    padding: 7px 18px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:hover:not(:disabled) { background: #2dd4bf; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>

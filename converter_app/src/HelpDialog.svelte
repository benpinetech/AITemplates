<script>
  // In-app help modal. Triggered from the application menu's "Help"
  // entry (which sends ``open-help`` over IPC). All content is about
  // *using the converter*, not the Electron host.

  let { open = $bindable(false) } = $props();

  function close() { open = false; }

  function onKeyDown(e) {
    if (e.key === "Escape") close();
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
      aria-labelledby="help-title"
      tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={onKeyDown}
    >
      <header>
        <div>
          <h2 id="help-title">JDA to Pine Converter</h2>
          <p class="lede">
            Migrates legacy JDA template syntax to Pine template syntax. Open a
            document, run Convert, and refine the result by clicking individual tokens.
          </p>
        </div>
        <button class="close" onclick={close} aria-label="Close">×</button>
      </header>

      <div class="body">
        <section>
          <h3>Workflow</h3>
          <ol class="steps">
            <li>
              <strong>Settings</strong> — open the Settings menu and enter your
              OpenAI API key. The Convert button is disabled until a key is saved.
            </li>
            <li>
              <strong>Open</strong> — pick a JDA RTF (the source pane fills in
              immediately).
            </li>
            <li>
              <strong>Convert</strong> — the pipeline rewrites JDA tokens into
              Pine tokens. Pattern matches are deterministic; anything unmatched
              falls back to the LLM.
            </li>
            <li>
              <strong>Review</strong> — converted tokens appear as teal chips in
              the right pane. The left pane shows the original for comparison.
            </li>
            <li>
              <strong>Edit</strong> — click any chip to edit it inline. Press
              <kbd>Enter</kbd> to confirm or <kbd>Esc</kbd> to cancel.
            </li>
            <li>
              <strong>Save</strong> — writes the converted RTF to disk. Edits
              you committed are already spliced into it.
            </li>
          </ol>
        </section>

        <section>
          <h3>Where edits go</h3>
          <p>
            Edits are saved and remembered. The next time you convert a template,
            your corrections are applied automatically so you don't have to make
            the same fix twice.
          </p>
        </section>
      </div>

      <footer>
        <button class="btn-primary" onclick={close}>Got it</button>
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
  @keyframes fade {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
  .modal {
    width: min(680px, 94vw);
    max-height: 90vh;
    overflow: hidden;
    background: #11121a;
    border: 1px solid #2a2d3a;
    border-radius: 10px;
    box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55);
    color: #e6e8ef;
    display: flex;
    flex-direction: column;
  }
  header {
    display: flex;
    align-items: flex-start;
    gap: 16px;
    padding: 18px 22px;
    border-bottom: 1px solid #1f212d;
    background: linear-gradient(180deg, #15171f 0%, #11121a 100%);
  }
  header > div { flex: 1; min-width: 0; }
  h2 {
    margin: 0 0 4px 0;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  .lede {
    margin: 0;
    font-size: 13px;
    color: #9ca3b8;
    line-height: 1.55;
  }
  .lede code {
    background: rgba(94, 234, 212, 0.12);
    color: #5eead4;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
  }
  .close {
    background: transparent;
    border: none;
    color: #6b7488;
    font-size: 22px;
    line-height: 1;
    cursor: pointer;
    padding: 0 6px;
    border-radius: 4px;
  }
  .close:hover { color: #e6e8ef; background: #1a1c26; }

  .body {
    padding: 20px 22px 8px;
    overflow-y: auto;
  }
  section { margin-bottom: 22px; }
  section:last-child { margin-bottom: 8px; }
  h3 {
    margin: 0 0 8px 0;
    text-transform: uppercase;
    letter-spacing: 1.4px;
    font-size: 10.5px;
    font-weight: 700;
    color: #6b7488;
  }

  .steps {
    margin: 0;
    padding-left: 22px;
    font-size: 13px;
    line-height: 1.7;
    color: #d6d8e0;
  }
  .steps li { margin-bottom: 4px; }
  .steps strong { color: #5eead4; font-weight: 600; }
  kbd {
    display: inline-block;
    padding: 1px 6px;
    margin: 0 1px;
    background: #1a1c26;
    border: 1px solid #2a2d3a;
    border-bottom-width: 2px;
    border-radius: 4px;
    color: #e6e8ef;
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
    line-height: 1.4;
  }
  p {
    margin: 0;
    font-size: 13px;
    line-height: 1.65;
    color: #d6d8e0;
  }
  em { color: #c084fc; font-style: italic; }

  footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 14px 22px;
    border-top: 1px solid #1f212d;
    background: #0e0f17;
  }
  .btn-primary {
    background: #5eead4;
    color: #0a0a0f;
    border: none;
    padding: 7px 18px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-primary:hover { background: #2dd4bf; }
</style>

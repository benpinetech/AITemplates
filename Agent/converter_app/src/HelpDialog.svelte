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
          <h2 id="help-title">JDA → Pine Converter</h2>
          <p class="lede">
            Migrates legacy JDA template syntax (<code>%[…]</code>) to Pine template
            syntax (<code>@[…]</code>). Open a document, run Convert, and refine
            the result by clicking individual tokens.
          </p>
        </div>
        <button class="close" onclick={close} aria-label="Close">×</button>
      </header>

      <div class="body">
        <section>
          <h3>Workflow</h3>
          <ol class="steps">
            <li>
              <strong>Open</strong> — pick a JDA RTF (the source pane fills in
              immediately).
            </li>
            <li>
              <strong>Convert</strong> — the v2 pipeline rewrites
              <code>%[…]</code> tokens into Pine <code>@[…]</code> tokens. Pattern
              matches are deterministic; anything unmatched falls back to the LLM.
            </li>
            <li>
              <strong>Review</strong> — every Pine token is a teal chip in the
              right pane. Hover for provenance (pattern id, source JDA, issues).
            </li>
            <li>
              <strong>Edit</strong> — click any chip to open the inline editor.
              Pick the scope (template / audience / global) the edit should
              persist as a suggestion for, then press <kbd>Enter</kbd>.
            </li>
            <li>
              <strong>Save</strong> — writes the converted RTF to disk. Edits
              you committed are already spliced into it.
            </li>
          </ol>
        </section>

        <section>
          <h3>Keyboard shortcuts</h3>
          <table class="shortcuts">
            <tbody>
              <tr>
                <td><kbd>Ctrl</kbd>/<kbd>⌘</kbd> + <kbd>,</kbd></td>
                <td>Open OpenAI settings</td>
              </tr>
              <tr>
                <td><kbd>F1</kbd></td>
                <td>Open this help dialog</td>
              </tr>
              <tr>
                <td><kbd>Enter</kbd></td>
                <td>Commit the edit in the popover</td>
              </tr>
              <tr>
                <td><kbd>Esc</kbd></td>
                <td>Cancel the edit / close a dialog</td>
              </tr>
              <tr>
                <td><kbd>Tab</kbd></td>
                <td>Move keyboard focus across chips</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section>
          <h3>Token colors</h3>
          <ul class="legend">
            <li>
              <span class="swatch legacy">%[Cust_Name]</span>
              <span class="legend-text">JDA source token. Read-only — left pane only.</span>
            </li>
            <li>
              <span class="swatch pine">@[Complainant.first.NameFirst]</span>
              <span class="legend-text">Pine output. Click to edit.</span>
            </li>
            <li>
              <span class="swatch edited">@[Complainant.full]</span>
              <span class="legend-text">Edited and persisted as a suggestion.</span>
            </li>
            <li>
              <span class="swatch error">@[…]</span>
              <span class="legend-text">Persist failed — hover the chip to see why.</span>
            </li>
          </ul>
        </section>

        <section>
          <h3>Where edits go</h3>
          <p>
            Each accepted edit is written to the suggestion store under the
            scope you pick. The next time you convert a template in that
            scope, the suggestion is applied automatically — patterns first,
            then suggestions, then the LLM fallback. The converter is
            <em>convert-once-then-edit</em>: the visible Pine document is the
            authoritative output for this session, not a preview waiting on a
            re-run.
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
  .steps code, p code {
    background: rgba(94, 234, 212, 0.12);
    color: #5eead4;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
  }

  .shortcuts {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .shortcuts td {
    padding: 5px 0;
    color: #d6d8e0;
  }
  .shortcuts td:first-child {
    width: 220px;
    color: #9ca3b8;
  }
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

  .legend {
    margin: 0;
    padding: 0;
    list-style: none;
    display: grid;
    gap: 8px;
  }
  .legend li {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 13px;
    color: #d6d8e0;
  }
  .swatch {
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px solid transparent;
    white-space: nowrap;
  }
  .swatch.legacy {
    background: #fff1e0;
    color: #9a3412;
    border-color: #fcd9b6;
  }
  .swatch.pine {
    background: #e6fbf5;
    color: #0f766e;
    border-color: #a7e9dd;
  }
  .swatch.edited {
    background: #fef3c7;
    color: #92400e;
    border-color: #fcd34d;
  }
  .swatch.error {
    background: #fee2e2;
    color: #b91c1c;
    border-color: #fca5a5;
  }
  .legend-text { color: #9ca3b8; }

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

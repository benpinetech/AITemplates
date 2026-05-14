# JDA → Pine Converter

Desktop GUI for the v2 conversion pipeline. Electron + Svelte
frontend, Python sidecar backend.

Replaces the earlier PySide6 implementation. See
[`../LLM_CAPABILITY_FINDINGS.md`](../LLM_CAPABILITY_FINDINGS.md) for
the empirical findings driving the underlying pipeline design and
[`../v2/README.md`](../v2/README.md) for the v2 architecture.

## Run from source

```bash
make run:converter        # from the repo root
```

The make target self-heals the common Electron postinstall problem
on newer Node versions (extract-zip + Node ≥ 26 incompatibility); it
falls back to manually unzipping the cached download if the
postinstall didn't.

You need:
- **Node ≥ 20** for the Electron shell (`node --version`)
- The project's Python venv at `venv/` for the pipeline (`make install`
  from the repo root creates it)

For end-user installs, neither of those matters — see "Build a
distributable installer" below.

## How it's wired

```
   ┌───────────────────────────────┐
   │  Renderer  (src/App.svelte)   │
   │  - two-pane document UI       │
   │  - click-to-edit on tokens    │
   │  - never touches Node/fs      │
   └───────────────┬───────────────┘
                   │  window.api.{openRtf, convertRtf, saveRtf}
                   ▼
   ┌───────────────────────────────┐
   │  Preload  (electron/preload.cjs)
   │  - exposes typed window.api   │
   │  - contextBridge isolates it  │
   └───────────────┬───────────────┘
                   │  ipcRenderer.invoke()
                   ▼
   ┌───────────────────────────────┐
   │  Main  (electron/main.cjs)    │
   │  - BrowserWindow lifecycle    │
   │  - file dialogs, fs read/write│
   │  - spawns Python pipeline     │
   └───────────────┬───────────────┘
                   │  child_process.spawn(python, "-m Agent.v2.tools.convert ... --json")
                   ▼
   ┌───────────────────────────────┐
   │  Python pipeline              │
   │  Agent/v2/                    │
   │  - emits JSON bundle on stdout│
   │    (schema: jda-pine-convert/v1)
   └───────────────────────────────┘
```

**JSON wire format** lives in `Agent/v2/tools/convert.py::_result_to_json`
— add fields there if the renderer needs more data.

**No direct Node access from the renderer.** `contextIsolation: true`
+ `nodeIntegration: false` in `main.cjs`. The only surface the
renderer can call is `window.api`, defined in `preload.cjs`.

## Layout

```
converter_app/
├── README.md
├── index.html              ← Vite entry + Google Fonts
├── package.json            ← Electron, Vite, Svelte, electron-builder
├── vite.config.js
├── electron/
│   ├── main.cjs            ← Electron main process (BrowserWindow + IPC handlers + pipeline spawn)
│   └── preload.cjs         ← contextBridge surface: window.api
└── src/
    ├── main.js             ← Svelte mount
    └── App.svelte          ← the whole UI (header, panes, status, styles)
```

## Build a distributable installer

```bash
cd Agent/converter_app
npm run build           # current OS
npm run build:win       # Windows NSIS .exe
npm run build:mac       # macOS .dmg
npm run build:linux     # .AppImage + .deb
```

Output in `release/`. Roughly 120–180 MB per platform — bundled
Chromium dominates. End user only needs to run the installer; no
Python, no Node, no toolchain.

### Bundling the Python pipeline as a sidecar

For end users to NOT need Python:

1. PyInstaller the v2 pipeline into a single-file sidecar:
   ```bash
   pyinstaller --onefile --name jda_pine_sidecar \
       --add-data "Agent/v2/grammar:v2/grammar" \
       --add-data "Agent/v2/patterns/library:v2/patterns/library" \
       Agent/v2/tools/convert.py
   ```
2. Place the resulting `jda_pine_sidecar` (or `.exe`) at
   `Agent/converter_app/binaries/jda_pine_sidecar`.
3. `package.json` already declares the `extraResources` mounts and
   `electron-builder` will bundle them.
4. `electron/main.cjs` already detects `app.isPackaged === true` and
   invokes the bundled sidecar instead of the venv Python. Same JSON
   wire format, no renderer code changes.

## Visual style

Dark canvas (`#0a0a0f`), cyan accent (`#5eead4`), Crimson Pro for
the document body, JetBrains Mono for bracketed tokens. Hover any
`%[…]` or `@[…]` chip → click handler is pending implementation
(see "Next") but the cursor + outline state is wired.

## What's not yet here

These were features of the PySide6 app that haven't been ported yet:

- **Inline edit popup on token click** — the click handlers are
  stubs; the popup UI + persist flow needs porting.
- **Scoped suggestion persistence** — `suggestion_store.accept_suggestion`
  is still in `v2/engine/`; the UI to call it is missing.
- **Theme picker** — current dark theme is hardcoded.
- **Settings dialog** — API key, custom model id. For dev,
  `OPENAI_API_KEY` in the repo-root `.env` works because
  `main.cjs` propagates `process.env` to the child Python.
- **Live RTF rendering** — currently shows plain text with token
  chips. A future enhancement could use the existing dotnet rtf
  rendering sidecar from the Streamlit app.

Next priorities, roughly in order:

1. Inline edit popup (click token → small modal with editor + scope
   picker + Save / Save+persist / Reset).
2. Wire Save+persist to `v2.engine.suggestion_store.accept_suggestion`
   via a new IPC handler.
3. Settings dialog (API key in keytar or similar, model picker).
4. Themes via swappable CSS files.

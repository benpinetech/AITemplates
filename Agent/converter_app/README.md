# JDA → Pine Converter (desktop)

Desktop GUI for running the v2 converter on a JDA RTF and reviewing
the result. Built on **PySide6**.

The Streamlit GUI under `../gui/` stays as the evaluation / testing
tool. This app is for the day-to-day mapper workflow: open file,
convert, review suggestions, save.

## Run from source

From the repo root:

```bash
./venv/bin/pip install -r Agent/v2/requirements.txt   # pytest etc.
./venv/bin/pip install PySide6 keyring openai          # GUI + LLM SDK
PYTHONPATH=Agent ./venv/bin/python -m converter_app    # launch
```

Or, equivalently, `make run:converter` from the repo root.

> **Note**: do *not* run via `cd Agent && ../venv/bin/python -m converter_app`.
> Python 3.14 emits a `RuntimeWarning` from `site.py` when `sys.prefix`
> contains `..`. Setting `PYTHONPATH=Agent` and invoking the venv
> through its canonical path avoids that warning.

## How the GUI is wired

```
File menu → Open RTF
        ↓
   ConversionWorker (QThread)
        ↓
   v2.pipeline.convert_template(rtf, org)
        ↓
   ConversionResult emitted back to MainWindow
        ↓
   ┌──────────────┬──────────────┐
   │ Source RTF   │ Converted RTF │   ← QTextEdit (read-only)
   ├──────────────┴──────────────┤
   │ Suggestions │ Issues │ Segments │   ← bottom tabs
   │             │        │          │
   │ Accept / Reject buttons → suggestion_store.accept_/reject_suggestion
   └─────────────────────────────────┘
```

## Settings persistence

- Org choice, model, base URL, last-opened path, and window geometry
  are stored in Qt's `QSettings` (platform-native — `~/Library/Preferences`,
  `HKEY_CURRENT_USER\Software`, `~/.config`). None of these are sensitive.
- The OpenAI API key is stored in the OS keyring (Keychain on macOS,
  Credential Manager on Windows, Secret Service on Linux). It is **not**
  written to a settings file.

## Security — where the API key does and does not go

Defense-in-depth around the API key:

| Path | Status |
|---|---|
| OS keychain (Keychain / Credential Manager / Secret Service) | **Stored here** — only place it lives |
| QSettings (the platform-native plist / registry / `~/.config`) | Never written |
| `os.environ` during the conversion run | Never written — the key passes directly to the SDK constructor in-memory (covered by `test_worker_passes_key_via_constructor`) |
| RTF / TOML / project files inside the repo | Never written |
| User-visible error messages and tracebacks | Routed through `secrets_redact.scrub` which replaces any `sk-…` / `Bearer …` substring with `[REDACTED]` before display (covered by `test_secrets_redact.py`) |
| `Agent/v2/suggestions/` files | Only contain bracketed JDA / Pine syntax — no secrets, by construction |

`.env` and friends are gitignored by the repo root `.gitignore`, but
because the key is *never* written to a project-relative file, there
is nothing to gitignore on the secret path. The defense relies on:

1. Storage living only in the OS keychain.
2. The SDK constructor being passed the key in memory — never the
   environment.
3. A redaction filter on every user-visible string that surfaces from
   the converter.

If you ever clear the key, use **Settings → Clear stored key**.
That removes the keychain entry directly; nothing remains on disk.

## Building an installable

Single binary via PyInstaller:

```bash
./venv/bin/pip install pyinstaller
./venv/bin/pyinstaller --windowed --name "JDA Pine Converter" \
    --add-data "Agent/v2/grammar:v2/grammar" \
    --add-data "Agent/v2/patterns/library:v2/patterns/library" \
    Agent/converter_app/app.py
```

The result lands under `dist/`. Wrap with platform installers as needed:
- macOS: `.dmg` via `hdiutil` or `dmgbuild`
- Windows: `.exe` installer via Inno Setup or NSIS
- Linux: `.AppImage` via appimagetool

A future `jda_pine_converter.spec` file will codify the bundled-data
list once we know what additional resources need shipping (icons,
QSS, etc.).

## Layout

```
converter_app/
├── pyproject.toml
├── README.md            ← this file
├── __init__.py
├── app.py               ← entry point: instantiate QApplication, MainWindow
├── main_window.py       ← MainWindow + toolbar + viewers + status bar
├── conversion_worker.py ← QThread that runs pipeline.convert_template
├── settings.py          ← QSettings + keyring wrappers
├── widgets/
│   ├── __init__.py
│   ├── suggestion_panel.py  ← LLM suggestions list with Accept/Reject
│   ├── issues_panel.py       ← validation errors / warnings
│   └── segments_panel.py     ← per-segment provenance table
└── resources/
    └── styles.qss        ← QSS theme
```

## What's deliberately NOT in v0.1

- Pattern library editor (use TOML files directly for now).
- Multi-file batch processing (open one RTF at a time).
- Pre-render of full RTF formatting via the dotnet sidecar — this app
  shows source/converted as styled text with bracketed-expression
  highlighting, not a Word-style rendered preview. The Streamlit
  evaluation app keeps the rendered preview if needed.
- Auto-update / "check for updates" — wire that in once we have a
  release process.

# Building the JDA → Pine Converter installer

This produces a standalone **Windows installer** (`.exe`) that anyone
can double-click to install and run the converter — no Python, no
Node.js, no command line required on the end-user's machine.

## What ends up on the user's machine

The installer bundles two things so the app is fully self-contained:

1. **The Electron desktop app** (`converter_app/`) — the GUI.
2. **The `jda_pine_sidecar` binary** — the LLM conversion pipeline,
   frozen by PyInstaller into a standalone executable. The end user
   does **not** need Python installed; the sidecar carries its own
   interpreter and dependencies.

At runtime the Electron app spawns the sidecar at
`…/resources/binaries/jda_pine_sidecar/jda_pine_sidecar.exe` and talks
to it over a small JSON protocol (see `pipeline/tools/convert.py
--json`).

> **The converter is LLM-only.** Each user must enter their own OpenAI
> API key in **Settings** (gear menu) before converting — the
> **Convert** button stays disabled until a key is saved. The key is
> stored locally in the app's `userData` folder and is **not** baked
> into the installer.

## Prerequisites (build machine only)

You build **on Windows** because PyInstaller cannot cross-compile — a
Windows `.exe` sidecar can only be frozen on Windows. The build machine
needs:

- **Python 3.11+** — <https://www.python.agency/> (check "Add to PATH")
- **Node.js 18+** — <https://nodejs.agency/>

The *end users* need neither.

## Build it (one command)

From the repo root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File build-windows.ps1
```

The script:

1. Creates a clean `build-venv\` and installs the sidecar's minimal
   runtime deps (`requirements-sidecar.txt`) + PyInstaller.
2. Freezes the pipeline → `dist\jda_pine_sidecar\` (via `sidecar.spec`).
3. Copies that bundle into `converter_app\binaries\`.
4. Runs `npm` + `electron-builder` to produce the installer.

**Output:**

```
converter_app\release\JDA Pine Converter Setup 0.1.0.exe
```

Ship that single `.exe`.

## Installing & first run (end user)

1. Double-click the `Setup` `.exe`.
2. **Unsigned-app warning:** the installer is not code-signed, so
   Windows SmartScreen shows *"Windows protected your PC."* Click
   **More info → Run anyway**. (This is expected; see "Code signing"
   below to remove it.)
3. The app installs per-user (no admin prompt) and adds Desktop +
   Start-Menu shortcuts.
4. On first launch, open **Settings** and paste an OpenAI API key, then
   convert.

## Code signing (optional, removes the SmartScreen warning)

The installer currently ships **unsigned**. To sign it, obtain an
Authenticode certificate (`.pfx`) and set these env vars before running
the build script:

```powershell
$env:CSC_LINK = "C:\path\to\cert.pfx"
$env:CSC_KEY_PASSWORD = "…"
```

electron-builder picks them up automatically. (Note: a brand-new OV
cert still accrues SmartScreen reputation over the first downloads; an
EV cert or Azure Trusted Signing avoids that.)

## Updating the app

Bump `version` in `converter_app/package.json`, rebuild, and ship the
new `Setup` `.exe`. There is no auto-updater wired up yet.

## Building for macOS / Linux

The same `npm run build:mac` / `build:linux` targets exist, but each
must run on its own OS to freeze a native sidecar first. The Linux path
is validated as the CI/proxy build; macOS is untested. The icon assets
live in `converter_app/build/` (`icon.ico`, `icon.png`).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Build fails at PyInstaller step | A `dist\jda_pine_sidecar` is locked (app running). Close it and re-run. |
| App opens but Convert is disabled | No API key saved — open **Settings** and add one. |
| "pipeline produced non-JSON output" | The sidecar crashed. Run `…\resources\binaries\jda_pine_sidecar\jda_pine_sidecar.exe convert <file> --agency oba --json` from a terminal to see the stderr. |
| SmartScreen blocks the installer | Expected (unsigned). **More info → Run anyway**, or sign it. |

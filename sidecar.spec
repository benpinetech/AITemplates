# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the jda_pine_sidecar binary.
#
# Build from the repo root:
#   pip install pyinstaller
#   pyinstaller sidecar.spec
#
# Output: dist/jda_pine_sidecar/  (onedir bundle; the launcher exe is
#         dist/jda_pine_sidecar/jda_pine_sidecar[.exe])
# Then:   copy the whole dist/jda_pine_sidecar/ dir to
#         converter_app/binaries/jda_pine_sidecar/
#
# onedir (not onefile) is deliberate: a onefile binary self-extracts to
# a temp dir on every launch, which is slow and a frequent trigger for
# Windows antivirus/SmartScreen heuristics. onedir starts immediately
# and ships as plain files.

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

REPO_ROOT = Path(SPECPATH)  # noqa: F821  (SPECPATH set by PyInstaller)

# ── Data files bundled into the binary ───────────────────────────────────────
_grammar = REPO_ROOT / "pipeline" / "grammar"
datas = [
    # Read-only grammar config (pine grammar, data model, lint rules) — only the
    # top-level .toml files, NOT agency_overrides/. Agencies are user data and
    # live in the writable userData dir at runtime (JDA_AGENCY_DIR; see
    # converter_app/electron/main.cjs), so NO agency configs are bundled into
    # the install — a fresh install starts with an empty agency list.
    *[(str(f), "pipeline/grammar") for f in sorted(_grammar.glob("*.toml"))],
    # LLM few-shot examples
    (str(REPO_ROOT / "pipeline" / "engine" / "llm_examples.toml"), "pipeline/engine"),
    # Pine field reference used as LLM context
    (str(REPO_ROOT / "pine_context.md"), "."),
]
# NOTE: the deterministic pattern matcher was removed (commit 6f15cac);
# the pipeline is now LLM-only. There is no longer a
# pipeline/patterns/library/ directory to bundle.

# ── Hidden imports PyInstaller can't auto-detect ──────────────────────────────
# Runtime third-party deps are intentionally minimal: openai (+ its httpx /
# anyio transport), python-dotenv, and pydantic. The deterministic pattern
# matcher (and its striprtf/tiktoken usage) was removed; RTF parsing is now
# pure-regex with no third-party RTF lib.
hiddenimports = [
    # TOML parsing — stdlib tomllib (3.11+)
    "tomllib",
    # OpenAI / httpx transport
    "openai",
    "httpx",
    "httpx._transports.default",
    "anyio",
    "anyio._backends._asyncio",
    # python-dotenv
    "dotenv",
    # pipeline submodules (dynamic imports via importlib or __init__ re-exports)
    *collect_submodules("pipeline"),
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [str(REPO_ROOT / "pipeline" / "tools" / "sidecar.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not needed in the sidecar — keeps the bundle small. These are
        # dev/eval-only deps (or v1 leftovers) that must never be pulled in.
        "streamlit",
        "altair",
        "pandas",
        "matplotlib",
        "langchain",
        "langchain_core",
        "langchain_openai",
        "langchain_community",
        "langchain_chroma",
        "chromadb",
        "tiktoken",
        "striprtf",
        "pytest",
        "playwright",
        "IPython",
        "notebook",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir: binaries collected by COLLECT below
    name="jda_pine_sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,   # UPX-packed exes are a common Windows AV false-positive
    console=True,   # pipeline is headless — needs stdout/stderr
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="jda_pine_sidecar",
)

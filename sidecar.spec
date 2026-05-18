# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the jda_pine_sidecar binary.
#
# Build from the repo root:
#   pip install pyinstaller
#   pyinstaller sidecar.spec
#
# Output: dist/jda_pine_sidecar  (or .exe on Windows)
# Then:   cp dist/jda_pine_sidecar converter_app/binaries/

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

REPO_ROOT = Path(SPECPATH)  # noqa: F821  (SPECPATH set by PyInstaller)

# ── Data files bundled into the binary ───────────────────────────────────────
datas = [
    # Grammar TOML files (org overrides, lint rules, pine grammar)
    (str(REPO_ROOT / "pipeline" / "grammar"), "pipeline/grammar"),
    # Pattern library TOML files
    (str(REPO_ROOT / "pipeline" / "patterns" / "library"), "pipeline/patterns/library"),
    # LLM few-shot examples
    (str(REPO_ROOT / "pipeline" / "engine" / "llm_examples.toml"), "pipeline/engine"),
    # Pine field reference used as LLM context
    (str(REPO_ROOT / "pine_context.md"), "."),
]

# Collect any data files from third-party packages that register them
# via package metadata (tiktoken encoding tables, etc.)
datas += collect_data_files("tiktoken")
datas += collect_data_files("tiktoken_ext")

# ── Hidden imports PyInstaller can't auto-detect ──────────────────────────────
hiddenimports = [
    # TOML parsing — stdlib tomllib (3.11+) with tomli fallback
    "tomllib",
    "tomli",
    "toml",
    # OpenAI / httpx transport
    "openai",
    "httpx",
    "httpx._transports.default",
    "anyio",
    "anyio._backends._asyncio",
    # striprtf
    "striprtf",
    "striprtf.striprtf",
    # python-dotenv
    "dotenv",
    # tiktoken
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
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
        # Not needed in the sidecar — saves ~100MB
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
    a.binaries,
    a.datas,
    [],
    name="jda_pine_sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # pipeline is headless — needs stdout/stderr
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

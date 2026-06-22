"""
Resolve paths to bundled data files in both dev and packaged (PyInstaller) mode.

PyInstaller extracts bundled data to a temp dir (sys._MEIPASS) and sets
sys.frozen = True. In dev we resolve relative to the repo root.
"""

import os
import sys
from pathlib import Path


def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Dev: this file is pipeline/_resource_path.py → repo root is parent.parent
    return Path(__file__).resolve().parent.parent


BASE = _base()

# Core data directories
GRAMMAR_DIR  = BASE / "pipeline" / "grammar"
PATTERNS_DIR = BASE / "pipeline" / "patterns" / "library"
LLM_EXAMPLES = BASE / "pipeline" / "engine" / "llm_examples.toml"
PINE_CONTEXT = BASE / "pine_context.md"

# Suggestions directory — writable, so NOT inside the frozen bundle.
# Packaged builds pass JDA_SUGGESTIONS_DIR via env; dev falls back to source tree.
def suggestions_dir() -> Path:
    env_override = os.environ.get("JDA_SUGGESTIONS_DIR")
    if env_override:
        p = Path(env_override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return BASE / "pipeline" / "suggestions"


# Agency-overrides directory — also user data (agencies are created/renamed/
# deleted at runtime), so it must be writable and live OUTSIDE the read-only
# frozen bundle. Packaged builds pass JDA_AGENCY_DIR (a userData path) via env;
# no agency configs are bundled into the install. Dev falls back to the source
# tree so the repo's reference configs (e.g. oba.toml) are available.
def agency_overrides_dir() -> Path:
    env_override = os.environ.get("JDA_AGENCY_DIR")
    if env_override:
        p = Path(env_override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return GRAMMAR_DIR / "agency_overrides"

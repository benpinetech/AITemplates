"""On-disk store for LLM-suggested mappings the mapper has reviewed.

Two operations from the GUI / CLI:

  - :func:`accept_suggestion` — persist the (jda, pine) pair as an
    exact-match deterministic pattern under
    ``Agent/v2/suggestions/verified/<org>/``. Subsequent pipeline
    runs see this pattern alongside the hand-authored library and
    match it without calling the LLM.
  - :func:`reject_suggestion` — append a JSON record to
    ``Agent/v2/suggestions/rejected.log``. No effect on future
    pipeline runs (today); the audit trail enables future
    negative-shot prompting if we want it.

The store never mutates the hand-authored pattern library. Verified
suggestions layer **on top** so you can delete or hand-edit them
freely without affecting the seed patterns.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional

from ..patterns import loader as pattern_loader
from ..patterns.schema import Pattern


# Default on-disk locations. Tests pass an explicit ``root`` to keep
# the real directory clean.
SUGGESTIONS_DIR = Path(__file__).resolve().parent.parent / "suggestions"
VERIFIED_DIRNAME = "verified"
REJECTED_LOG_NAME = "rejected.log"


def _suggestion_id(jda_text: str, pine_text: str) -> str:
    """Stable sha1-based id. Same (jda, pine) → same id, so accepting
    the same suggestion twice is idempotent (overwrites the same file)."""
    h = hashlib.sha1((jda_text + "→" + pine_text).encode("utf-8")).hexdigest()[:8]
    return f"verified_{h}"


def _safe_org(org: str) -> str:
    """Reject paths / weird characters before they reach the
    filesystem. Org slugs are lowercase letters/digits/hyphens only."""
    if not re.fullmatch(r"[a-z0-9_\-]+", org or ""):
        raise ValueError(f"invalid org slug for filesystem: {org!r}")
    return org


def _toml_quote(s: str) -> str:
    """TOML literal string requires escaping single quotes — but TOML
    doesn't allow ``\'`` in literal strings at all, so we use a
    triple-quoted basic string when the input contains a ``'``. For the
    common case (no apostrophe) a single-quoted literal string is used,
    which preserves backslashes verbatim — important for ``Subdocument(Template\\X)``.
    """
    if "'" in s:
        # Triple-quoted basic string allows escapes; escape backslashes
        # and double quotes.
        escaped = s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return f'"""{escaped}"""'
    return f"'{s}'"


def _suggestion_toml(
    jda_text: str, pine_text: str, sug_id: str, org: str,
    source_template: Optional[str] = None,
    source_segment_index: Optional[int] = None,
) -> str:
    """Build the TOML body for a verified suggestion.

    The pattern has NO holes — match and rewrite are the exact text the
    LLM produced. Higher priority than the seed library so this entry
    wins on exact match.
    """
    when = datetime.datetime.now().isoformat(timespec="seconds")
    notes_bits = [f"accepted {when}", f"org={org}"]
    if source_template:
        notes_bits.append(f"source={source_template}")
    if source_segment_index is not None:
        notes_bits.append(f"segment_index={source_segment_index}")
    notes = "; ".join(notes_bits)
    return (
        "[[pattern]]\n"
        f"id          = {_toml_quote(sug_id)}\n"
        "description = \"verified LLM suggestion (exact-match cache)\"\n"
        "provenance  = \"llm-generated\"\n"
        "verification = \"verified\"\n"
        f"notes       = {_toml_quote(notes)}\n"
        f"org_context = {_toml_quote(org)}\n"
        "priority    = 150\n"
        "\n"
        f"match   = {_toml_quote(jda_text)}\n"
        f"rewrite = {_toml_quote(pine_text)}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────


def load_verified_for_org(
    org: str, root: Optional[Path] = None
) -> List[Pattern]:
    """Load every verified-suggestion TOML for the given org as a list
    of Pattern objects. Empty list if the directory doesn't exist or has
    no files yet."""
    if root is None:
        root = SUGGESTIONS_DIR
    org_dir = Path(root) / VERIFIED_DIRNAME / _safe_org(org)
    if not org_dir.exists():
        return []
    report = pattern_loader.load_library(org_dir)
    # If the verified directory contained malformed files, surface the
    # issues but don't block — the rest of the pipeline can still run.
    if not report.ok:
        for issue in report.issues:
            # Print to stderr so they surface but don't crash.
            import sys
            print(
                f"warning: verified suggestion at {issue.file} ({issue.pattern_id}): "
                f"{issue.message}",
                file=sys.stderr,
            )
    return report.patterns


# ─────────────────────────────────────────────────────────────────────────────
# Accept
# ─────────────────────────────────────────────────────────────────────────────


def accept_suggestion(
    jda_text: str,
    pine_text: str,
    org: str,
    *,
    source_template: Optional[str] = None,
    source_segment_index: Optional[int] = None,
    root: Optional[Path] = None,
) -> Path:
    """Persist an accepted LLM suggestion. Returns the path of the
    written file.

    Idempotent — accepting the same (jda, pine) twice overwrites the
    same TOML.
    """
    if root is None:
        root = SUGGESTIONS_DIR
    org_safe = _safe_org(org)
    sug_id = _suggestion_id(jda_text, pine_text)
    org_dir = Path(root) / VERIFIED_DIRNAME / org_safe
    org_dir.mkdir(parents=True, exist_ok=True)
    path = org_dir / f"{sug_id}.toml"
    path.write_text(
        _suggestion_toml(
            jda_text, pine_text, sug_id, org_safe,
            source_template=source_template,
            source_segment_index=source_segment_index,
        ),
        encoding="utf-8",
    )
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Reject
# ─────────────────────────────────────────────────────────────────────────────


def reject_suggestion(
    jda_text: str,
    pine_text: str,
    org: str,
    *,
    reason: str = "",
    source_template: Optional[str] = None,
    source_segment_index: Optional[int] = None,
    root: Optional[Path] = None,
) -> None:
    """Append a JSONL record for a rejected suggestion. No effect on
    pattern matching today — purely an audit trail."""
    if root is None:
        root = SUGGESTIONS_DIR
    log_path = Path(root) / REJECTED_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "org": org,
        "jda": jda_text,
        "pine": pine_text,
        "reason": reason,
        "source_template": source_template,
        "source_segment_index": source_segment_index,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def is_suggestion_accepted(
    jda_text: str,
    pine_text: str,
    org: str,
    root: Optional[Path] = None,
) -> bool:
    """True if this exact (jda, pine) pair has already been accepted."""
    if root is None:
        root = SUGGESTIONS_DIR
    org_safe = _safe_org(org)
    sug_id = _suggestion_id(jda_text, pine_text)
    return (Path(root) / VERIFIED_DIRNAME / org_safe / f"{sug_id}.toml").exists()

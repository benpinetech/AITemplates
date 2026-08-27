"""On-disk store for verified Pine suggestions (LLM-proposed or
human-edited) that should re-apply deterministically on future runs.

A suggestion is stored as a tiny single-pattern TOML file. The pattern
has no holes — it's an exact-match rewrite of one JDA token text to one
Pine token text.

### Scoping

Suggestions live under a scope to limit when they re-apply. Scope is
encoded in the directory layout, not in the pattern schema:

    suggestions/verified/<agency>/
        global/                  ← re-applies on every template (the
                                   "exact-match cache" hazard documented
                                   in PROJECT_STATUS.md; default OFF)
        by_template/<name>/      ← re-applies only when the source
                                   template's filename equals <name>
        by_audience/<name>/      ← re-applies only when the document
                                   audience classifier returns <name>
                                   (e.g. "complainant", "respondent")

The loader is the gatekeeper: callers pass ``template_name`` and
``audience`` for the current document, and only the matching subdirs
are walked. A scoped suggestion that doesn't match the current document
is invisible.

### Priority

Scoped overrides (``template`` or ``audience``) get **priority 500** so
they beat hand-written seed patterns (default priority 100). Within a
scope, a converter's deliberate "the pattern is wrong here" override
takes effect immediately. Global suggestions stay at priority 150 —
above the seed patterns, but below scoped overrides — preserving the
old auto-accept semantics for callers that pass ``scope=("global",
"")``.

### Public API

    accept_suggestion(jda, pine, agency, *, scope=..., source_template=...)
    reject_suggestion(jda, pine, agency, *, reason=..., source_template=...)
    load_verified_for_agency(agency, *, template_name=..., audience=...)
    is_suggestion_accepted(jda, pine, agency, *, scope=...)
    list_scoped_suggestions(agency, *, template_name=..., audience=...)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from ..patterns import loader as pattern_loader
from ..patterns.schema import Pattern


# Accepts either a single string (the common 1:1 case) or a sequence of
# strings (multi-token chunk mapping). Internal code normalizes to a
# list immediately so the rest of the module sees one shape.
TokenInput = Union[str, Sequence[str]]


# Default on-disk locations. Tests pass an explicit ``root`` to keep
# the real directory clean. The ``JDA_SUGGESTIONS_DIR`` env var (read by
# ``_resource_path.suggestions_dir`` at import) lets callers — notably the
# packaged Electron host and end-to-end tests — point all reads + writes at
# a per-process or per-test temp directory without threading an explicit
# ``root=`` through every layer.
from pipeline._resource_path import suggestions_dir as _suggestions_dir

SUGGESTIONS_DIR = _suggestions_dir()


def _resolve_root(root: Optional[Path]) -> Path:
    """Pick the on-disk root: caller-supplied wins, then module-level default."""
    if root is not None:
        return Path(root)
    return SUGGESTIONS_DIR
VERIFIED_DIRNAME = "verified"
REJECTED_LOG_NAME = "rejected.log"

# Scope encoding.
SCOPE_GLOBAL = "global"
SCOPE_TEMPLATE = "template"
SCOPE_AUDIENCE = "audience"

_VALID_SCOPE_KINDS = (SCOPE_GLOBAL, SCOPE_TEMPLATE, SCOPE_AUDIENCE)

# Priorities. Seed patterns default to 100; verified-global is 150 (the
# original behavior); scoped overrides are 500 so the converter's edit
# wins inside its scope.
PRIORITY_GLOBAL = 150
PRIORITY_SCOPED = 500


Scope = Tuple[str, str]  # (kind, value); value is "" for global


def normalize_scope(scope: Optional[Scope]) -> Scope:
    """Coerce a scope tuple to the canonical form. ``None`` means global."""
    if scope is None:
        return (SCOPE_GLOBAL, "")
    kind, value = scope
    if kind not in _VALID_SCOPE_KINDS:
        raise ValueError(
            f"unknown scope kind {kind!r}; expected one of {_VALID_SCOPE_KINDS}"
        )
    if kind == SCOPE_GLOBAL:
        return (SCOPE_GLOBAL, "")
    if not value:
        raise ValueError(f"scope kind {kind!r} requires a non-empty value")
    return (kind, value)


def _safe_agency(agency: str) -> str:
    """Reject paths / weird characters before they reach the
    filesystem. Org slugs are lowercase letters/digits/hyphens only."""
    if not re.fullmatch(r"[a-z0-9_\-]+", agency or ""):
        raise ValueError(f"invalid agency slug for filesystem: {agency!r}")
    return agency


def agency_verified_dir(agency: str, root: Optional[Path] = None) -> Path:
    """Absolute path to an agency's verified-suggestion tree."""
    return _resolve_root(root) / VERIFIED_DIRNAME / _safe_agency(agency)


def delete_agency_suggestions(agency: str, root: Optional[Path] = None) -> bool:
    """Remove an agency's entire verified-suggestion tree. Returns True if
    a directory was actually removed (False if there was nothing there)."""
    import shutil
    d = agency_verified_dir(agency, root)
    if d.exists():
        shutil.rmtree(d)
        return True
    return False


def move_agency_suggestions(
    old: str, new: str, root: Optional[Path] = None
) -> bool:
    """Move an agency's verified-suggestion tree to a new agency id (used by
    rename). Returns True if a move happened. No-op if ``old == new`` or the
    source is absent; raises ``FileExistsError`` if the destination exists."""
    import shutil
    if old == new:
        return False
    src = agency_verified_dir(old, root)
    if not src.exists():
        return False
    dst = agency_verified_dir(new, root)
    if dst.exists():
        raise FileExistsError(f"suggestions already exist for agency {new!r}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


# Scope-value slugs allow uppercase + dot/space (template filenames look
# like "Letter to C.rtf"). Forbid path separators and parents.
_SCOPE_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-. ]+$")


def _safe_scope_value(value: str) -> str:
    if value == "":
        return value
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"invalid scope value (path traversal): {value!r}")
    if not _SCOPE_VALUE_RE.fullmatch(value):
        raise ValueError(f"invalid scope value: {value!r}")
    return value


def _coerce_token_list(value: TokenInput) -> List[str]:
    """Normalize TokenInput to a list. Strings become single-element
    lists; sequences are listified. Empty inputs are allowed only on
    the rewrite side (drop patterns) — callers are responsible for
    refusing an empty match list."""
    if isinstance(value, str):
        return [value]
    return list(value)


def _suggestion_id(
    jda_tokens: Sequence[str], pine_tokens: Sequence[str], scope: Scope,
) -> str:
    """Stable sha1-based id. Same (jda, pine, scope) → same id, so
    accepting the same suggestion twice within the same scope is
    idempotent. Different scopes produce different ids so the files
    don't collide.

    The join character ``|`` is chosen so single-token mappings stored
    via the old single-string API and the same mapping stored via the
    list API produce identical hashes: ``'|'.join(['x']) == 'x'``.
    """
    kind, value = scope
    jda_joined = "|".join(jda_tokens)
    pine_joined = "|".join(pine_tokens)
    key = f"{kind}:{value}:{jda_joined}→{pine_joined}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"verified_{h}"


def _scope_subdir(scope: Scope) -> Path:
    kind, value = normalize_scope(scope)
    if kind == SCOPE_GLOBAL:
        return Path("global")
    if kind == SCOPE_TEMPLATE:
        return Path("by_template") / _safe_scope_value(value)
    if kind == SCOPE_AUDIENCE:
        return Path("by_audience") / _safe_scope_value(value)
    raise ValueError(f"unhandled scope kind: {kind!r}")


def _scope_priority(scope: Scope) -> int:
    return PRIORITY_GLOBAL if scope[0] == SCOPE_GLOBAL else PRIORITY_SCOPED


def _toml_quote(s: str) -> str:
    """TOML literal string requires escaping single quotes — but TOML
    doesn't allow ``\\'`` in literal strings at all, so we use a
    triple-quoted basic string when the input contains a ``'``. For the
    common case (no apostrophe) a single-quoted literal string is used,
    which preserves backslashes verbatim — important for ``Subdocument(Template\\X)``.
    """
    if "'" in s:
        escaped = s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return f'"""{escaped}"""'
    return f"'{s}'"


def _toml_match_rewrite(values: Sequence[str]) -> str:
    """Render a match or rewrite field as either a scalar (single-token
    case) or an inline array (multi-token case). Empty list renders as
    ``[]`` — the drop-pattern shape.

    TOML allows mixed string-quote styles within an inline array, so
    elements that contain apostrophes can use triple-quoted basic
    strings while plain elements use the literal-string single-quote
    form."""
    if len(values) == 1:
        return _toml_quote(values[0])
    if not values:
        return "[]"
    inner = ", ".join(_toml_quote(v) for v in values)
    return f"[{inner}]"


def _suggestion_toml(
    jda_tokens: Sequence[str], pine_tokens: Sequence[str],
    sug_id: str, agency: str, scope: Scope,
    *,
    source_template: Optional[str] = None,
    source_segment_index: Optional[int] = None,
    note: Optional[str] = None,
) -> str:
    """Build the TOML body for a verified suggestion.

    ``jda_tokens`` / ``pine_tokens`` are sequences. Length-1 lists are
    written as scalar fields for readability; longer lists become inline
    TOML arrays so the pattern loader sees a chunk pattern. An empty
    ``pine_tokens`` list is allowed and renders as ``rewrite = []`` —
    a drop pattern that consumes the matched JDA token(s) and emits
    nothing.
    """
    when = datetime.datetime.now().isoformat(timespec="seconds")
    kind, value = scope
    notes_bits = [f"accepted {when}", f"agency={agency}", f"scope={kind}"]
    if value:
        notes_bits.append(f"scope_value={value}")
    if source_template:
        notes_bits.append(f"source={source_template}")
    if source_segment_index is not None:
        notes_bits.append(f"segment_index={source_segment_index}")
    if note:
        notes_bits.append(f"note={note}")
    # Multi-token mappings need to be obvious in the file's notes too,
    # since `match = [...]` and `rewrite = [...]` are easy to miss.
    if len(jda_tokens) != 1 or len(pine_tokens) != 1:
        notes_bits.append(
            f"shape={len(jda_tokens)}→{len(pine_tokens)}"
        )
    notes = "; ".join(notes_bits)
    priority = _scope_priority(scope)
    is_drop = len(pine_tokens) == 0
    description = (
        "verified drop pattern (consumes JDA, emits no Pine)" if is_drop
        else "verified suggestion (exact-match cache)"
    )
    return (
        "[[pattern]]\n"
        f"id          = {_toml_quote(sug_id)}\n"
        f"description = {_toml_quote(description)}\n"
        "provenance  = \"llm-generated\"\n"
        "verification = \"verified\"\n"
        f"notes       = {_toml_quote(notes)}\n"
        f"agency_context = {_toml_quote(agency)}\n"
        f"priority    = {priority}\n"
        "\n"
        f"match   = {_toml_match_rewrite(list(jda_tokens))}\n"
        f"rewrite = {_toml_match_rewrite(list(pine_tokens))}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────


def _active_scope_dirs(
    agency_dir: Path,
    template_name: Optional[str],
    audience: Optional[str],
) -> List[Path]:
    """Return the list of scope subdirectories that should be loaded for
    the current document. Always includes ``global`` if it exists; adds
    the matching template / audience subdirs when the inputs name them."""
    dirs: List[Path] = []
    global_dir = agency_dir / "global"
    if global_dir.exists():
        dirs.append(global_dir)
    if template_name:
        try:
            t_dir = agency_dir / "by_template" / _safe_scope_value(template_name)
        except ValueError:
            t_dir = None
        if t_dir and t_dir.exists():
            dirs.append(t_dir)
    if audience:
        try:
            a_dir = agency_dir / "by_audience" / _safe_scope_value(audience)
        except ValueError:
            a_dir = None
        if a_dir and a_dir.exists():
            dirs.append(a_dir)
    return dirs


def load_verified_for_agency(
    agency: str,
    *,
    root: Optional[Path] = None,
    template_name: Optional[str] = None,
    audience: Optional[str] = None,
    include_legacy_flat: bool = True,
) -> List[Pattern]:
    """Load verified-suggestion patterns whose scope matches the document.

    ``template_name`` and ``audience`` together determine which scoped
    subdirectories load. ``global`` always loads when it exists.

    ``include_legacy_flat``: if True, also picks up suggestions written
    by older versions that lived directly under ``verified/<agency>/`` (no
    scope subdir). They're treated as global-priority entries.

    Patterns with unparseable rewrites are dropped (with a stderr
    warning). The LLM occasionally produces a malformed Pine token
    that passes loader validation but blows up at rewrite time;
    filtering here prevents one bad cached suggestion from killing an
    entire eval run.
    """
    import sys

    root = _resolve_root(root)
    agency_dir = Path(root) / VERIFIED_DIRNAME / _safe_agency(agency)
    if not agency_dir.exists():
        return []

    dirs = _active_scope_dirs(agency_dir, template_name, audience)
    if include_legacy_flat:
        # Legacy layout: TOML files directly in the agency dir. Pre-scope
        # versions of this module wrote them there. Treat as global.
        legacy_dir = agency_dir
        legacy_files = [
            f for f in legacy_dir.glob("*.toml") if f.is_file()
        ]
    else:
        legacy_files = []

    patterns: List[Pattern] = []
    for scope_dir in dirs:
        report = pattern_loader.load_library(scope_dir)
        if not report.ok:
            for issue in report.issues:
                print(
                    f"warning: verified suggestion at {issue.file} "
                    f"({issue.pattern_id}): {issue.message}",
                    file=sys.stderr,
                )
        patterns.extend(report.patterns)

    # Legacy flat files: load each one individually to avoid recursive
    # double-loading of scope subdirs.
    if legacy_files:
        from ..patterns.loader import _load_one_file  # type: ignore
        issues: list = []
        for f in legacy_files:
            patterns.extend(_load_one_file(f, issues))
        for issue in issues:
            print(
                f"warning: legacy verified suggestion at {issue.file} "
                f"({issue.pattern_id}): {issue.message}",
                file=sys.stderr,
            )

    # Belt-and-braces: parse each rewrite eagerly. The loader only
    # checks pattern *syntax*; it doesn't try to actually parse the
    # Pine output the rewrite produces. Bad LLM outputs sneak past.
    from ..parser import pine_parser
    clean: List[Pattern] = []
    for p in patterns:
        rewrites = p.rewrite_tokens()
        if rewrites is None:
            clean.append(p)
            continue
        ok = True
        for r in rewrites:
            if "$" in r:
                continue
            try:
                # Fragment-aware: a rewrite may be a single token or a
                # multi-token block with literal glue between tokens.
                pine_parser.parse_fragment(r)
            except Exception as e:  # noqa: BLE001
                print(
                    f"warning: dropping verified suggestion {p.id!r}: "
                    f"unparseable rewrite {r!r}: {e}",
                    file=sys.stderr,
                )
                ok = False
                break
        if ok:
            clean.append(p)
    return clean


# ─────────────────────────────────────────────────────────────────────────────
# Accept
# ─────────────────────────────────────────────────────────────────────────────


def accept_suggestion(
    jda_text: TokenInput,
    pine_text: TokenInput,
    agency: str,
    *,
    scope: Optional[Scope] = None,
    source_template: Optional[str] = None,
    source_segment_index: Optional[int] = None,
    note: Optional[str] = None,
    root: Optional[Path] = None,
) -> Path:
    """Persist an accepted suggestion. Returns the path of the
    written file.

    ``jda_text`` and ``pine_text`` accept either a single string (the
    common 1:1 case) or a list of strings (a multi-token chunk
    mapping). ``pine_text`` may be an empty list to register a *drop
    pattern* — the matched JDA gets consumed and nothing is emitted in
    its place, useful for the OBA "drop StateIDNum / OBAAttorney.Title"
    cases. An empty ``jda_text`` is rejected.

    Idempotent within a scope — accepting the same content at the same
    scope twice overwrites the same TOML. Different scopes produce
    distinct files so a global accept doesn't collide with a per-
    template override of the same mapping.

    Refuses to save if any non-empty ``pine_text`` element doesn't
    parse as a Pine token — those are the "garbage in" entries that
    would crash the rewriter on every future run.
    """
    jda_list = _coerce_token_list(jda_text)
    pine_list = _coerce_token_list(pine_text)
    if not jda_list:
        raise ValueError("refusing to save suggestion with empty JDA match")
    if any(not s for s in jda_list):
        raise ValueError(
            f"refusing to save suggestion with empty JDA token(s): {jda_list!r}"
        )

    from ..parser import pine_parser
    for piece in pine_list:
        if not piece:
            raise ValueError(
                f"refusing to save suggestion with empty Pine token(s): {pine_list!r} "
                "— pass an empty list (not a list with empty strings) to register a drop."
            )
        # A rewrite may be a single ``@[...]`` token OR a multi-token
        # block (e.g. the gender-pronoun ``if/elseif/else/endif`` with
        # literal text between tokens). ``parse_fragment`` validates
        # every ``@[...]`` in the piece while allowing the literal glue.
        try:
            pine_parser.parse_fragment(piece)
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"refusing to cache unparseable Pine output {piece!r}: {e}"
            ) from e

    scope = normalize_scope(scope)
    root = _resolve_root(root)
    agency_safe = _safe_agency(agency)
    sug_id = _suggestion_id(jda_list, pine_list, scope)
    agency_dir = Path(root) / VERIFIED_DIRNAME / agency_safe
    target_dir = agency_dir / _scope_subdir(scope)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{sug_id}.toml"
    path.write_text(
        _suggestion_toml(
            jda_list, pine_list, sug_id, agency_safe, scope,
            source_template=source_template,
            source_segment_index=source_segment_index,
            note=note,
        ),
        encoding="utf-8",
    )
    return path


def prune_conflicting_in_scope(
    jda_text: TokenInput,
    pine_text: TokenInput,
    agency: str,
    *,
    scope: Optional[Scope] = None,
    root: Optional[Path] = None,
) -> List[Path]:
    """Delete any prior suggestion file in ``scope`` that has the same
    JDA match as ``jda_text`` but a different rewrite than ``pine_text``.

    Auto-persisted inline edits need this: if a converter edits a chip,
    changes their mind, and edits it again, accumulating both files
    would leave two patterns competing at the same priority. Calling
    this before ``accept_suggestion`` enforces "one rewrite per (agency,
    scope, jda)".

    Returns the paths that were removed (empty list when nothing
    conflicts). The new mapping itself is *not* re-saved here — call
    ``accept_suggestion`` afterwards.
    """
    root = _resolve_root(root)
    scope = normalize_scope(scope)
    agency_safe = _safe_agency(agency)
    agency_dir = Path(root) / VERIFIED_DIRNAME / agency_safe
    scope_dir = agency_dir / _scope_subdir(scope)
    if not scope_dir.exists():
        return []

    target_jda = _coerce_token_list(jda_text)
    target_pine = _coerce_token_list(pine_text)
    new_id = _suggestion_id(target_jda, target_pine, scope)

    report = pattern_loader.load_library(scope_dir)
    removed: List[Path] = []
    for p in report.patterns:
        match_tokens = list(p.match_tokens() or ())
        rewrite_tokens = list(p.rewrite_tokens() or ())
        if match_tokens != target_jda:
            continue
        if rewrite_tokens == target_pine:
            # Identical mapping — accept_suggestion will be idempotent.
            continue
        if p.id == new_id:
            # Same id but different content (shouldn't happen, but be
            # defensive); leave it for accept_suggestion to overwrite.
            continue
        path = scope_dir / f"{p.id}.toml"
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


# ─────────────────────────────────────────────────────────────────────────────
# Reject
# ─────────────────────────────────────────────────────────────────────────────


def reject_suggestion(
    jda_text: str,
    pine_text: str,
    agency: str,
    *,
    reason: str = "",
    source_template: Optional[str] = None,
    source_segment_index: Optional[int] = None,
    root: Optional[Path] = None,
) -> None:
    """Append a JSONL record for a rejected suggestion. No effect on
    pattern matching today — purely an audit trail."""
    root = _resolve_root(root)
    log_path = Path(root) / REJECTED_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "agency": agency,
        "jda": jda_text,
        "pine": pine_text,
        "reason": reason,
        "source_template": source_template,
        "source_segment_index": source_segment_index,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def is_suggestion_accepted(
    jda_text: TokenInput,
    pine_text: TokenInput,
    agency: str,
    *,
    scope: Optional[Scope] = None,
    root: Optional[Path] = None,
) -> bool:
    """True if this exact (jda, pine, scope) triple has already been
    accepted at the given scope. Accepts the same list-or-string input
    shapes as ``accept_suggestion``."""
    root = _resolve_root(root)
    scope = normalize_scope(scope)
    agency_safe = _safe_agency(agency)
    sug_id = _suggestion_id(
        _coerce_token_list(jda_text),
        _coerce_token_list(pine_text),
        scope,
    )
    agency_dir = Path(root) / VERIFIED_DIRNAME / agency_safe
    target = agency_dir / _scope_subdir(scope) / f"{sug_id}.toml"
    return target.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Listing — for the GUI's "manage saved overrides" view (future)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopedSuggestion:
    """One row in the scoped-suggestion listing — the on-disk file plus
    its scope metadata."""

    path: Path
    agency: str
    scope: Scope
    pattern: Pattern


def list_scoped_suggestions(
    agency: str,
    *,
    root: Optional[Path] = None,
    template_name: Optional[str] = None,
    audience: Optional[str] = None,
) -> List[ScopedSuggestion]:
    """Return one ScopedSuggestion per on-disk file for the agency. Filters
    by template_name / audience the same way ``load_verified_for_agency``
    does. Useful for a future "manage overrides" GUI."""
    root = _resolve_root(root)
    agency_safe = _safe_agency(agency)
    agency_dir = Path(root) / VERIFIED_DIRNAME / agency_safe
    if not agency_dir.exists():
        return []
    dirs_with_scope: List[Tuple[Path, Scope]] = []
    if (agency_dir / "global").exists():
        dirs_with_scope.append((agency_dir / "global", (SCOPE_GLOBAL, "")))
    if template_name:
        try:
            d = agency_dir / "by_template" / _safe_scope_value(template_name)
        except ValueError:
            d = None
        if d and d.exists():
            dirs_with_scope.append((d, (SCOPE_TEMPLATE, template_name)))
    if audience:
        try:
            d = agency_dir / "by_audience" / _safe_scope_value(audience)
        except ValueError:
            d = None
        if d and d.exists():
            dirs_with_scope.append((d, (SCOPE_AUDIENCE, audience)))
    out: List[ScopedSuggestion] = []
    for d, scope in dirs_with_scope:
        report = pattern_loader.load_library(d)
        for p in report.patterns:
            out.append(ScopedSuggestion(
                path=d / f"{p.id}.toml",
                agency=agency_safe, scope=scope, pattern=p,
            ))
    return out

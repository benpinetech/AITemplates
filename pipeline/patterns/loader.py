"""Read TOML pattern files, validate against schema, return Pattern[].

A *library* is a flat list of validated Pattern objects loaded from
``Agent/v2/patterns/library/**/*.toml``. The loader doesn't parse the
match/rewrite source itself — that happens lazily inside the matcher
and rewriter, so a parse error in one pattern's match string doesn't
prevent the rest of the library from loading.

What it does check:

  - every TOML file is valid TOML
  - every ``[[pattern]]`` validates against schema.Pattern
  - referenced transforms and rewrite_functions are registered
  - hole names referenced in match/rewrite source are declared in
    ``[pattern.holes]`` (or are derived holes whose source is declared)

All discovered errors are collected into a single ``LoadReport`` so the
caller can show them as a list — better than failing on the first.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from . import transforms
from .schema import Pattern, PatternFile


from pipeline._resource_path import PATTERNS_DIR


@dataclass
class LoadIssue:
    file: Path
    pattern_id: Optional[str]
    message: str


@dataclass
class LoadReport:
    """Result of a library load."""

    patterns: List[Pattern] = field(default_factory=list)
    issues: List[LoadIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def raise_if_issues(self) -> None:
        if self.issues:
            lines = [f"  {i.file}: {i.pattern_id or '<file>'}: {i.message}"
                     for i in self.issues]
            raise PatternLoadError(
                "library load failed with "
                f"{len(self.issues)} issue(s):\n" + "\n".join(lines)
            )


class PatternLoadError(Exception):
    """Raised by raise_if_issues() when any issue was recorded."""


# ─── helpers ────────────────────────────────────────────────────────────────


def _hole_names_in_source(text: str) -> List[str]:
    """Find every ``$name`` occurrence in pattern source text."""
    return re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", text)


def _validate_pattern(p: Pattern, file_path: Path, issues: List[LoadIssue]) -> None:
    declared = set(p.holes.keys())

    # Every match/rewrite hole reference must be declared (or derivable).
    referenced: set[str] = set()
    for tok in p.match_tokens():
        referenced.update(_hole_names_in_source(tok))
    if p.rewrite is not None:
        for tok in p.rewrite_tokens() or []:
            referenced.update(_hole_names_in_source(tok))

    undeclared = referenced - declared
    if undeclared:
        issues.append(LoadIssue(
            file_path, p.id,
            f"holes referenced but not declared in [pattern.holes]: "
            f"{sorted(undeclared)}",
        ))

    # Each derived hole must reference an existing declared hole and a
    # registered transform.
    for hname, hole in p.holes.items():
        if hole.derive_from:
            if hole.derive_from not in declared:
                issues.append(LoadIssue(
                    file_path, p.id,
                    f"hole {hname!r} derives from {hole.derive_from!r} "
                    "which is not declared",
                ))
            if hole.transform and hole.transform not in transforms.registered_transforms():
                issues.append(LoadIssue(
                    file_path, p.id,
                    f"hole {hname!r} references unknown transform "
                    f"{hole.transform!r}; available: "
                    f"{transforms.registered_transforms()}",
                ))

    # If rewrite_function is set, it must exist.
    if p.rewrite_function and p.rewrite_function not in transforms.registered_rewrite_functions():
        issues.append(LoadIssue(
            file_path, p.id,
            f"unknown rewrite_function {p.rewrite_function!r}; "
            f"available: {transforms.registered_rewrite_functions()}",
        ))


def _load_one_file(path: Path, issues: List[LoadIssue]) -> List[Pattern]:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        issues.append(LoadIssue(path, None, f"TOML parse error: {e}"))
        return []
    except OSError as e:
        issues.append(LoadIssue(path, None, f"cannot read: {e}"))
        return []

    try:
        parsed = PatternFile.model_validate(data)
    except Exception as e:  # noqa: BLE001  (pydantic ValidationError)
        issues.append(LoadIssue(path, None, f"schema validation: {e}"))
        return []

    out: List[Pattern] = []
    for p in parsed.pattern:
        _validate_pattern(p, path, issues)
        out.append(p)
    return out


# ─── public API ─────────────────────────────────────────────────────────────


def load_library(root: Optional[Path] = None) -> LoadReport:
    """Walk ``library/**/*.toml`` and return all patterns, collecting any
    issues. The default ``root`` is ``Agent/v2/patterns/library``.

    No exception is raised on partial failure — call
    ``LoadReport.raise_if_issues()`` if you want a hard failure mode.
    """
    if root is None:
        root = PATTERNS_DIR
    report = LoadReport()
    if not root.exists():
        return report

    seen_ids: dict[str, Path] = {}
    for path in sorted(root.glob("**/*.toml")):
        for p in _load_one_file(path, report.issues):
            if p.id in seen_ids:
                report.issues.append(LoadIssue(
                    path, p.id,
                    f"duplicate pattern id (also in {seen_ids[p.id]})",
                ))
                continue
            seen_ids[p.id] = path
            report.patterns.append(p)
    return report


def patterns_for_org(patterns: Iterable[Pattern], org: str) -> List[Pattern]:
    """Filter a list to patterns applicable to ``org``.

    Patterns with ``org_context = "any"`` always apply; org-specific
    patterns apply only when their context matches. Higher-priority
    patterns come first.
    """
    out = [p for p in patterns if p.org_context in ("any", org)]
    out.sort(key=lambda p: (-p.priority, p.id))
    return out

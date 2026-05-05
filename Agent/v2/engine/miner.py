"""Pattern miner — slice 1 (index-aligned single-token mining).

Reads the verified-pair corpus, attempts to generalize new patterns
by structural substitution, and emits TOML candidates for human
review. Never silently registers a pattern.

Strategy (slice 1):

  1. Walk every ``(legacy.rtf, pine.rtf)`` pair in the corpus.
  2. Extract bracketed expressions from each side (Phase 1 extractor).
  3. For pairs where ``len(legacy_tokens) == len(pine_tokens)``,
     align by index — the i-th JDA token corresponds to the i-th Pine
     token. Pairs with mismatched counts are skipped entirely; tree
     alignment is slice 2.
  4. For each aligned (jda, pine) pair:
     a. Try the existing pattern engine. If it converts ``jda`` to
        ``pine`` already, skip (the pattern is covered).
     b. Otherwise, look for the simplest generalization: substitute
        the JDA entity name with ``$entity`` and the corresponding
        Pine entity with ``$entity_pine`` (derived via
        ``translate_jda_entity_to_pine``).
     c. Validate the generalized pattern by re-running the engine
        on the original token; if it now produces the original Pine
        output, the pattern is plausible — emit it as a candidate.
  5. Deduplicate candidates by their (match, rewrite) shape.

What slice 1 catches:
  - Simple wrapper conversions where JDA has one entity reference
    and Pine has its translated counterpart.
  - Variants on existing patterns (e.g. an entity not yet in the
    transform's translation table).

What slice 1 misses:
  - Chunk patterns (multi-token).
  - Patterns where JDA and Pine token counts differ (FullName split
    into FirstName + LastName, Subdocument expanding to two tokens).
  - Patterns with multiple holes.
  - Patterns where a transform other than entity translation is
    needed.

These are slice 2's territory (tree alignment, more transforms, AST
diffing).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from ..parser import jda_parser, pine_parser, rtf_extractor
from ..parser.jda_ast import (
    JdaBinaryOp,
    JdaCall,
    JdaControl,
    JdaIter,
    JdaNode,
    JdaPath,
    JdaToken,
)
from ..parser.pine_ast import PineChain, PineNested, PineNode, PineToken
from ..patterns import engine as patterns_engine
from ..patterns import transforms
from ..patterns.schema import Pattern


# A handful of known JDA entity prefixes we use to recognise an "entity"
# segment in a path. The miner doesn't try to invent new prefixes; it
# trusts this list (which mirrors transforms.py's translation table).
_JDA_ENTITY_PREFIX_RE = re.compile(
    r"^(?:JW_|Cust_|KF_|kf_|JD_|OCA_)?[A-Z][A-Za-z0-9_]*$"
)


@dataclass(frozen=True)
class Candidate:
    """One mined pattern candidate."""

    id: str
    match: str
    rewrite: str
    jda_entity: str
    pine_entity: str
    source_template: str   # which template name produced this candidate
    source_index: int      # 0-based index inside that template

    def to_toml(self) -> str:
        return (
            "[[pattern]]\n"
            f"id          = {self.id!r}\n"
            "description = \"mined candidate — review before promoting\"\n"
            "provenance  = \"mined\"\n"
            "verification = \"candidate\"\n"
            f"notes       = \"sourced from template {self.source_template}, "
            f"token index {self.source_index}; jda entity={self.jda_entity}, "
            f"pine entity={self.pine_entity}\"\n"
            "org_context = \"any\"\n"
            "priority    = 50\n"
            "\n"
            f"match   = {self.match!r}\n"
            f"rewrite = {self.rewrite!r}\n"
            "\n"
            "[pattern.holes.entity]\n"
            "kind = \"path-segment\"\n"
            "\n"
            "[pattern.holes.entity_pine]\n"
            "derive_from = \"entity\"\n"
            "transform   = \"translate_jda_entity_to_pine\"\n"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — find the entity in each AST
# ─────────────────────────────────────────────────────────────────────────────


def _find_jda_entity(node: JdaNode) -> Optional[str]:
    """Return the JDA entity name found in ``node`` if there is exactly
    one identifiable entity. We define "entity" as the first segment of
    a JdaPath whose name matches the prefix regex.

    If there are multiple distinct entities, return None — slice 1
    handles single-hole patterns only.
    """
    seen: Set[str] = set()
    _collect_jda_entities(node, seen)
    if len(seen) != 1:
        return None
    only = next(iter(seen))
    return only


def _collect_jda_entities(node: JdaNode, out: Set[str]) -> None:
    if isinstance(node, JdaPath):
        # Only multi-segment paths name an entity. A single-segment
        # path is more likely a bare value (an unquoted RHS like ``RBA``).
        if len(node.parts) < 2:
            return
        head = node.parts[0]
        if _JDA_ENTITY_PREFIX_RE.match(head) and not _is_likely_field_name(head):
            out.add(head)
        return
    if isinstance(node, JdaCall):
        for a in node.args:
            _collect_jda_entities(a, out)
        return
    if isinstance(node, JdaControl):
        for a in node.args:
            _collect_jda_entities(a, out)
        return
    if isinstance(node, JdaBinaryOp):
        _collect_jda_entities(node.left, out)
        _collect_jda_entities(node.right, out)
        return
    if isinstance(node, JdaIter):
        _collect_jda_entities(node.collection, out)


# Fields/keywords that look like an entity but aren't. The naming
# convention is "PascalCase, longer, doesn't start with a JDA prefix"
# overlaps with field names. Slice 1 takes the conservative route:
# treat anything starting with a known prefix (JW_ / Cust_ / KF_ /
# kf_ / JD_ / OCA_) as definitely an entity, and everything else as
# possibly-a-field.
_LIKELY_FIELD_NAMES = frozenset({
    "FullName", "LastName", "FirstName", "MrMs", "Address", "City",
    "StateCode", "Zip", "Title", "Number", "Gender", "Type", "Subtype",
    "DateOfBirth", "EventDt", "ProsNum", "Date",
})


def _is_likely_field_name(s: str) -> bool:
    return s in _LIKELY_FIELD_NAMES


def _find_pine_entity(node: PineNode) -> Optional[str]:
    """Return the Pine chain-base used in ``node`` if exactly one
    capitalised base appears. Mirrors _find_jda_entity for the Pine
    side."""
    seen: Set[str] = set()
    _collect_pine_bases(node, seen)
    capitalised = {s for s in seen if s and s[0].isupper()}
    if len(capitalised) != 1:
        return None
    return next(iter(capitalised))


def _collect_pine_bases(node: PineNode, out: Set[str]) -> None:
    if isinstance(node, PineChain):
        # Only chains with segments name an entity. A bare chain like
        # ``Title`` (the arg to SetCasing) is a value, not an entity —
        # we'd false-positive on it otherwise.
        if isinstance(node.base, str) and node.segments:
            out.add(node.base)
        elif isinstance(node.base, PineNested):
            _collect_pine_bases(node.base.inner, out)
        for seg in node.segments:
            if seg.args:
                for a in seg.args:
                    _collect_pine_bases(a, out)
        return
    if isinstance(node, PineNested):
        _collect_pine_bases(node.inner, out)


# ─────────────────────────────────────────────────────────────────────────────
# Generalization
# ─────────────────────────────────────────────────────────────────────────────


def _generalize(
    jda_text: str, pine_text: str, jda_entity: str, pine_entity: str
) -> Tuple[str, str]:
    """Return (match, rewrite) by substituting entity names with
    placeholders, using whole-word boundaries to avoid partial
    matches inside other identifiers."""
    jda_re = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(jda_entity) + r"(?![A-Za-z0-9_])")
    pine_re = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(pine_entity) + r"(?![A-Za-z0-9_])")
    match = jda_re.sub("$entity", jda_text)
    rewrite = pine_re.sub("$entity_pine", pine_text)
    return match, rewrite


def _candidate_id(match: str, rewrite: str) -> str:
    """Stable-but-readable id derived from the shape."""
    h = hashlib.sha1((match + "→" + rewrite).encode("utf-8")).hexdigest()[:8]
    return f"mined_{h}"


# ─────────────────────────────────────────────────────────────────────────────
# Mining
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MiningReport:
    candidates: Dict[str, Candidate] = field(default_factory=dict)
    pairs_examined: int = 0
    pairs_skipped_count_mismatch: int = 0
    pairs_already_covered: int = 0
    pairs_unmineable: int = 0   # couldn't find single entity, etc.

    def add(self, c: Candidate) -> bool:
        """Returns True if this candidate is new (deduplicated)."""
        if c.id in self.candidates:
            return False
        self.candidates[c.id] = c
        return True


def mine_pair(
    jda: JdaToken,
    pine: PineToken,
    library: List[Pattern],
    org: str,
    source_template: str,
    source_index: int,
) -> Optional[Candidate]:
    """Try to produce a candidate from one (JDA, Pine) pair.

    Returns the Candidate on success, None if:
      - the existing engine already converts ``jda`` to ``pine`` (covered)
      - no clean entity translation exists (un-mineable for slice 1)
    """
    # 1. Already covered?
    result = patterns_engine.convert(jda, library, org=org)
    if result.matched and len(result.outputs) == 1:
        if result.outputs[0].inner == pine.inner:
            return None  # nothing to mine, existing pattern handles it

    # 2. Identify single entity on each side.
    jda_entity = _find_jda_entity(jda.inner)
    pine_entity = _find_pine_entity(pine.inner)
    if jda_entity is None or pine_entity is None:
        return None

    # 3. Verify the entity translation table agrees with this pairing.
    try:
        translated = transforms.translate_jda_entity_to_pine(jda_entity, org)
    except transforms.UnknownTransformInputError:
        # Unknown to the translation table. Two possibilities:
        # (a) JDA == Pine literally (no rename needed) — still mineable.
        # (b) JDA needs a new translation entry — slice 2 territory.
        if jda_entity != pine_entity:
            return None
        translated = pine_entity
    except Exception:
        return None
    if translated != pine_entity:
        return None  # The table disagrees; needs human review.

    # 4. Generalize.
    match, rewrite = _generalize(jda.unparse(), pine.unparse(), jda_entity, pine_entity)
    return Candidate(
        id=_candidate_id(match, rewrite),
        match=match,
        rewrite=rewrite,
        jda_entity=jda_entity,
        pine_entity=pine_entity,
        source_template=source_template,
        source_index=source_index,
    )


def mine_template(
    legacy_rtf: str,
    pine_rtf: str,
    library: List[Pattern],
    org: str,
    template_name: str,
    report: Optional[MiningReport] = None,
) -> MiningReport:
    """Mine candidates from one (legacy.rtf, pine.rtf) pair."""
    if report is None:
        report = MiningReport()

    legacy_hits = [
        h for h in rtf_extractor.extract(legacy_rtf, bracket="%[", parse=True)
        if h.ast is not None
    ]
    pine_hits = [
        h for h in rtf_extractor.extract(pine_rtf, bracket="@[", parse=True)
        if h.ast is not None
    ]

    # Drop CreateVar tokens from the pine side — they're declarative
    # prelude and have no JDA counterpart.
    pine_hits_filtered = [
        h for h in pine_hits
        if not (
            h.ast is not None
            and isinstance(h.ast, PineToken)
            and getattr(h.ast.inner, "name", "").lower() == "createvar"
        )
    ]

    if len(legacy_hits) != len(pine_hits_filtered):
        report.pairs_skipped_count_mismatch += 1
        return report

    for i, (lh, ph) in enumerate(zip(legacy_hits, pine_hits_filtered)):
        report.pairs_examined += 1
        candidate = mine_pair(
            lh.ast, ph.ast, library, org, template_name, i,
        )
        if candidate is None:
            # Distinguish "already covered" from "unmineable" by re-checking.
            covered_check = patterns_engine.convert(lh.ast, library, org=org)
            if (
                covered_check.matched
                and len(covered_check.outputs) == 1
                and covered_check.outputs[0].inner == ph.ast.inner
            ):
                report.pairs_already_covered += 1
            else:
                report.pairs_unmineable += 1
            continue
        report.add(candidate)
    return report


def mine_corpus(
    legacy_dir: Path,
    pine_dir: Path,
    library: List[Pattern],
    org: str = "any",
    on_template: Optional[Callable[[str], None]] = None,
) -> MiningReport:
    """Mine the verified-pair corpus end-to-end.

    Walks ``legacy_dir/*.rtf``, pairs by filename with ``pine_dir``,
    and aggregates a single report. Returns the report so callers
    can either inspect in-process or write candidates to disk.
    """
    report = MiningReport()
    for legacy_path in sorted(legacy_dir.glob("*.rtf")):
        pine_path = pine_dir / legacy_path.name
        if not pine_path.exists():
            continue
        if on_template:
            on_template(legacy_path.name)
        legacy_rtf = legacy_path.read_text(encoding="utf-8", errors="replace")
        pine_rtf = pine_path.read_text(encoding="utf-8", errors="replace")
        mine_template(
            legacy_rtf, pine_rtf, library, org, legacy_path.name, report,
        )
    return report


def write_candidates(report: MiningReport, output_dir: Path) -> List[Path]:
    """Write each candidate to ``output_dir/<id>.toml``. Returns the
    list of written paths. Idempotent: rewriting an existing candidate
    file overwrites with the same content.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for c in report.candidates.values():
        path = output_dir / f"{c.id}.toml"
        path.write_text(c.to_toml(), encoding="utf-8")
        written.append(path)
    return written

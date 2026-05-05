"""Pattern matcher.

Walks a pattern's parsed match-AST in parallel with a candidate source
AST. When a hole reference appears in the pattern, it captures the
corresponding source value (a string for path-segment holes, an AST
node for atom holes). On success, returns a Captures dict mapping hole
name → captured value. On any structural mismatch, returns None.

This module is JDA-side only. The matcher walks JDA ASTs and binds
holes; the rewriter (rewriter.py) consumes the Captures plus the
pattern's parsed rewrite-AST to emit Pine.

Phase 2 limitation: only single-token patterns are matched here. Chunk
matching (multi-token sequences with structural balancing) is Phase 2.5.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..parser import jda_parser
from ..parser.jda_ast import (
    JdaCall,
    JdaLiteral,
    JdaNode,
    JdaPath,
    JdaToken,
    LITERAL_BOOL,
    LITERAL_FORMAT,
    LITERAL_PATH,
)
from .holes import atom_hole_name_jda, hole_name, is_atom_hole_jda, is_hole_segment
from .schema import Hole, Pattern


# Captures is the runtime mapping from hole name → captured value.
# A captured value is either:
#   - a string  (for path-segment holes)
#   - an AST node  (for atom holes)
Captures = Dict[str, Any]


@dataclass(frozen=True)
class MatchResult:
    """Outcome of running a pattern against a source AST."""

    pattern: Pattern
    captures: Captures


# ─────────────────────────────────────────────────────────────────────────────
# Hole-kind validation
# ─────────────────────────────────────────────────────────────────────────────


def _kind_accepts_atom(kind: str, node: JdaNode) -> bool:
    """Check that an atom-hole's captured node fits the declared kind."""
    if kind == "expression":
        return True
    if kind == "path":
        return isinstance(node, JdaPath)
    if kind == "literal":
        return isinstance(node, JdaLiteral)
    if kind == "bool":
        return isinstance(node, JdaLiteral) and node.kind == LITERAL_BOOL
    if kind == "format-string":
        return isinstance(node, JdaLiteral) and node.kind == LITERAL_FORMAT
    if kind == "subdoc-path":
        return isinstance(node, JdaLiteral) and node.kind == LITERAL_PATH
    if kind == "path-segment":
        # A path-segment hole shouldn't have been bound at atom level —
        # but if it was, accept a single-segment path.
        return isinstance(node, JdaPath) and len(node.parts) == 1
    return False


def _kind_accepts_segment(kind: str, segment: str) -> bool:
    """Check that a segment-hole's captured string fits the declared kind."""
    if kind in ("path-segment", "expression", "path"):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Recursive matcher
# ─────────────────────────────────────────────────────────────────────────────


def _bind_atom_hole(
    name: str, src: JdaNode, holes: Dict[str, Hole], captures: Captures
) -> bool:
    if name in captures:
        # Same hole name appearing twice must capture the same value.
        return captures[name] == src
    declared = holes.get(name)
    if declared is None:
        # Pattern referenced an undeclared hole — schema validation should
        # have caught this. Fail closed.
        return False
    if not _kind_accepts_atom(declared.kind, src):
        return False
    captures[name] = src
    return True


def _bind_segment_hole(
    name: str, segment: str, holes: Dict[str, Hole], captures: Captures
) -> bool:
    if name in captures:
        return captures[name] == segment
    declared = holes.get(name)
    if declared is None:
        return False
    if not _kind_accepts_segment(declared.kind, segment):
        return False
    captures[name] = segment
    return True


def _match_path(
    pat: JdaPath, src: JdaNode, holes: Dict[str, Hole], captures: Captures
) -> bool:
    if not isinstance(src, JdaPath):
        return False
    if len(pat.parts) != len(src.parts):
        return False
    for p, s in zip(pat.parts, src.parts):
        if is_hole_segment(p):
            if not _bind_segment_hole(hole_name(p), s, holes, captures):
                return False
        elif p != s:
            return False
    return True


def _match_node(
    pat: JdaNode, src: JdaNode, holes: Dict[str, Hole], captures: Captures
) -> bool:
    # Atom-hole shortcut: a single-segment path whose only segment is a
    # hole captures any source node (subject to hole-kind constraint).
    if is_atom_hole_jda(pat):
        return _bind_atom_hole(atom_hole_name_jda(pat), src, holes, captures)

    # Literal-position hole: when a hole appears inside a raw-captured
    # arg (FormatDate's pattern, Subdocument's path), the parser swallows
    # the ``$name`` text *as the literal's value*. Recognise that and
    # bind the source literal to the named hole.
    if isinstance(pat, JdaLiteral) and pat.value.startswith("$"):
        return _bind_atom_hole(pat.value[1:], src, holes, captures)

    if type(pat) is not type(src):
        return False

    if isinstance(pat, JdaPath):
        return _match_path(pat, src, holes, captures)

    # Generic structural recursion over dataclass fields. We compare
    # every comparable field (skipping ``raw`` which is compare=False)
    # and recurse into JdaNode children and tuples-of-children.
    for field in dataclasses.fields(pat):
        if field.compare is False:
            continue
        pv = getattr(pat, field.name)
        sv = getattr(src, field.name)
        if isinstance(pv, JdaNode):
            if not _match_node(pv, sv, holes, captures):
                return False
        elif isinstance(pv, tuple):
            if not isinstance(sv, tuple) or len(pv) != len(sv):
                return False
            for pp, ss in zip(pv, sv):
                if isinstance(pp, JdaNode):
                    if not _match_node(pp, ss, holes, captures):
                        return False
                elif pp != ss:
                    return False
        else:
            if pv != sv:
                return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def match_one(pattern: Pattern, source: JdaToken) -> Optional[MatchResult]:
    """Try to match ``pattern`` against a single JDA token AST.

    Returns a MatchResult on success, None otherwise. Chunk patterns
    (multi-token match) are skipped here — see the engine for the
    chunk-matching path (Phase 2.5).
    """
    if pattern.is_chunk_pattern():
        return None
    match_src = pattern.match_tokens()[0]
    try:
        match_ast = jda_parser.parse(match_src)
    except jda_parser.JdaParseError:
        # A pattern with a malformed match string can't match anything.
        return None
    captures: Captures = {}
    if _match_node(match_ast.inner, source.inner, pattern.holes, captures):
        return MatchResult(pattern=pattern, captures=captures)
    return None


def match_first(
    patterns: List[Pattern], source: JdaToken
) -> Optional[MatchResult]:
    """Walk ``patterns`` in order and return the first that matches.

    The caller is responsible for sorting patterns by priority and
    filtering by org context (see ``loader.patterns_for_org``).
    """
    for p in patterns:
        result = match_one(p, source)
        if result is not None:
            return result
    return None


def match_all(
    patterns: List[Pattern], source: JdaToken
) -> List[MatchResult]:
    """Return every pattern that matches — useful for debugging /
    surfacing ambiguities. Production calls should use ``match_first``.
    """
    return [r for r in (match_one(p, source) for p in patterns) if r is not None]


# ─────────────────────────────────────────────────────────────────────────────
# Chunk matching — multi-token patterns over a token stream
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChunkMatchResult:
    """Outcome of a chunk-pattern match against a token stream."""

    pattern: Pattern
    captures: Captures
    consumed: int   # how many source tokens this match consumed


SEQUENCE_HOLE_SUFFIX = "..."


def _is_hole_only_element(elem: str) -> bool:
    """A chunk-pattern element of the form ``$NAME`` (entire string is a hole ref).

    Single-token form — captures exactly one source token. The
    sequence form ``$NAME...`` is a different element type, recognised
    by :func:`_is_sequence_hole_element`.
    """
    return (
        isinstance(elem, str)
        and len(elem) >= 2
        and elem[0] == "$"
        and not elem.endswith(SEQUENCE_HOLE_SUFFIX)
        and all(c.isalnum() or c == "_" for c in elem[1:])
    )


def _is_sequence_hole_element(elem: str) -> bool:
    """A chunk-pattern element of the form ``$NAME...`` — captures a
    variable-length token sequence (zero or more)."""
    if not (isinstance(elem, str) and elem.endswith(SEQUENCE_HOLE_SUFFIX)):
        return False
    head = elem[: -len(SEQUENCE_HOLE_SUFFIX)]
    return _is_hole_only_element(head)


def _sequence_hole_name(elem: str) -> str:
    return elem[1 : -len(SEQUENCE_HOLE_SUFFIX)]


def match_chunk(
    pattern: Pattern, tokens: List[JdaToken], start_idx: int
) -> Optional[ChunkMatchResult]:
    """Try to match a chunk pattern starting at ``tokens[start_idx]``.

    Pattern elements:
      - ``%[...]`` JDA token pattern — must structurally match the source
        token at the current position (single-token holes inside the
        pattern bind as usual).
      - ``$name`` — captures the source token at the current position
        into a single-token hole.
      - ``$name...`` — captures zero or more source tokens into a
        sequence hole. Sequence holes match shortest-first via
        backtracking, so the rest of the pattern always anchors the
        capture length.

    Returns a ChunkMatchResult on success, with ``consumed`` set to the
    total number of source tokens the chunk swallowed. Returns None on
    any mismatch.
    """
    if not pattern.is_chunk_pattern():
        return None

    captures: Captures = {}
    end_idx = _match_elements(
        pattern, pattern.match_tokens(), 0, tokens, start_idx, captures
    )
    if end_idx is None:
        return None
    return ChunkMatchResult(
        pattern=pattern, captures=captures, consumed=end_idx - start_idx,
    )


def _match_elements(
    pattern: Pattern,
    elements: List[str],
    elem_idx: int,
    tokens: List[JdaToken],
    src_idx: int,
    captures: Captures,
) -> Optional[int]:
    """Recursive worker for chunk matching with backtracking on sequence holes.

    Returns the source index just past the last matched token, or None
    on failure. Mutates ``captures`` in place and rolls back on
    backtrack so the caller sees a consistent dict on success and an
    unchanged dict on failure.
    """
    if elem_idx >= len(elements):
        return src_idx

    elem = elements[elem_idx]

    # ── sequence hole: try lengths shortest-first, recurse with the
    # remainder of the pattern ────────────────────────────────────────
    if _is_sequence_hole_element(elem):
        hole_n = _sequence_hole_name(elem)
        declared = pattern.holes.get(hole_n)
        if declared is None or declared.kind not in ("token", "expression"):
            return None
        max_len = len(tokens) - src_idx
        for length in range(0, max_len + 1):
            captured_seq = list(tokens[src_idx : src_idx + length])
            saved = captures.get(hole_n, _UNSET)
            captures[hole_n] = captured_seq
            result = _match_elements(
                pattern, elements, elem_idx + 1, tokens, src_idx + length, captures,
            )
            if result is not None:
                return result
            # Backtrack: restore captures for this hole.
            if saved is _UNSET:
                captures.pop(hole_n, None)
            else:
                captures[hole_n] = saved
        return None

    # ── single-token hole ───────────────────────────────────────────
    if _is_hole_only_element(elem):
        if src_idx >= len(tokens):
            return None
        hole_n = elem[1:]
        declared = pattern.holes.get(hole_n)
        if declared is None or declared.kind not in ("token", "expression"):
            return None
        if hole_n in captures and captures[hole_n] != tokens[src_idx]:
            return None
        saved = captures.get(hole_n, _UNSET)
        captures[hole_n] = tokens[src_idx]
        result = _match_elements(
            pattern, elements, elem_idx + 1, tokens, src_idx + 1, captures,
        )
        if result is None:
            if saved is _UNSET:
                captures.pop(hole_n, None)
            else:
                captures[hole_n] = saved
        return result

    # ── JDA token pattern (structural match) ─────────────────────────
    if elem.startswith("%["):
        if src_idx >= len(tokens):
            return None
        try:
            elem_ast = jda_parser.parse(elem)
        except jda_parser.JdaParseError:
            return None
        sub_caps: Captures = {}
        if not _match_node(elem_ast.inner, tokens[src_idx].inner, pattern.holes, sub_caps):
            return None
        # Merge sub-captures with the parent dict, checking consistency.
        added: List[str] = []
        for k, v in sub_caps.items():
            if k in captures and captures[k] != v:
                # Roll back any keys added so far.
                for a in added:
                    captures.pop(a, None)
                return None
            if k not in captures:
                captures[k] = v
                added.append(k)
        result = _match_elements(
            pattern, elements, elem_idx + 1, tokens, src_idx + 1, captures,
        )
        if result is None:
            for a in added:
                captures.pop(a, None)
        return result

    # Unrecognised element shape.
    return None


_UNSET = object()


def match_chunk_first(
    patterns: List[Pattern], tokens: List[JdaToken], start_idx: int
) -> Optional[ChunkMatchResult]:
    """Walk ``patterns`` and return the first chunk that matches starting at
    ``tokens[start_idx]``. Single-token patterns are skipped here — the
    caller is responsible for filtering or for falling back to
    ``match_first`` when no chunk fires.
    """
    for p in patterns:
        if not p.is_chunk_pattern():
            continue
        result = match_chunk(p, tokens, start_idx)
        if result is not None:
            return result
    return None

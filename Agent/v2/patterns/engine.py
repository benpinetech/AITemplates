"""Top-level pattern engine — match and rewrite in one call.

This is the entry point downstream code (Phase 5's pipeline) talks to.

    from v2.patterns import engine
    from v2.patterns.loader import load_library

    lib = load_library()
    lib.raise_if_issues()                  # optional: hard-fail on bad TOML

    result = engine.convert(jda_token, lib.patterns, org="oba")
    if result.matched:
        for pine_token in result.outputs:
            ...
    else:
        # No pattern matched → Phase 4 LLM fallback would handle this.
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..parser.jda_ast import JdaToken
from ..parser.pine_ast import PineToken
from . import matcher, rewriter, transforms
from .loader import patterns_for_org
from .schema import Pattern


@dataclass(frozen=True)
class ConvertResult:
    """The outcome of converting one JDA token via the pattern library."""

    outputs: Tuple[PineToken, ...]   # empty when no pattern matched
    pattern: Optional[Pattern]        # the pattern that matched, or None
    captures: dict = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return self.pattern is not None


def convert(
    source: JdaToken,
    patterns: List[Pattern],
    org: str = "any",
) -> ConvertResult:
    """Match ``source`` against the library and return the rewritten output.

    Walks patterns by priority (highest first). For each pattern that
    structurally matches, attempts rewriting. If a transform raises
    ``UnknownTransformInputError`` the pattern is skipped (treated as
    "doesn't apply to this input") and the engine tries the next
    candidate. Other rewrite errors propagate.

    Returns a ``ConvertResult`` with empty ``outputs`` and ``pattern=None``
    if no pattern matched. That's the signal for Phase 4's LLM fallback.
    """
    candidates = patterns_for_org(patterns, org)
    for pat in candidates:
        if pat.is_chunk_pattern():
            continue   # convert() is single-token only; see convert_stream.
        result = matcher.match_one(pat, source)
        if result is None:
            continue
        try:
            outputs = rewriter.rewrite(pat, result.captures, org)
        except transforms.UnknownTransformInputError:
            # Pattern matched structurally but can't produce a rewrite
            # for this specific input — try the next candidate.
            continue
        return ConvertResult(
            outputs=tuple(outputs),
            pattern=pat,
            captures=dict(result.captures),
        )
    return ConvertResult(outputs=(), pattern=None)


# ─────────────────────────────────────────────────────────────────────────────
# Stream-level conversion (chunk-aware)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamSegment:
    """One segment of a chunk-aware conversion: either successfully
    converted (with a pattern attached) or unmatched (the original JDA
    token preserved for the LLM fallback path).
    """

    outputs: Tuple[PineToken, ...]
    pattern: Optional[Pattern]
    consumed: int                     # how many source tokens this segment ate
    source_start: int                 # index in the original token list
    unmatched_source: Optional[JdaToken] = None  # set iff pattern is None

    @property
    def matched(self) -> bool:
        return self.pattern is not None


def convert_stream(
    tokens: List[JdaToken],
    patterns: List[Pattern],
    org: str = "any",
) -> List[StreamSegment]:
    """Walk a JDA token list, emit one StreamSegment per consumed slice.

    At each position the engine tries chunk patterns first (longest /
    highest-priority match wins), then single-token patterns, then
    finally emits an unmatched segment so the caller can route the
    token to LLM fallback in Phase 4.

    The output preserves source order; concatenating each segment's
    ``outputs`` reconstructs the converted token sequence.
    """
    candidates = patterns_for_org(patterns, org)
    chunk_candidates = [p for p in candidates if p.is_chunk_pattern()]
    single_candidates = [p for p in candidates if not p.is_chunk_pattern()]

    segments: List[StreamSegment] = []
    i = 0
    while i < len(tokens):
        # 1. Try chunks first.
        chunk_match = None
        chunk_outputs = None
        for cp in chunk_candidates:
            cm = matcher.match_chunk(cp, tokens, i)
            if cm is None:
                continue
            try:
                chunk_outputs = rewriter.rewrite_chunk(
                    cp,
                    cm.captures,
                    org,
                    # Recursively convert sub-tokens via single-token engine.
                    lambda t: convert(t, patterns, org).outputs,
                )
            except transforms.UnknownTransformInputError:
                continue
            chunk_match = cm
            break

        if chunk_match is not None and chunk_outputs is not None:
            segments.append(StreamSegment(
                outputs=tuple(chunk_outputs),
                pattern=chunk_match.pattern,
                consumed=chunk_match.consumed,
                source_start=i,
            ))
            i += chunk_match.consumed
            continue

        # 2. Single-token fallback.
        single = convert(tokens[i], single_candidates, org)
        if single.matched:
            segments.append(StreamSegment(
                outputs=single.outputs,
                pattern=single.pattern,
                consumed=1,
                source_start=i,
            ))
            i += 1
            continue

        # 3. Unmatched — pass the original through for LLM fallback.
        segments.append(StreamSegment(
            outputs=(),
            pattern=None,
            consumed=1,
            source_start=i,
            unmatched_source=tokens[i],
        ))
        i += 1

    return segments

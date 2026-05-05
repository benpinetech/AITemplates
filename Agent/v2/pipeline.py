"""Phase 5 — end-to-end conversion pipeline.

Public entry point: ``convert_template(rtf, org, ...) -> ConversionResult``.

The pipeline glues together every earlier phase:

    rtf  ──Phase 1── extract bracketed expressions with positions
                    ↓
                    Phase 2/2.5/2.6 — pattern engine over the token stream
                    ↓
                    Phase 4 — LLM fallback for unmatched segments (optional)
                    ↓
                    Phase 4 — validator over the combined Pine output
                    ↓
                    Phase 5 — RTF reconstruction (replaces bracketed regions
                                only; prose untouched)

Each output segment carries provenance so the diff UI can show "this
chunk matched pattern X" / "this came from LLM fallback" / "this is
still unmatched". The privacy boundary (no prose to the LLM) is
preserved end-to-end: only bracketed expressions plus the org's
vocabulary and grammar fragments ever cross into the LLM call.

Org context is required. Pass ``org="oba"`` for OBA conversions; pass
``org="any"`` only for tests / org-agnostic pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .engine import suggestion_store
from .engine.llm_fallback import LlmFallback
from .engine.validator import ValidationIssue, Validator
from .grammar.loaders import OrgRoot, load_org_overrides
from .parser import rtf_extractor
from .parser.jda_ast import JdaToken
from .parser.pine_ast import PineToken
from .patterns import engine as patterns_engine
from .patterns import loader as pattern_loader
from .patterns.engine import StreamSegment
from .patterns.schema import Pattern


# ─────────────────────────────────────────────────────────────────────────────
# Result shapes
# ─────────────────────────────────────────────────────────────────────────────


PROV_PATTERN = "pattern"
PROV_LLM = "llm-fallback"
PROV_UNMATCHED = "unmatched"


@dataclass(frozen=True)
class ConversionSegment:
    """One contiguous slice of the conversion result. Mirrors a
    :class:`StreamSegment` plus the source RTF byte range and any
    validation issues attached to this segment's outputs."""

    source_start_byte: int           # inclusive
    source_end_byte: int             # exclusive
    source_jda_tokens: Tuple[JdaToken, ...]
    pine_outputs: Tuple[PineToken, ...]
    provenance: str                  # PROV_PATTERN | PROV_LLM | PROV_UNMATCHED
    pattern: Optional[Pattern] = None
    issues: Tuple[ValidationIssue, ...] = ()

    @property
    def matched(self) -> bool:
        return self.provenance != PROV_UNMATCHED


@dataclass(frozen=True)
class ConversionResult:
    """End-to-end conversion outcome."""

    converted_rtf: str
    segments: Tuple[ConversionSegment, ...]
    issues: Tuple[ValidationIssue, ...]   # all validation issues, stream-level + per-token
    org: str

    @property
    def total_jda_tokens(self) -> int:
        return sum(len(s.source_jda_tokens) for s in self.segments)

    @property
    def total_pine_tokens(self) -> int:
        return sum(len(s.pine_outputs) for s in self.segments)

    @property
    def by_provenance(self) -> dict:
        """How many segments produced output via each provenance class."""
        out = {PROV_PATTERN: 0, PROV_LLM: 0, PROV_UNMATCHED: 0}
        for s in self.segments:
            out[s.provenance] += 1
        return out

    def summary_line(self) -> str:
        prov = self.by_provenance
        return (
            f"org={self.org!r}  "
            f"pattern={prov[PROV_PATTERN]}  "
            f"llm={prov[PROV_LLM]}  "
            f"unmatched={prov[PROV_UNMATCHED]}  "
            f"tokens={self.total_jda_tokens}→{self.total_pine_tokens}  "
            f"issues={len(self.issues)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RTF reconstruction
# ─────────────────────────────────────────────────────────────────────────────


def _reconstruct(
    rtf: str,
    hits: Sequence[rtf_extractor.Hit],
    segments: Sequence[ConversionSegment],
) -> str:
    """Build the converted RTF by replacing each segment's source byte
    range(s) with the segment's Pine output text.

    Two replacement strategies depending on the count balance:

    - **Same count** (output tokens == source tokens): per-token
      replacement. The prose between source tokens stays put — each
      ``%[...]`` byte range is replaced with the corresponding
      ``@[...]`` text. Used by structurally-balanced chunk patterns
      like the gender pronoun block.
    - **Different count** (collapse, expand, or 0/N): replace the full
      span from the first source token's start to the last source
      token's end with the joined Pine outputs. Prose between source
      tokens within that span is dropped (which is what the user wants
      for collapse patterns like the defensive null wrapper).

    Walking back-to-front so earlier byte positions stay valid as
    later replacements happen.
    """
    pieces: List[Tuple[int, int, str]] = []   # (start, end, replacement)
    for seg in segments:
        if not seg.source_jda_tokens:
            continue   # synthetic segment, nothing to replace
        start_idx, end_idx = _hit_range_for_segment(hits, seg)
        if start_idx is None:
            continue
        seg_hits = hits[start_idx : end_idx + 1]
        if seg.provenance == PROV_UNMATCHED:
            # Leave the JDA token in place so the mapper sees what
            # didn't convert.
            continue
        outputs = seg.pine_outputs
        if len(outputs) == len(seg_hits):
            for h, out_tok in zip(seg_hits, outputs):
                pieces.append((h.start, h.end, out_tok.unparse()))
        else:
            full_start = seg_hits[0].start
            full_end = seg_hits[-1].end
            replacement = " ".join(o.unparse() for o in outputs) if outputs else ""
            pieces.append((full_start, full_end, replacement))

    pieces.sort(key=lambda p: p[0], reverse=True)
    out = rtf
    for start, end, replacement in pieces:
        out = out[:start] + replacement + out[end:]
    return out


def _hit_range_for_segment(
    hits: Sequence[rtf_extractor.Hit], seg: ConversionSegment
) -> Tuple[Optional[int], Optional[int]]:
    """Find the indices of the first and last hit for this segment by
    matching JDA tokens. We use ``unparse()`` equality because the
    pipeline is the only producer here and identity comparison would
    miss tokens reconstructed by Phase 1."""
    if not seg.source_jda_tokens:
        return None, None
    first = seg.source_jda_tokens[0]
    last = seg.source_jda_tokens[-1]
    first_idx = None
    for i, h in enumerate(hits):
        if h.ast is first or (h.ast is not None and h.ast.inner == first.inner
                              and h.start == seg.source_start_byte):
            first_idx = i
            break
    if first_idx is None:
        return None, None
    last_idx = first_idx + len(seg.source_jda_tokens) - 1
    if last_idx >= len(hits):
        return first_idx, None
    return first_idx, last_idx


# ─────────────────────────────────────────────────────────────────────────────
# Public pipeline
# ─────────────────────────────────────────────────────────────────────────────


def convert_template(
    rtf: str,
    org: str,
    *,
    library: Optional[List[Pattern]] = None,
    org_overrides: Optional[OrgRoot] = None,
    validator: Optional[Validator] = None,
    llm_fallback: Optional[LlmFallback] = None,
    include_verified_suggestions: bool = True,
    suggestions_root: Optional[Path] = None,
) -> ConversionResult:
    """Convert a JDA RTF template to Pine.

    ``org`` is required — Phase 5 enforces explicit declaration. Inferring
    org from JDA entity prefixes is a future convenience layer.

    All four optional dependencies (library, org_overrides, validator,
    llm_fallback) are loaded with sensible defaults if not provided.
    Pass an explicit instance to override:

      - ``library``: from ``patterns.loader.load_library()``
      - ``org_overrides``: from ``grammar.loaders.load_org_overrides(org)``
        (only loaded if the file exists; ``any`` bypasses)
      - ``validator``: built from the lint rules and the org overrides
      - ``llm_fallback``: not run by default; pass an instance to enable.
        The pipeline runs it only on unmatched segments.

    Returns a :class:`ConversionResult` with the converted RTF, per-
    segment provenance, and validation issues.
    """
    # Resolve dependencies.
    if library is None:
        report = pattern_loader.load_library()
        library = report.patterns
        # Layer in any verified LLM suggestions for this org so the
        # closed-loop kicks in: yesterday's accepted suggestion becomes
        # today's deterministic match.
        if include_verified_suggestions and org != "any":
            library = library + suggestion_store.load_verified_for_org(
                org, root=suggestions_root,
            )
    if org_overrides is None and org != "any":
        try:
            org_overrides = load_org_overrides(org)
        except FileNotFoundError:
            org_overrides = None
    if validator is None:
        validator = Validator(org=org_overrides) if org_overrides else Validator(org=None)

    # 1. Extract JDA tokens with positions.
    hits = [
        h for h in rtf_extractor.extract(rtf, bracket="%[", parse=True)
        if h.ast is not None
    ]
    tokens = [h.ast for h in hits]

    # 2. Run the pattern engine over the stream.
    stream_segments = patterns_engine.convert_stream(tokens, library, org=org)

    # 3. For each StreamSegment, build a ConversionSegment with byte
    #    positions; for unmatched segments, optionally run LLM fallback.
    segments: List[ConversionSegment] = []
    for s in stream_segments:
        if s.consumed == 0:
            continue
        seg_hits = hits[s.source_start : s.source_start + s.consumed]
        seg_tokens = tuple(h.ast for h in seg_hits)
        start_byte = seg_hits[0].start
        end_byte = seg_hits[-1].end

        if s.pattern is not None:
            segments.append(ConversionSegment(
                source_start_byte=start_byte,
                source_end_byte=end_byte,
                source_jda_tokens=seg_tokens,
                pine_outputs=s.outputs,
                provenance=PROV_PATTERN,
                pattern=s.pattern,
            ))
            continue

        # Unmatched — try LLM fallback if configured.
        llm_token: Optional[PineToken] = None
        if llm_fallback is not None and s.unmatched_source is not None:
            llm_token = llm_fallback.convert(s.unmatched_source)
        if llm_token is not None:
            segments.append(ConversionSegment(
                source_start_byte=start_byte,
                source_end_byte=end_byte,
                source_jda_tokens=seg_tokens,
                pine_outputs=(llm_token,),
                provenance=PROV_LLM,
                pattern=None,
            ))
        else:
            segments.append(ConversionSegment(
                source_start_byte=start_byte,
                source_end_byte=end_byte,
                source_jda_tokens=seg_tokens,
                pine_outputs=(),
                provenance=PROV_UNMATCHED,
                pattern=None,
            ))

    # 4. Validate the combined Pine output stream.
    pine_stream = [tok for seg in segments for tok in seg.pine_outputs]
    all_issues = validator.validate_stream(pine_stream) if pine_stream else []

    # Attach issues to segments by token index.
    issues_by_token = _bucket_issues_by_token(all_issues, segments)
    final_segments = tuple(
        ConversionSegment(
            source_start_byte=s.source_start_byte,
            source_end_byte=s.source_end_byte,
            source_jda_tokens=s.source_jda_tokens,
            pine_outputs=s.pine_outputs,
            provenance=s.provenance,
            pattern=s.pattern,
            issues=tuple(issues_by_token.get(i, ())),
        )
        for i, s in enumerate(segments)
    )

    # 5. Reconstruct the RTF.
    converted_rtf = _reconstruct(rtf, hits, final_segments)

    return ConversionResult(
        converted_rtf=converted_rtf,
        segments=final_segments,
        issues=tuple(all_issues),
        org=org,
    )


def _bucket_issues_by_token(
    issues: Sequence[ValidationIssue], segments: Sequence[ConversionSegment]
) -> dict:
    """Associate validator issues (which carry the index of an offending
    token in the *combined* pine stream) back to the segment that
    produced that token. Stream-level issues with token_index=None go
    to no segment."""
    out: dict = {}
    # Build an index from "global token position" to "segment index".
    seg_index_of_token: List[int] = []
    for seg_idx, seg in enumerate(segments):
        for _ in seg.pine_outputs:
            seg_index_of_token.append(seg_idx)
    for issue in issues:
        if issue.token_index is None:
            continue
        if 0 <= issue.token_index < len(seg_index_of_token):
            seg_idx = seg_index_of_token[issue.token_index]
            out.setdefault(seg_idx, []).append(issue)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Convenience
# ─────────────────────────────────────────────────────────────────────────────


def convert_file(
    input_path: Path,
    org: str,
    *,
    output_path: Optional[Path] = None,
    **kwargs,
) -> ConversionResult:
    """Convert one RTF file. If ``output_path`` is given, also write
    the converted RTF to disk."""
    rtf = Path(input_path).read_text(encoding="utf-8", errors="replace")
    result = convert_template(rtf, org, **kwargs)
    if output_path is not None:
        Path(output_path).write_text(result.converted_rtf, encoding="utf-8")
    return result

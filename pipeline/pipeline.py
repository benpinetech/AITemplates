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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .engine import suggestion_store
from .engine import prelude as prelude_module
from .engine.audience import classify_document_audience
from .engine.llm_fallback import LlmFallback
from .engine.validator import ValidationIssue, Validator
from .grammar.loaders import OrgRoot, load_org_overrides
from .parser import branch_swap, rtf_extractor
from .parser.jda_ast import JdaToken
from .parser.pine_ast import PineToken
from .patterns import engine as patterns_engine
from .patterns import loader as pattern_loader
from .patterns.engine import StreamSegment
from .patterns.schema import Pattern


# ── Context window helper for LLM-fallback inputs ───────────────────
# Strips enough RTF noise from a window of source bytes that the LLM
# sees readable prose, not ``\rtlch\fcs1 \af1 \rtlch\fcs0 \loch\f1``
# control runs. Doesn't need to be a full RTF parser — the LLM is
# tolerant of leftover artefacts; the goal is "mostly prose".
_CTX_RTF_GROUP_RE   = re.compile(r"\{\\[^{}]*\}")
_CTX_RTF_CONTROL_RE = re.compile(r"\\[a-zA-Z]+(?:-?\d+)?\s?")
_CTX_RTF_BRACES_RE  = re.compile(r"[{}]")
_CTX_WS_RE          = re.compile(r"\s+")


def _strip_rtf_for_context(s: str) -> str:
    if not s:
        return ""
    # Strip leaf RTF groups (one nesting level — quick & dirty, but
    # enough for the small context windows we care about).
    prev = None
    while prev != s:
        prev = s
        s = _CTX_RTF_GROUP_RE.sub("", s)
    s = _CTX_RTF_CONTROL_RE.sub(" ", s)
    s = _CTX_RTF_BRACES_RE.sub("", s)
    s = _CTX_WS_RE.sub(" ", s).strip()
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Result shapes
# ─────────────────────────────────────────────────────────────────────────────


PROV_PATTERN = "pattern"
PROV_LLM = "llm-fallback"
PROV_UNMATCHED = "unmatched"
PROV_EDIT = "edit"


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
    """End-to-end conversion outcome.

    ``normalized_rtf`` and ``hits`` are kept on the result so a GUI
    layer can rebuild the converted RTF after a converter edits a
    segment's Pine output — without re-running the parser and pattern
    engine from scratch. ``rebuild_result_with_edits`` uses them.
    Optional and default-empty so older consumers stay compatible.
    """

    converted_rtf: str
    segments: Tuple[ConversionSegment, ...]
    issues: Tuple[ValidationIssue, ...]   # all validation issues, stream-level + per-token
    org: str
    template_name: Optional[str] = None
    audience: Optional[str] = None
    normalized_rtf: str = ""
    hits: Tuple = ()
    org_overrides: Optional[OrgRoot] = None
    emit_prelude: bool = True
    # The CreateVar prelude lines that were prepended to ``converted_rtf``.
    # The GUI needs this to know how many Pine chips at the start of the
    # output came from the prelude (and have no backing segment), so it
    # can skip them when mapping a clicked chip back to its segment.
    prelude_lines: Tuple[str, ...] = ()

    @property
    def total_jda_tokens(self) -> int:
        return sum(len(s.source_jda_tokens) for s in self.segments)

    @property
    def total_pine_tokens(self) -> int:
        return sum(len(s.pine_outputs) for s in self.segments)

    @property
    def by_provenance(self) -> dict:
        """How many segments produced output via each provenance class."""
        out = {PROV_PATTERN: 0, PROV_LLM: 0, PROV_UNMATCHED: 0, PROV_EDIT: 0}
        for s in self.segments:
            out[s.provenance] = out.get(s.provenance, 0) + 1
        return out

    def summary_line(self) -> str:
        prov = self.by_provenance
        return (
            f"org={self.org!r}  "
            f"pattern={prov[PROV_PATTERN]}  "
            f"llm={prov[PROV_LLM]}  "
            f"unmatched={prov[PROV_UNMATCHED]}  "
            f"edit={prov[PROV_EDIT]}  "
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
    template_name: Optional[str] = None,
    emit_prelude: bool = True,
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
    # Resolve dependencies. Seed library loads up front; verified
    # suggestions layer in after audience classification (below) so
    # the loader can filter by the current document's scope.
    auto_load_suggestions = (
        library is None and include_verified_suggestions and org != "any"
    )
    if library is None:
        report = pattern_loader.load_library()
        library = report.patterns
    if org_overrides is None and org != "any":
        try:
            org_overrides = load_org_overrides(org)
        except FileNotFoundError:
            org_overrides = None
    if validator is None:
        validator = Validator(org=org_overrides) if org_overrides else Validator(org=None)

    # 0. Normalize RTF first. The extractor stitches fragmented
    # ``%}{...}\n[`` openers (Word emits them when bold/italic spans
    # split a token) and returns Hit positions in that normalized
    # text. If we then reconstructed against the *original* RTF those
    # positions would drift by however many chars normalization
    # collapsed — content disappears, partial JDA tails leak through,
    # and the output becomes scrambled. We normalize once here and
    # use the result as the single base for extraction AND
    # reconstruction.
    rtf = rtf_extractor.normalize_rtf(rtf)

    # 0a. Branch-swap pre-pass. JDA `If(X.IsEmpty=true)` translates to
    # Pine `If(@[Y.Any()]==true)`, which is the OPPOSITE polarity — the
    # if-body and else-body need to be swapped before token extraction
    # so each body ends up in the semantically-correct branch after
    # condition translation. Idempotent on RTF with no inverted-If
    # blocks.
    rtf = branch_swap.swap_inverted_branches(rtf)

    # 1. Extract JDA tokens with positions.
    hits = [
        h for h in rtf_extractor.extract(rtf, bracket="%[", parse=True)
        if h.ast is not None
    ]
    tokens = [h.ast for h in hits]

    # 1a. Classify document audience once, against the full extracted
    #     token stream. The result threads through to the suggestion
    #     loader (so audience-scoped overrides only fire on matching
    #     documents) and the LLM fallback (so the prompt's audience
    #     hint agrees with the loader's decision).
    audience: Optional[str] = None
    if org != "any":
        try:
            from .patterns.transforms import _JDA_TO_PINE_ENTITY
            audience = classify_document_audience(
                template_name, tokens, _JDA_TO_PINE_ENTITY,
            )
        except Exception:  # noqa: BLE001
            audience = None

    # 1b. Layer in verified suggestions whose scope matches this
    #     document. ``global`` always applies; ``by_template`` and
    #     ``by_audience`` only when the inputs name them.
    if auto_load_suggestions:
        library = library + suggestion_store.load_verified_for_org(
            org,
            root=suggestions_root,
            template_name=template_name,
            audience=audience,
        )

    # 2. Run the pattern engine over the stream.
    stream_segments = patterns_engine.convert_stream(tokens, library, org=org)

    # 3. Two-pass segment building. First pass collects which stream
    #    segments are unmatched and bundles them into a single LLM
    #    batch call. Document-level batching lets the LLM see siblings
    #    so it can disambiguate context-dependent translations
    #    (e.g. which entity an address belongs to in this letter).
    unmatched_indices: List[int] = []
    unmatched_tokens: List[JdaToken] = []
    unmatched_contexts: List[tuple] = []   # parallel: (before, after) prose
    for idx, s in enumerate(stream_segments):
        if s.consumed == 0 or s.pattern is not None:
            continue
        if s.unmatched_source is not None:
            unmatched_indices.append(idx)
            unmatched_tokens.append(s.unmatched_source)
            # Pull surrounding prose so context-dependent translations
            # (MrMs salutation, c/o reversal, Dear-form last name, etc.)
            # are disambiguated by what's actually next to the token —
            # not just the token in isolation. We grab a wider raw RTF
            # window than we need, strip it, then trim to a sensible
            # display length so the LLM gets readable prose rather
            # than \rtlch{\fcs1...} noise.
            h = hits[s.source_start]
            before_raw = rtf[max(0, h.start - 240) : h.start]
            after_raw  = rtf[h.end : h.end + 240]
            unmatched_contexts.append((
                _strip_rtf_for_context(before_raw)[-80:],
                _strip_rtf_for_context(after_raw)[:80],
            ))

    batch_outputs: List[List[PineToken]] = []
    drop_slots: set = set()    # indices into batch_outputs the LLM dropped
    if llm_fallback is not None and unmatched_tokens:
        try:
            batch_outputs, drop_slots = llm_fallback.convert_batch(
                unmatched_tokens, template_name=template_name,
                contexts=tuple(unmatched_contexts),
                return_drops=True,
            )
        except Exception:  # noqa: BLE001 — never let a fallback error abort the pipeline
            batch_outputs = [[] for _ in unmatched_tokens]
    # Index outputs back to their source segments. A slot the LLM
    # returned as ``[]`` (intentional drop) is recorded here with an
    # empty tuple so the segment-builder below can mark it PROV_LLM
    # rather than leaving the raw JDA in the output as PROV_UNMATCHED.
    output_for_segment: dict = {}
    llm_dropped_segments: set = set()
    for slot_i, (stream_idx, outs) in enumerate(
        zip(unmatched_indices, batch_outputs)
    ):
        if outs:
            output_for_segment[stream_idx] = tuple(outs)
        elif slot_i in drop_slots:
            output_for_segment[stream_idx] = ()
            llm_dropped_segments.add(stream_idx)

    segments: List[ConversionSegment] = []
    for idx, s in enumerate(stream_segments):
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

        if idx in output_for_segment:
            # Both real translations and intentional drops (empty
            # tuple) land here — they were "matched by the LLM",
            # difference is whether any Pine output was produced.
            segments.append(ConversionSegment(
                source_start_byte=start_byte,
                source_end_byte=end_byte,
                source_jda_tokens=seg_tokens,
                pine_outputs=output_for_segment[idx],
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

    # 6. Prepend a Pine ``CreateVar`` prelude when child entities are
    #    referenced. Child entities (``*Address``, ``*Phone``, ``*Email``)
    #    have foreign keys to a Root entity (Personnel / Name), not to
    #    Case, so the destination Pine renderer can't reach them
    #    unless the template either pre-declares them at the
    #    variable-screen level OR ships its own ``CreateVar`` block.
    #    Emitting the prelude makes the converted template
    #    self-contained.
    final_prelude_lines: Tuple[str, ...] = ()
    if emit_prelude and pine_stream:
        prelude_lines = prelude_module.generate_prelude(
            pine_stream, org_overrides=org_overrides,
        )
        if prelude_lines:
            converted_rtf = prelude_module.prepend_prelude_to_rtf(
                converted_rtf, prelude_lines,
            )
            final_prelude_lines = tuple(prelude_lines)

    return ConversionResult(
        converted_rtf=converted_rtf,
        segments=final_segments,
        issues=tuple(all_issues),
        org=org,
        template_name=template_name,
        audience=audience,
        normalized_rtf=rtf,
        hits=tuple(hits),
        org_overrides=org_overrides,
        emit_prelude=emit_prelude,
        prelude_lines=final_prelude_lines,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Edit-time rebuild — used by the GUI's per-segment editor
# ─────────────────────────────────────────────────────────────────────────────


def rebuild_result_with_edits(
    result: ConversionResult,
    edits: dict,
    *,
    validator: Optional[Validator] = None,
) -> ConversionResult:
    """Return a new ConversionResult with one or more segments replaced
    by edited Pine outputs.

    ``edits`` maps ``segment_index → tuple[PineToken, ...]``. Indices
    not in ``edits`` are kept as-is. The original normalized RTF and
    hits are reused so this is cheap — no parse / pattern-engine
    rerun. The validator runs again and the prelude is regenerated so
    the rebuilt output stays self-consistent.

    Provenance flips to ``"edit"`` for any segment that was actually
    edited so the GUI can label it.
    """
    if validator is None:
        validator = (
            Validator(org=result.org_overrides)
            if result.org_overrides else Validator(org=None)
        )

    # Apply edits → new segments, preserving everything else.
    new_segments_list: List[ConversionSegment] = []
    for i, seg in enumerate(result.segments):
        if i in edits:
            new_segments_list.append(ConversionSegment(
                source_start_byte=seg.source_start_byte,
                source_end_byte=seg.source_end_byte,
                source_jda_tokens=seg.source_jda_tokens,
                pine_outputs=tuple(edits[i]),
                provenance=PROV_EDIT,
                pattern=seg.pattern,
            ))
        else:
            new_segments_list.append(seg)

    # Re-validate the combined Pine stream and re-attach per-segment
    # issues.
    pine_stream = [tok for seg in new_segments_list for tok in seg.pine_outputs]
    all_issues = validator.validate_stream(pine_stream) if pine_stream else []
    issues_by_token = _bucket_issues_by_token(all_issues, new_segments_list)
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
        for i, s in enumerate(new_segments_list)
    )

    # Re-reconstruct from the normalized RTF + original hits, then
    # re-prepend the prelude. Without rebuilding the prelude an edit
    # that introduces a new child-entity reference (or removes the
    # last one) would leave stale CreateVars.
    converted_rtf = _reconstruct(result.normalized_rtf, result.hits, final_segments)
    final_prelude_lines: Tuple[str, ...] = ()
    if result.emit_prelude and pine_stream:
        prelude_lines = prelude_module.generate_prelude(
            pine_stream, org_overrides=result.org_overrides,
        )
        if prelude_lines:
            converted_rtf = prelude_module.prepend_prelude_to_rtf(
                converted_rtf, prelude_lines,
            )
            final_prelude_lines = tuple(prelude_lines)

    return ConversionResult(
        converted_rtf=converted_rtf,
        segments=final_segments,
        issues=tuple(all_issues),
        org=result.org,
        template_name=result.template_name,
        audience=result.audience,
        normalized_rtf=result.normalized_rtf,
        hits=result.hits,
        org_overrides=result.org_overrides,
        emit_prelude=result.emit_prelude,
        prelude_lines=final_prelude_lines,
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
    the converted RTF to disk. The filename is automatically passed
    as ``template_name`` so the LLM's audience classifier can use it."""
    p = Path(input_path)
    rtf = p.read_text(encoding="utf-8", errors="replace")
    kwargs.setdefault("template_name", p.name)
    result = convert_template(rtf, org, **kwargs)
    if output_path is not None:
        Path(output_path).write_text(result.converted_rtf, encoding="utf-8")
    return result

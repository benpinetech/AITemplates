"""End-to-end conversion CLI — single template at a time.

Usage::

    ./venv/bin/python Agent/v2/tools/convert.py INPUT.rtf --org oba
    ./venv/bin/python Agent/v2/tools/convert.py INPUT.rtf --org oba --output OUT.rtf

The ``--org`` flag is required: Phase 5 enforces explicit declaration.
Inferring the org from JDA entity prefixes is a future convenience layer.

Exit code:
  0  — converted, no validation errors (warnings OK)
  1  — converted but has validation errors
  2  — input or argument error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import pipeline
from pipeline.engine.llm_fallback import LlmFallback, OpenAILlmClient
from pipeline.engine.validator import errors_only, warnings_only
from pipeline.grammar.loaders import load_org_overrides
from pipeline.patterns import loader as pattern_loader


_PINE_CONTEXT_PATH = REPO_ROOT / "pine_context.md"
_LLM_EXAMPLES_PATH = REPO_ROOT / "pipeline" / "engine" / "llm_examples.toml"


def _load_shape_examples():
    """Load ``engine/llm_examples.toml`` as a list of (match, rewrite)
    tuples for the LLM's prompt. Empty list on any error — the LLM
    still works, just without shape templates.

    These are NEVER loaded by the pattern loader (they live outside
    ``patterns/library/``) and never participate in deterministic
    matching. They're prompt enrichment only.
    """
    try:
        import tomllib            # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return []
    try:
        with _LLM_EXAMPLES_PATH.open("rb") as f:
            data = tomllib.load(f)
    except Exception:  # noqa: BLE001
        return []
    examples = data.get("example", [])
    out = []
    for ex in examples:
        m = ex.get("match")
        r = ex.get("rewrite", "")
        if isinstance(m, str) and isinstance(r, str):
            out.append((m, r))
    return out


def _load_grammar_fragment() -> str:
    """Read the Pine mapping awareness guide (``Agent/pine_context.md``)
    so the LLM has a field-level reference to consult inline. The
    universal role enum + per-org vocabulary tell the model which
    entity to use; this fragment tells it how to render the FIELDS
    on that entity (``.FullName`` wrappers, address subfields, casing,
    etc.). Failure to load is non-fatal — the LLM just runs without
    the reference, same as before this wiring.
    """
    try:
        return _PINE_CONTEXT_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _make_llm_fallback(org: str) -> Optional[LlmFallback]:
    """Construct an ``LlmFallback`` for this conversion if the env is
    set up for it. Returns ``None`` (so the pipeline falls back to
    patterns-only) when:

      - ``OPENAI_API_KEY`` is unset — no credentials available.
      - The ``openai`` SDK isn't importable.
      - Loading the org overrides or pattern library raises.

    Failure is silent here because the GUI shouldn't refuse to render a
    conversion just because the LLM happens to be unavailable; the
    user sees pattern-only output rather than an error dialog.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        client = OpenAILlmClient()
    except RuntimeError:
        return None
    try:
        library = pattern_loader.load_library().patterns
    except Exception:  # noqa: BLE001 — fallback should never abort convert
        library = []
    try:
        org_overrides = load_org_overrides(org) if org != "any" else None
    except Exception:  # noqa: BLE001
        org_overrides = None
    return LlmFallback(
        client=client,
        library=library,
        org_overrides=org_overrides,
        grammar_fragment=_load_grammar_fragment(),
        shape_examples=_load_shape_examples(),
    )


def _count_top_level_pine_chips(text: str) -> int:
    """Count bracket-balanced ``@[…]`` openers at depth zero.

    Mirrors the renderer's tokenizer: nested ``[`` / ``]`` inside a chip
    body don't open new chips. Used to tell the GUI how many leading
    Pine chips in the converted RTF came from the prelude (and have no
    backing segment in ``result.segments``)."""
    count = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if depth == 0 and c == "@" and i + 1 < n and text[i + 1] == "[":
            count += 1
            depth = 1
            i += 2
            continue
        if depth > 0:
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
        i += 1
    return count


def _result_to_json(result, *, source_rtf: str, source_path: Path) -> dict:
    """Serialise a ``ConversionResult`` to a GUI-friendly JSON shape.

    Designed to be the wire format between the v2 pipeline and the
    Electron desktop app. Per-segment data lets the renderer render
    legacy + converted side-by-side with provenance colors and wire up
    inline-edit popups."""
    # Count the Pine chips contributed by the CreateVar prelude so the
    # GUI can offset chip-to-segment lookups. The prelude is prepended
    # to ``converted_rtf`` AFTER segments are built — without this hint
    # the renderer would map prelude chips onto the first real segments
    # and surface wrong provenance / source info on hover.
    #
    # Only top-level ``@[`` count: each prelude line looks like
    # ``@[CreateVar(..., @[builtin.CaseID])]`` where the inner ``@[…]``
    # is nested inside the outer chip body. The renderer's tokenizer is
    # bracket-aware and treats the whole thing as ONE chip — so a naive
    # ``"@["`` substring count would over-count the offset and push
    # body chips off the segment list.
    prelude_pine_token_count = _count_top_level_pine_chips(
        "\n".join(result.prelude_lines or ())
    )
    return {
        "schema": "jda-pine-convert/v1",
        "source": {
            "path": str(source_path),
            "rtf": source_rtf,
        },
        "converted": {
            "rtf": result.converted_rtf,
        },
        "template_name": result.template_name,
        "audience": result.audience,
        "org": result.org,
        "prelude_pine_token_count": prelude_pine_token_count,
        "totals": {
            "jda_tokens": result.total_jda_tokens,
            "pine_tokens": result.total_pine_tokens,
            "pattern_segments": result.by_provenance.get(pipeline.PROV_PATTERN, 0),
            "llm_segments": result.by_provenance.get(pipeline.PROV_LLM, 0),
            "unmatched_segments": result.by_provenance.get(pipeline.PROV_UNMATCHED, 0),
            "edit_segments": result.by_provenance.get(pipeline.PROV_EDIT, 0),
        },
        "segments": [
            {
                "index": i,
                "provenance": seg.provenance,
                "pattern_id": seg.pattern.id if seg.pattern is not None else None,
                "source_byte_range": [seg.source_start_byte, seg.source_end_byte],
                "jda_tokens": [t.unparse() for t in seg.source_jda_tokens],
                "pine_tokens": [t.unparse() for t in seg.pine_outputs],
                "issues": [
                    {
                        "rule_id": issue.rule_id,
                        "severity": (issue.severity or "warning").lower(),
                        "message": issue.message,
                        "token_index": issue.token_index,
                    }
                    for issue in (seg.issues or ())
                ],
            }
            for i, seg in enumerate(result.segments)
        ],
        "issues": [
            {
                "rule_id": issue.rule_id,
                "severity": (issue.severity or "warning").lower(),
                "message": issue.message,
                "token_index": issue.token_index,
            }
            for issue in result.issues
        ],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="JDA RTF file to convert")
    p.add_argument("--org", required=True,
                   help="org context (e.g. 'oba'). Must match a "
                        "grammar/org_overrides/<org>.toml file or be 'any'.")
    p.add_argument("--output", type=Path, default=None,
                   help="write converted RTF here (default: stdout)")
    p.add_argument("--json", action="store_true",
                   help="print a structured JSON bundle to stdout instead "
                        "of RTF — used by the Electron GUI to render the "
                        "two panes + per-segment provenance.")
    p.add_argument("--summary", action="store_true",
                   help="print provenance + issues summary to stderr")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--no-llm", action="store_true",
                   help="skip the LLM fallback (patterns + accepted "
                        "suggestions only — useful for offline runs).")
    args = p.parse_args(argv)

    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2

    # Normalize the source RTF the same way the pipeline does before
    # parsing — Word can split a ``%[`` opener across two formatting
    # runs (``%}{\rtlch...\n[``) and the renderer needs the stitched
    # form for tokens to be contiguous in display. The pipeline output
    # is already normalized, so this just brings the source side in
    # line.
    from ..parser.rtf_extractor import normalize_rtf as _normalize_rtf
    source_rtf = _normalize_rtf(
        args.input.read_text(encoding="utf-8", errors="replace")
    )
    # Construct the LLM fallback unless explicitly disabled. The GUI's
    # Convert button hits this code path, so by default we want the LLM
    # in the loop (the user's accepted edits accumulate as patterns
    # over time, but everything not yet covered should be the LLM's
    # output, not unmatched).
    llm = None if args.no_llm else _make_llm_fallback(args.org)

    # Diagnostic line — written to stderr, never to stdout (stdout is
    # the JSON wire format). Surfaces in the Electron dev terminal via
    # main.cjs's stderr forwarder. Tells the user at a glance whether
    # the LLM is wired up for this run; "no key" means the .env loader
    # didn't put OPENAI_API_KEY in env or the user hasn't entered one
    # in Settings.
    if args.no_llm:
        print("[convert] LLM fallback disabled (--no-llm).", file=sys.stderr)
    elif llm is not None:
        model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
        print(f"[convert] LLM fallback ready (model={model}).", file=sys.stderr)
    else:
        reason = (
            "OPENAI_API_KEY not in env" if not os.environ.get("OPENAI_API_KEY")
            else "openai SDK or org overrides failed to load"
        )
        print(f"[convert] LLM fallback OFF — {reason}.", file=sys.stderr)

    try:
        result = pipeline.convert_file(
            args.input, args.org,
            output_path=args.output,
            llm_fallback=llm,
        )
    except FileNotFoundError as e:
        print(f"file error: {e}", file=sys.stderr)
        return 2

    if args.json:
        # In JSON mode, stdout is the wire format — never mix
        # human-readable summary into it. ``--summary`` still goes to
        # stderr if requested.
        bundle = _result_to_json(result, source_rtf=source_rtf, source_path=args.input)
        sys.stdout.write(json.dumps(bundle))
    elif args.output is None:
        sys.stdout.write(result.converted_rtf)

    # In JSON mode we never spam stderr unless the caller explicitly
    # asked for a summary (machine consumers like the GUI keep --json
    # alone). Otherwise the default is to show a summary.
    show_summary = args.summary if args.json else (args.summary or not args.quiet)
    if show_summary:
        print(file=sys.stderr)
        print(f"  {result.summary_line()}", file=sys.stderr)
        prov = result.by_provenance
        if prov[pipeline.PROV_UNMATCHED]:
            print(f"  unmatched JDA tokens left in output:", file=sys.stderr)
            for s in result.segments:
                if s.provenance == pipeline.PROV_UNMATCHED:
                    for tok in s.source_jda_tokens:
                        print(f"    {tok.unparse()}", file=sys.stderr)
        errors = errors_only(result.issues)
        warnings = warnings_only(result.issues)
        if errors:
            print(f"  errors ({len(errors)}):", file=sys.stderr)
            for e in errors:
                idx = "" if e.token_index is None else f" @{e.token_index}"
                print(f"    [{e.rule_id}]{idx} {e.message}", file=sys.stderr)
        if warnings and args.summary:
            print(f"  warnings ({len(warnings)}):", file=sys.stderr)
            for w in warnings:
                idx = "" if w.token_index is None else f" @{w.token_index}"
                print(f"    [{w.rule_id}]{idx} {w.message}", file=sys.stderr)

    return 1 if errors_only(result.issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())

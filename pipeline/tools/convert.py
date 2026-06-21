"""Single-template conversion — backend process spawned by the desktop app.

Reads one JDA RTF, prints the JSON conversion bundle (schema
``jda-pine-convert/v1``) to stdout for the Electron GUI to render. Not a
human CLI: the RTF/summary output modes were removed. ``--agency`` is
required.

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
from pipeline.engine.llm_converter import LlmConverter, OpenAILlmClient
from pipeline.engine.validator import errors_only
from pipeline.grammar.loaders import load_agency_overrides
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
    universal role enum + per-agency vocabulary tell the model which
    entity to use; this fragment tells it how to render the FIELDS
    on that entity (``.FullName`` wrappers, address subfields, casing,
    etc.). Failure to load is non-fatal — the LLM just runs without
    the reference, same as before this wiring.
    """
    try:
        return _PINE_CONTEXT_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _make_llm_converter(agency: str) -> Optional[LlmConverter]:
    """Construct an ``LlmConverter`` for this conversion if the env is
    set up for it. Returns ``None`` (so the pipeline falls back to
    patterns-only) when:

      - ``OPENAI_API_KEY`` is unset — no credentials available.
      - The ``openai`` SDK isn't importable.
      - Loading the agency overrides or pattern library raises.

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
        agency_overrides = load_agency_overrides(agency) if agency != "any" else None
    except Exception:  # noqa: BLE001
        agency_overrides = None
    return LlmConverter(
        client=client,
        library=library,
        agency_overrides=agency_overrides,
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
        "agency": result.agency,
        "prelude_pine_token_count": prelude_pine_token_count,
        "totals": {
            "jda_tokens": result.total_jda_tokens,
            "pine_tokens": result.total_pine_tokens,
            "suggestion_segments": result.by_provenance.get(pipeline.PROV_SUGGESTION, 0),
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
    p.add_argument("--agency", required=True,
                   help="agency context (e.g. 'oba'). Must match a "
                        "grammar/agency_overrides/<agency>.toml file or be 'any'.")
    # ``--json`` / ``--quiet`` are accepted for compatibility with how the
    # desktop app spawns this process. Output is always the JSON bundle now;
    # the human-readable RTF/summary modes were removed (GUI-only backend).
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-llm", action="store_true",
                   help="skip the LLM converter (accepted suggestions only — "
                        "useful for offline runs).")
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
    # Construct the LLM converter unless explicitly disabled.
    llm = None if args.no_llm else _make_llm_converter(args.agency)

    # Diagnostic line — written to stderr, never to stdout (stdout is
    # the JSON wire format). Surfaces in the Electron dev terminal via
    # main.cjs's stderr forwarder. Tells the user at a glance whether
    # the LLM is wired up for this run; "no key" means the .env loader
    # didn't put OPENAI_API_KEY in env or the user hasn't entered one
    # in Settings.
    if args.no_llm:
        print("[convert] LLM converter disabled (--no-llm).", file=sys.stderr)
    elif llm is not None:
        model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
        print(f"[convert] LLM converter ready (model={model}).", file=sys.stderr)
    else:
        reason = (
            "OPENAI_API_KEY not in env" if not os.environ.get("OPENAI_API_KEY")
            else "openai SDK or agency overrides failed to load"
        )
        print(f"[convert] LLM converter OFF — {reason}.", file=sys.stderr)

    try:
        result = pipeline.convert_file(
            args.input, args.agency,
            converter=llm,
        )
    except FileNotFoundError as e:
        print(f"file error: {e}", file=sys.stderr)
        return 2

    # stdout is always the JSON wire format the desktop app consumes.
    bundle = _result_to_json(result, source_rtf=source_rtf, source_path=args.input)
    sys.stdout.write(json.dumps(bundle))

    return 1 if errors_only(result.issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())

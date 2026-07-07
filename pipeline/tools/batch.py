"""Batch conversion — folder → confirm-mappings → folder.

Backend process spawned by the desktop app. Unlike ``convert.py`` (one
file, one JSON bundle), batch mode works over a whole folder of JDA RTF
templates in two phases with a human confirmation step between them:

    collect   scan every *.rtf in the input folder, extract the JDA
              fillpoints, DEDUPLICATE them to the set of unique
              ``%[...]`` tokens across the whole folder, and propose a
              Pine conversion for each (verified suggestion first, then
              a single deduplicated LLM call for the rest). Emits the
              unique-mapping table for the app to render.

    apply     given the (human-confirmed / bulk-edited) mapping table,
              convert every file deterministically using those mappings
              and write the Pine RTF outputs to the output folder.
              Optionally persist the confirmed mappings as agency-scoped
              verified suggestions so future runs reuse them.

Deduping to unique tokens is the whole point: the LLM is consulted once
per DISTINCT ``%[...]`` token across the corpus instead of once per
occurrence, and the human confirms each mapping once instead of once per
file. Both phases are scoped to a single ``--agency``.

Wire format: each phase reads a JSON request from **stdin** and writes a
JSON response to **stdout** (schemas ``jda-pine-batch-collect/v1`` /
``jda-pine-batch-apply/v1``). Diagnostics go to stderr. This mirrors
``convert.py``'s JSON-in/JSON-out contract for the Electron host.

Exit codes:
  0  — completed
  2  — input or argument error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load OPENAI_API_KEY (and friends) from the repo-root .env so dev CLI
# runs work without an explicit export. The Electron host propagates its
# own env, so this is a no-op there.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(REPO_ROOT.parent / ".env")
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from pipeline import pipeline
from pipeline.engine import suggestion_store
from pipeline.parser import branch_swap, rtf_extractor
from pipeline.patterns.schema import Pattern

PROV_SUGGESTION = "suggestion"
PROV_LLM = "llm"
PROV_UNMATCHED = "unmatched"


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _list_rtf(input_dir: Path) -> List[Path]:
    """Every ``*.rtf`` directly under ``input_dir``, sorted by name.

    Non-recursive: a batch is a flat folder of templates. ``.rtf`` match
    is case-insensitive so ``.RTF`` from Windows exports is included."""
    return sorted(
        (p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".rtf"),
        key=lambda p: p.name.lower(),
    )


def _extract_jda_tokens(raw_rtf: str):
    """Return the pipeline's JDA token stream for one RTF's raw text.

    Mirrors ``convert_template`` steps 0/0a/1 exactly (normalize the
    fragmented ``%[`` openers, branch-swap inverted ``If`` blocks, then
    extract + parse) so the tokens collected here are the same ones the
    conversion will see."""
    rtf = rtf_extractor.normalize_rtf(raw_rtf)
    rtf = branch_swap.swap_inverted_branches(rtf)
    return [
        h.ast
        for h in rtf_extractor.extract(rtf, bracket="%[", parse=True)
        if h.ast is not None
    ]


def _suggestion_lookup(
    agency: str, suggestions_root: Optional[Path]
) -> Dict[str, List[str]]:
    """Map ``jda_token_text → [pine_token_text, ...]`` from the agency's
    verified (global-scope) suggestions. These are the deterministic,
    already-human-approved mappings; the LLM is never consulted for a
    token covered here.

    Built the same way the pipeline builds its ``suggestion_lookup`` — a
    verified suggestion is a hole-less, single-token-match pattern."""
    if agency == "any":
        return {}
    patterns = suggestion_store.load_verified_for_agency(
        agency, root=suggestions_root
    )
    lookup: Dict[str, List[str]] = {}
    for p in patterns:
        if p.holes or p.is_chunk_pattern() or p.rewrite is None:
            continue
        jda_key = p.match if isinstance(p.match, str) else p.match[0]
        if jda_key in lookup:
            continue
        lookup[jda_key] = list(p.rewrite_tokens() or [])
    return lookup


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — collect the unique-mapping table
# ─────────────────────────────────────────────────────────────────────────────


def collect_mappings(
    input_dir: Path,
    agency: str,
    *,
    converter=None,
    suggestions_root: Optional[Path] = None,
    on_progress=None,
) -> dict:
    """Scan ``input_dir``, dedupe JDA tokens across every template, and
    propose a Pine conversion for each unique token.

    ``converter`` is an optional ``LlmConverter``; when provided, every
    unique token not already covered by a verified suggestion is sent
    through a SINGLE ``convert_batch`` call (one LLM round-trip for the
    whole folder). When ``None``, those tokens come back ``unmatched``
    for the human to fill in.

    ``on_progress`` is an optional callback invoked with a progress event
    dict as each file is scanned and before the LLM call, so the caller
    can stream a progress bar.

    Returns a JSON-serialisable dict (schema ``jda-pine-batch-collect/v1``).
    """
    files = _list_rtf(input_dir)

    # jda_text → aggregate occurrence info + a representative AST (for the
    # LLM call, which needs JdaToken objects, not strings).
    order: List[str] = []
    counts: Dict[str, int] = {}
    file_sets: Dict[str, List[str]] = {}
    representative: Dict[str, object] = {}
    scanned: List[dict] = []

    for i, f in enumerate(files):
        if on_progress:
            on_progress({
                "kind": "progress", "phase": "scan",
                "done": i, "total": len(files), "label": f.name,
            })
        raw = f.read_text(encoding="utf-8", errors="replace")
        try:
            tokens = _extract_jda_tokens(raw)
        except Exception as e:  # noqa: BLE001 — one bad file shouldn't sink the batch
            scanned.append({"name": f.name, "tokens": 0, "error": str(e)})
            continue
        scanned.append({"name": f.name, "tokens": len(tokens)})
        for tok in tokens:
            key = tok.unparse()
            if key not in counts:
                counts[key] = 0
                file_sets[key] = []
                representative[key] = tok
                order.append(key)
            counts[key] += 1
            if f.name not in file_sets[key]:
                file_sets[key].append(f.name)

    lookup = _suggestion_lookup(agency, suggestions_root)

    # Everything not covered by a verified suggestion is an LLM candidate.
    llm_keys = [k for k in order if k not in lookup]
    llm_result: Dict[str, List[str]] = {}
    llm_used = False
    if converter is not None and llm_keys:
        llm_used = True
        if on_progress:
            on_progress({
                "kind": "progress", "phase": "llm",
                "done": 0, "total": len(llm_keys),
                "label": f"converting {len(llm_keys)} unique tokens",
            })
        llm_tokens = [representative[k] for k in llm_keys]
        try:
            batch_outputs = converter.convert_batch(llm_tokens)
        except Exception as e:  # noqa: BLE001 — degrade to unmatched, never abort
            print(f"[batch] LLM convert_batch failed: {e}", file=sys.stderr)
            batch_outputs = [[] for _ in llm_tokens]
        for key, outs in zip(llm_keys, batch_outputs):
            llm_result[key] = [t.unparse() for t in outs]

    mappings: List[dict] = []
    for key in order:
        drop = False
        if key in lookup:
            pine_tokens = lookup[key]
            prov = PROV_SUGGESTION
            # A verified suggestion with no Pine output is an explicit
            # drop — carry that through so the table shows it as dropped
            # (resolved), not blank (unresolved).
            drop = len(pine_tokens) == 0
        elif key in llm_result and llm_result[key]:
            pine_tokens = llm_result[key]
            prov = PROV_LLM
        else:
            pine_tokens = []
            prov = PROV_UNMATCHED
        mappings.append(
            {
                "jda": key,
                "pine_tokens": pine_tokens,
                "pine": " ".join(pine_tokens),
                "provenance": prov,
                "drop": drop,
                "count": counts[key],
                "files": file_sets[key],
            }
        )

    # Most-impactful first: high occurrence count, then alphabetical.
    mappings.sort(key=lambda m: (-m["count"], m["jda"]))

    prov_counts = {PROV_SUGGESTION: 0, PROV_LLM: 0, PROV_UNMATCHED: 0}
    for m in mappings:
        prov_counts[m["provenance"]] += 1

    return {
        "schema": "jda-pine-batch-collect/v1",
        "agency": agency,
        "input_dir": str(input_dir),
        "files": scanned,
        "mappings": mappings,
        "stats": {
            "files": len(files),
            "total_tokens": sum(counts.values()),
            "unique_tokens": len(order),
            "suggestion": prov_counts[PROV_SUGGESTION],
            "llm": prov_counts[PROV_LLM],
            "unmatched": prov_counts[PROV_UNMATCHED],
            "llm_used": llm_used,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — apply the confirmed mappings to every file
# ─────────────────────────────────────────────────────────────────────────────


def _patterns_from_mappings(mappings: List[dict]) -> List[Pattern]:
    """Turn confirmed rows into exact-match Pattern objects the pipeline
    can use as a deterministic library.

    Three row shapes:
      - ``drop: true``            → a drop pattern (``rewrite = []``): the
                                    JDA token is consumed and nothing is
                                    emitted.
      - non-empty ``pine_tokens`` → a normal 1:1 or multi-output rewrite.
      - empty + not drop          → skipped (the human hasn't resolved it),
                                    so the token stays ``unmatched``."""
    patterns: List[Pattern] = []
    for i, m in enumerate(mappings):
        jda = m.get("jda")
        if not jda:
            continue
        is_drop = bool(m.get("drop"))
        pine_tokens = [p for p in (m.get("pine_tokens") or []) if p]
        if is_drop:
            rewrite: object = []
        elif pine_tokens:
            rewrite = pine_tokens[0] if len(pine_tokens) == 1 else pine_tokens
        else:
            continue
        patterns.append(
            Pattern(
                id=f"batch_{i}",
                description="batch-confirmed mapping",
                provenance="llm-generated",
                verification="verified",
                agency_context="any",
                priority=500,
                match=jda,
                rewrite=rewrite,
            )
        )
    return patterns


def _persist_mappings(
    mappings: List[dict], agency: str, suggestions_root: Optional[Path]
) -> int:
    """Save each confirmed mapping as an agency global-scope verified
    suggestion so future single-file and batch runs reuse it. Returns the
    count persisted. Global scope is deliberate here: a batch confirms a
    mapping for the WHOLE agency, which is exactly the folder-wide intent
    (see suggestion_store's scope docs). Idempotent; a changed rewrite for
    the same JDA replaces the old file."""
    persisted = 0
    for m in mappings:
        jda = m.get("jda")
        if not jda:
            continue
        is_drop = bool(m.get("drop"))
        pine_tokens = [p for p in (m.get("pine_tokens") or []) if p]
        if not is_drop and not pine_tokens:
            continue
        # A drop persists as an empty Pine list (the store's drop-pattern
        # shape); a normal mapping persists its tokens.
        save_pine: List[str] = [] if is_drop else pine_tokens
        try:
            suggestion_store.prune_conflicting_in_scope(
                jda, save_pine, agency, root=suggestions_root
            )
            suggestion_store.accept_suggestion(
                jda,
                save_pine,
                agency,
                source_template="batch",
                root=suggestions_root,
            )
            persisted += 1
        except Exception as e:  # noqa: BLE001 — one bad row shouldn't sink the save
            print(f"[batch] persist failed for {jda!r}: {e}", file=sys.stderr)
    return persisted


def apply_mappings(
    input_dir: Path,
    output_dir: Path,
    agency: str,
    mappings: List[dict],
    *,
    persist: bool = False,
    suggestions_root: Optional[Path] = None,
    on_progress=None,
) -> dict:
    """Convert every ``*.rtf`` in ``input_dir`` using ``mappings`` and
    write the Pine output to ``output_dir`` (same filename). Conversion
    is deterministic: only the confirmed mappings are applied — no LLM
    call — so a token is either matched by a confirmed mapping or left
    ``unmatched`` in the output.

    Returns a JSON-serialisable dict (schema ``jda-pine-batch-apply/v1``)
    with a per-file result row (matched / unmatched counts + the list of
    still-unmatched tokens) and totals.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    library = _patterns_from_mappings(mappings)

    persisted = (
        _persist_mappings(mappings, agency, suggestions_root) if persist else 0
    )

    files = _list_rtf(input_dir)
    results: List[dict] = []
    total_matched = 0
    total_unmatched = 0

    for i, f in enumerate(files):
        if on_progress:
            on_progress({
                "kind": "progress", "phase": "apply",
                "done": i, "total": len(files), "label": f.name,
            })
        raw = f.read_text(encoding="utf-8", errors="replace")
        out_path = output_dir / f.name
        try:
            result = pipeline.convert_template(
                raw,
                agency=agency,
                library=library,
                include_verified_suggestions=False,
                converter=None,
                template_name=f.name,
            )
        except Exception as e:  # noqa: BLE001 — record the failure, keep going
            results.append({"name": f.name, "error": str(e)})
            continue

        out_path.write_text(result.converted_rtf, encoding="utf-8")
        prov = result.by_provenance
        matched = prov.get(pipeline.PROV_SUGGESTION, 0)
        unmatched = prov.get(pipeline.PROV_UNMATCHED, 0)
        total_matched += matched
        total_unmatched += unmatched
        unmatched_tokens = sorted(
            {
                s.source_jda_tokens[0].unparse()
                for s in result.segments
                if s.provenance == pipeline.PROV_UNMATCHED and s.source_jda_tokens
            }
        )
        results.append(
            {
                "name": f.name,
                "out_path": str(out_path),
                "matched": matched,
                "unmatched": unmatched,
                "unmatched_tokens": unmatched_tokens,
            }
        )

    return {
        "schema": "jda-pine-batch-apply/v1",
        "agency": agency,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "files": results,
        "stats": {
            "files": len(files),
            "written": sum(1 for r in results if "out_path" in r),
            "errors": sum(1 for r in results if "error" in r),
            "matched": total_matched,
            "unmatched": total_unmatched,
            "persisted": persisted,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI — JSON in (stdin) / JSON out (stdout)
# ─────────────────────────────────────────────────────────────────────────────


def _read_stdin_json() -> dict:
    data = sys.stdin.read()
    if not data.strip():
        return {}
    return json.loads(data)


def _emit(obj: dict) -> None:
    """Write one NDJSON line to stdout and flush, so the Electron host
    receives progress events incrementally rather than all at once when
    the process exits."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _make_converter(agency: str, use_llm: bool):
    """Build the LLM converter for a collect run, reusing convert.py's
    constructor (respects OPENAI_API_KEY, agency overrides, grammar
    fragment). Returns None when disabled or unavailable."""
    if not use_llm:
        return None
    from pipeline.tools.convert import _make_llm_converter

    return _make_llm_converter(agency)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="phase", required=True)
    sub.add_parser("collect", help="scan a folder → unique-mapping table (JSON)")
    sub.add_parser("apply", help="apply confirmed mappings → write outputs (JSON)")
    args = p.parse_args(argv)

    try:
        req = _read_stdin_json()
    except json.JSONDecodeError as e:
        print(f"batch: invalid JSON request on stdin: {e}", file=sys.stderr)
        return 2

    agency = req.get("agency")
    if not agency:
        print("batch: request missing 'agency'", file=sys.stderr)
        return 2

    if args.phase == "collect":
        input_dir = Path(req.get("input_dir", ""))
        if not input_dir.is_dir():
            print(f"batch: input_dir not a directory: {input_dir}", file=sys.stderr)
            return 2
        converter = _make_converter(agency, bool(req.get("use_llm", True)))
        if req.get("use_llm", True) and converter is None:
            print("[batch] LLM unavailable — unmatched tokens left for manual "
                  "mapping.", file=sys.stderr)
        bundle = collect_mappings(
            input_dir, agency, converter=converter, on_progress=_emit
        )
        _emit({"kind": "result", "bundle": bundle})
        return 0

    if args.phase == "apply":
        input_dir = Path(req.get("input_dir", ""))
        output_dir = Path(req.get("output_dir", ""))
        if not input_dir.is_dir():
            print(f"batch: input_dir not a directory: {input_dir}", file=sys.stderr)
            return 2
        if not req.get("output_dir"):
            print("batch: request missing 'output_dir'", file=sys.stderr)
            return 2
        bundle = apply_mappings(
            input_dir,
            output_dir,
            agency,
            list(req.get("mappings", [])),
            persist=bool(req.get("persist", False)),
            on_progress=_emit,
        )
        _emit({"kind": "result", "bundle": bundle})
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

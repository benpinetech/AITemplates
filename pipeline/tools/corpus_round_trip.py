"""Run the v2 parsers across the verified-pair corpus and report results.

Reads every RTF in
``Agent/ground_truth/evaluation_templates/jda_to_pine/{legacy,pine}/``,
extracts each bracketed expression, parses it, unparses, and parses
again. Reports:

  - total expressions found
  - how many parsed cleanly
  - how many round-tripped (parse → unparse → parse equal)
  - top failure modes by error message
  - up to N example failing expressions per error class

This is the source of truth for "what currently parses" — Phase 1's
done-criterion is a small failure tail that we triage as we grow the
parser.

Usage:

    ./venv/bin/python Agent/v2/tools/corpus_round_trip.py
    ./venv/bin/python Agent/v2/tools/corpus_round_trip.py --side legacy
    ./venv/bin/python Agent/v2/tools/corpus_round_trip.py --examples 10
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

# Make `from pipeline.parser import ...` work whether this script is run from
# anywhere in the repo.
HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parent.parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from pipeline.parser import jda_parser, pine_parser, rtf_extractor


CORPUS_ROOT = AGENT_DIR / "ground_truth" / "evaluation_templates" / "jda_to_pine"
LEGACY_DIR = CORPUS_ROOT / "legacy"
PINE_DIR = CORPUS_ROOT / "pine"


@dataclass
class Result:
    side: str  # "legacy" or "pine"
    total: int = 0
    parsed: int = 0
    round_tripped: int = 0
    failures_by_class: Counter = None  # type: ignore[assignment]
    failure_examples: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.failures_by_class is None:
            self.failures_by_class = Counter()
        if self.failure_examples is None:
            self.failure_examples = defaultdict(list)


def _classify_error(msg: str) -> str:
    """Bucket errors by leading phrase so we can group them in the report."""
    if not msg:
        return "no error"
    first_line = msg.splitlines()[0]
    # Trim long quoted source so similar failures collapse.
    if len(first_line) > 120:
        first_line = first_line[:120] + "…"
    return first_line


def _record_failure(result: Result, src: str, err: str, examples_per_class: int) -> None:
    klass = _classify_error(err)
    result.failures_by_class[klass] += 1
    if len(result.failure_examples[klass]) < examples_per_class:
        result.failure_examples[klass].append(src)


def _process(
    rtf_paths: Iterable[Path],
    side: str,
    examples_per_class: int,
) -> Result:
    parser_fn = jda_parser.parse if side == "legacy" else pine_parser.parse
    bracket = "%[" if side == "legacy" else "@["
    result = Result(side=side)

    for path in rtf_paths:
        try:
            rtf = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"warning: cannot read {path}: {e}", file=sys.stderr)
            continue

        for hit in rtf_extractor.extract(rtf, bracket=bracket, parse=True):
            result.total += 1
            if hit.error is not None:
                _record_failure(result, hit.text, hit.error, examples_per_class)
                continue
            result.parsed += 1
            try:
                ast2 = parser_fn(hit.ast.unparse())  # type: ignore[union-attr]
            except Exception as e:  # noqa: BLE001
                _record_failure(result, hit.text, f"round-trip reparse: {e}", examples_per_class)
                continue
            if ast2.inner == hit.ast.inner:  # type: ignore[union-attr]
                result.round_tripped += 1
            else:
                _record_failure(
                    result,
                    hit.text,
                    f"round-trip structural mismatch: {hit.ast.unparse()!r}",  # type: ignore[union-attr]
                    examples_per_class,
                )
    return result


def _print_report(r: Result, examples_per_class: int) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {r.side.upper()}")
    print(f"{'=' * 72}")
    print(f"  total expressions found:     {r.total}")
    if r.total == 0:
        return
    parse_pct = r.parsed / r.total
    rt_pct = r.round_tripped / r.total
    print(f"  parsed successfully:         {r.parsed} ({parse_pct:.1%})")
    print(f"  round-tripped structurally:  {r.round_tripped} ({rt_pct:.1%})")
    print(f"  distinct failure classes:    {len(r.failures_by_class)}")
    if not r.failures_by_class:
        print("  (no failures)")
        return
    print(f"\n  top failure classes (count, message):")
    for klass, count in r.failures_by_class.most_common(15):
        print(f"    {count:5d}  {klass}")
        for ex in r.failure_examples[klass][:examples_per_class]:
            print(f"             ↳ {ex!r}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--side",
        choices=("legacy", "pine", "both"),
        default="both",
        help="Which corpus to walk (default: both).",
    )
    p.add_argument(
        "--examples",
        type=int,
        default=3,
        help="How many failing example expressions to print per error class.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most this many RTF files per side (debugging aid).",
    )
    args = p.parse_args(argv)

    sides = ["legacy", "pine"] if args.side == "both" else [args.side]
    overall_total = 0
    overall_rt = 0
    for side in sides:
        d = LEGACY_DIR if side == "legacy" else PINE_DIR
        if not d.exists():
            print(f"warning: corpus directory missing: {d}", file=sys.stderr)
            continue
        files = sorted(d.glob("*.rtf"))
        if args.limit is not None:
            files = files[: args.limit]
        result = _process(files, side, args.examples)
        _print_report(result, args.examples)
        overall_total += result.total
        overall_rt += result.round_tripped

    if overall_total > 0:
        print(f"\n{'=' * 72}")
        print(f"  OVERALL: {overall_rt}/{overall_total} round-trip "
              f"({overall_rt / overall_total:.1%})")
        print(f"{'=' * 72}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the v2 pipeline across the verified-pair corpus and report stats.

This is the v2 analogue of v1's eval suite. It walks every legacy
RTF in the corpus, converts it via ``pipeline.convert_template``,
and prints aggregate numbers:

  - how many tokens converted via patterns vs LLM vs unmatched
  - validation errors / warnings per template
  - top error / warning rule IDs

Usage::

    ./venv/bin/python Agent/v2/tools/corpus_pipeline.py --org oba
    ./venv/bin/python Agent/v2/tools/corpus_pipeline.py --limit 20

The LLM fallback is OFF by default — set ``--use-llm`` (which requires
``ANTHROPIC_API_KEY``) to exercise it. Without the LLM, segments that
no pattern handles stay unmatched.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parent.parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from pipeline import pipeline
from pipeline.engine.llm_converter import AnthropicLlmClient, LlmConverter
from pipeline.grammar.loaders import load_org_overrides
from pipeline.patterns import loader as pattern_loader


CORPUS_ROOT = AGENT_DIR / "ground_truth" / "evaluation_templates" / "jda_to_pine"
LEGACY_DIR = CORPUS_ROOT / "legacy"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-dir", type=Path, default=LEGACY_DIR)
    p.add_argument("--org", default="oba")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--use-llm", action="store_true",
                   help="enable Anthropic LLM fallback for unmatched chunks")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    library = pattern_loader.load_library().patterns
    org_overrides = load_org_overrides(args.org) if args.org != "any" else None

    fb = None
    if args.use_llm:
        client = AnthropicLlmClient()
        fb = LlmConverter(client=client, library=library, org_overrides=org_overrides)

    files = sorted(args.legacy_dir.glob("*.rtf"))
    if args.limit is not None:
        files = files[: args.limit]

    totals = Counter()
    rule_counts = Counter()
    per_template_errors = []

    for path in files:
        if not args.quiet:
            print(f"  {path.name}", flush=True)
        rtf = path.read_text(encoding="utf-8", errors="replace")
        try:
            result = pipeline.convert_template(
                rtf, org=args.org,
                library=library,
                org_overrides=org_overrides,
                converter=fb,
            )
        except Exception as e:  # noqa: BLE001
            print(f"    ERROR: {e}", file=sys.stderr)
            totals["errored_template"] += 1
            continue

        prov = result.by_provenance
        totals["templates"] += 1
        totals["pattern_segments"] += prov[pipeline.PROV_PATTERN]
        totals["llm_segments"] += prov[pipeline.PROV_LLM]
        totals["unmatched_segments"] += prov[pipeline.PROV_UNMATCHED]
        totals["jda_tokens"] += result.total_jda_tokens
        totals["pine_tokens"] += result.total_pine_tokens
        for issue in result.issues:
            rule_counts[(issue.severity, issue.rule_id)] += 1
            if issue.severity == "error":
                totals["errors"] += 1
            else:
                totals["warnings"] += 1
        if any(i.severity == "error" for i in result.issues):
            per_template_errors.append((path.name, sum(1 for i in result.issues if i.severity == "error")))

    print(f"\n{'=' * 70}")
    print(f"  v2 pipeline corpus run  (org={args.org!r})")
    print(f"{'=' * 70}")
    print(f"  Templates:              {totals['templates']}")
    if totals['errored_template']:
        print(f"  Errored:                {totals['errored_template']}")
    print(f"  JDA tokens:             {totals['jda_tokens']}")
    print(f"  Pine tokens:            {totals['pine_tokens']}")
    print(f"  Pattern-matched segs:   {totals['pattern_segments']}")
    print(f"  LLM-fallback segs:      {totals['llm_segments']}")
    print(f"  Unmatched segs:         {totals['unmatched_segments']}")
    if totals['pattern_segments'] + totals['llm_segments'] + totals['unmatched_segments']:
        denom = totals['pattern_segments'] + totals['llm_segments'] + totals['unmatched_segments']
        match_rate = (totals['pattern_segments'] + totals['llm_segments']) / denom
        print(f"  Coverage:               {match_rate:.1%}")
    print(f"  Validation errors:      {totals['errors']}")
    print(f"  Validation warnings:    {totals['warnings']}")
    if rule_counts:
        print()
        print("  Top issues:")
        for (severity, rule_id), n in rule_counts.most_common(10):
            print(f"    {n:5d}  [{severity}] {rule_id}")
    if per_template_errors:
        print(f"\n  Templates with errors: {len(per_template_errors)}")
        for name, n in per_template_errors[:10]:
            print(f"    {n:5d}  {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

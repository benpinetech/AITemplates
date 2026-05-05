"""Mine the verified-pair corpus and write candidate patterns.

Usage::

    ./venv/bin/python Agent/v2/tools/mine_patterns.py
    ./venv/bin/python Agent/v2/tools/mine_patterns.py --output /tmp/candidates
    ./venv/bin/python Agent/v2/tools/mine_patterns.py --org oba --limit 20

Produces one TOML file per unique candidate in
``Agent/v2/patterns/_candidates/`` (default). Review each file
manually; promote good ones by moving them under
``Agent/v2/patterns/library/<org>/`` and editing the id and
description into something descriptive.

The miner is read-only against the active library — running this
script never silently changes anything except the candidates
directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `from v2... import ...` work from anywhere.
HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parent.parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from v2.engine import miner
from v2.patterns import loader as pattern_loader


CORPUS_ROOT = AGENT_DIR / "ground_truth" / "evaluation_templates" / "jda_to_pine"
LEGACY_DIR = CORPUS_ROOT / "legacy"
PINE_DIR = CORPUS_ROOT / "pine"
DEFAULT_OUTPUT = AGENT_DIR / "v2" / "patterns" / "_candidates"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-dir", type=Path, default=LEGACY_DIR)
    p.add_argument("--pine-dir", type=Path, default=PINE_DIR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help="where to write candidate TOML files")
    p.add_argument("--org", default="any",
                   help="org context for engine.convert during mining")
    p.add_argument("--limit", type=int, default=None,
                   help="process at most this many template pairs")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    report = pattern_loader.load_library()
    if not report.ok:
        print("warning: pattern library has issues; proceeding anyway:", file=sys.stderr)
        for issue in report.issues:
            print(f"  {issue.file}: {issue.pattern_id}: {issue.message}", file=sys.stderr)
    library = report.patterns

    miner_report = miner.MiningReport()
    legacy_files = sorted(args.legacy_dir.glob("*.rtf"))
    if args.limit is not None:
        legacy_files = legacy_files[: args.limit]

    for path in legacy_files:
        pine_path = args.pine_dir / path.name
        if not pine_path.exists():
            continue
        if not args.quiet:
            print(f"  {path.name}", flush=True)
        legacy_rtf = path.read_text(encoding="utf-8", errors="replace")
        pine_rtf = pine_path.read_text(encoding="utf-8", errors="replace")
        miner.mine_template(legacy_rtf, pine_rtf, library, args.org, path.name, miner_report)

    print(f"\n{'=' * 60}")
    print(f"Mining report")
    print(f"{'=' * 60}")
    print(f"  Templates processed:        {len(legacy_files)}")
    print(f"  Token-pairs examined:       {miner_report.pairs_examined}")
    print(f"  Already covered:            {miner_report.pairs_already_covered}")
    print(f"  Skipped (count mismatch):   {miner_report.pairs_skipped_count_mismatch}")
    print(f"  Un-mineable (slice 1):      {miner_report.pairs_unmineable}")
    print(f"  Distinct candidates:        {len(miner_report.candidates)}")

    if miner_report.candidates:
        written = miner.write_candidates(miner_report, args.output)
        print(f"\n  Wrote {len(written)} candidates to {args.output}")
        if not args.quiet:
            for c in miner_report.candidates.values():
                print(f"    {c.id}  ←  {c.source_template}#{c.source_index}")
                print(f"      match:   {c.match}")
                print(f"      rewrite: {c.rewrite}")
    else:
        print(f"\n  No new candidates found.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

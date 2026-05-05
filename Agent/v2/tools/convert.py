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
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parent.parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from v2 import pipeline
from v2.engine.validator import errors_only, warnings_only


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="JDA RTF file to convert")
    p.add_argument("--org", required=True,
                   help="org context (e.g. 'oba'). Must match a "
                        "grammar/org_overrides/<org>.toml file or be 'any'.")
    p.add_argument("--output", type=Path, default=None,
                   help="write converted RTF here (default: stdout)")
    p.add_argument("--summary", action="store_true",
                   help="print provenance + issues summary to stderr")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2

    try:
        result = pipeline.convert_file(
            args.input, args.org, output_path=args.output,
        )
    except FileNotFoundError as e:
        print(f"file error: {e}", file=sys.stderr)
        return 2

    if args.output is None:
        sys.stdout.write(result.converted_rtf)

    if args.summary or not args.quiet:
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

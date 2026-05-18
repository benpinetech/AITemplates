"""Regenerate the CreateVar prelude in a converted Pine RTF.

Reads the full RTF from stdin, strips any existing GENERATED CreateVar
prelude block (only when ``--prelude-count`` is > 0), regenerates from
the supplied Pine token strings, and writes {"ok": true, "rtf": "..."}
JSON to stdout.  On failure writes {"error": "..."} instead.

When ``--prelude-count 0`` (the default), no stripping is attempted —
this is safe for documents where ``@[CreateVar]`` tokens come from the
original source file rather than our prelude generator.

Usage::

    echo '<rtf string>' | python -m Agent.v2.tools.update_prelude \\
        --tokens '["@[Respondent.first.NameFirstName]", ...]' \\
        --prelude-count 2 \\
        [--org oba]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parent.parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from pipeline.engine.prelude import generate_prelude, prepend_prelude_to_rtf
from pipeline.grammar.loaders import load_org_overrides
from pipeline.parser.pine_parser import parse as parse_pine_token


def _strip_prelude(rtf: str) -> str:
    """Remove the CreateVar prelude block that prepend_prelude_to_rtf inserted.

    Only called when we KNOW a generated prelude exists (prelude_count > 0).
    The block was inserted immediately before the first ``\\par`` in the
    body, producing: ``...preamble... @[CreateVar(...)] \\par \\par...body...``

    We locate the first ``@[CreateVar`` and strip through the ``\\par ``
    separator that was appended after the block.
    """
    cv_idx = rtf.find("@[CreateVar")
    if cv_idx < 0:
        return rtf

    par_idx = rtf.find("\\par", cv_idx)
    if par_idx < 0:
        return rtf

    after = par_idx + 4  # skip \par
    if after < len(rtf) and rtf[after] == " ":
        after += 1

    return rtf[:cv_idx] + rtf[after:]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tokens", required=True,
        help="JSON array of current Pine token strings, e.g. '[\"@[Respondent.first.NameFirstName]\"]'",
    )
    ap.add_argument("--org", default="oba", help="Org name for override config (default: oba)")
    ap.add_argument(
        "--prelude-count", type=int, default=0,
        help="prelude_pine_token_count from the original conversion result (default: 0 = no generated prelude)",
    )
    args = ap.parse_args()

    try:
        token_strings: list[str] = json.loads(args.tokens)
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps({"error": f"bad --tokens JSON: {exc}"}))
        return

    rtf = sys.stdin.read()
    if not rtf:
        sys.stdout.write(json.dumps({"error": "no RTF received on stdin"}))
        return

    # Parse each token string into a PineToken AST node.
    pine_tokens = []
    for s in token_strings:
        try:
            pine_tokens.append(parse_pine_token(s))
        except Exception:
            pass  # skip malformed tokens — don't abort the whole save

    # Load org overrides for proper type-code / pre-declared lookup.
    try:
        org_overrides = load_org_overrides(args.org)
    except Exception:
        org_overrides = None

    # Only strip a generated prelude when we know one was inserted.
    # If prelude_count == 0, @[CreateVar] tokens in the RTF came from
    # the source file, not our prelude generator — leave them untouched.
    stripped = _strip_prelude(rtf) if args.prelude_count > 0 else rtf
    try:
        prelude_lines = generate_prelude(pine_tokens, org_overrides)
    except Exception as exc:
        sys.stdout.write(json.dumps({"error": f"prelude generation failed: {exc}"}))
        return

    updated = prepend_prelude_to_rtf(stripped, prelude_lines)
    sys.stdout.write(json.dumps({"ok": True, "rtf": updated}))


if __name__ == "__main__":
    main()

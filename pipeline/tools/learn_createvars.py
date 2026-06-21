"""Learn agency CreateVar definitions from mapper corrections.

Backend process for the desktop converter's CreateVar HITL loop. Takes an
agency and a JSON array of Pine token strings (the corrected CreateVar
declarations — non-CreateVar tokens are ignored) and merges the structured
facts into the agency's learned role tables via
:func:`engine.createvar_learning.learn_createvars`.

Prints ``{"learned": <n>, "entities": [...]}`` to stdout, or ``{"error": ...}``.

    ./venv/bin/python -m pipeline.tools.learn_createvars \\
        --agency oba --tokens '["@[CreateVar(@Guardian, ...)]"]'
"""

from __future__ import annotations

import argparse
import json
import sys

from ..engine.createvar_learning import learn_createvars


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agency", required=True, help="agency slug")
    p.add_argument("--tokens", required=True,
                   help="JSON array of Pine token strings to learn from")
    args = p.parse_args(argv)

    try:
        tokens = json.loads(args.tokens)
        if not isinstance(tokens, list):
            raise ValueError("--tokens must be a JSON array")
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"bad --tokens JSON: {e}"}))
        return 1

    try:
        result = learn_createvars(args.agency, [str(t) for t in tokens])
    except Exception as e:  # noqa: BLE001 — emit a JSON error the GUI can show
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

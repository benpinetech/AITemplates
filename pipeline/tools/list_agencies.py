"""List the configured agencies as JSON.

Used by the desktop converter to populate the Agency picker. Scans
``grammar/agency_overrides/*.toml`` via :func:`grammar.loaders.list_agencies`
and prints ``[{"id", "description"}]`` to stdout.

    ./venv/bin/python -m pipeline.tools.list_agencies
"""

from __future__ import annotations

import json
import sys

from ..grammar.loaders import list_agencies


def main(argv=None) -> int:
    try:
        agencies = list_agencies()
    except Exception as e:  # noqa: BLE001 — emit a JSON error the GUI can show
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(agencies))
    return 0


if __name__ == "__main__":
    sys.exit(main())

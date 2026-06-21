"""Create a new agency from a display name and print it as JSON.

Used by the desktop converter's "Add agency" flow. Writes a minimal
``grammar/agency_overrides/<slug>.toml`` via
:func:`grammar.loaders.create_agency` and prints ``{"id", "description"}``
to stdout, or ``{"error": ...}`` on failure.

    ./venv/bin/python -m pipeline.tools.create_agency --name "Oklahoma Bar Association"
"""

from __future__ import annotations

import argparse
import json
import sys

from ..grammar.loaders import create_agency


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True, help="agency display name")
    args = p.parse_args(argv)

    try:
        created = create_agency(args.name)
    except FileExistsError as e:
        print(json.dumps({"error": str(e), "code": "exists"}))
        return 1
    except ValueError as e:
        print(json.dumps({"error": str(e), "code": "invalid"}))
        return 1
    except Exception as e:  # noqa: BLE001 — emit a JSON error the GUI can show
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(created))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Delete an agency and everything tied to it — backend process for the
desktop converter's "Manage agencies" dialog.

Removes ``grammar/agency_overrides/<slug>.toml``, its learned-tables file,
and the agency's verified-suggestion tree. Prints ``{"ok": true, "id"}`` to
stdout, or ``{"error", "code"?}`` on failure.

    ./venv/bin/python -m pipeline.tools.delete_agency --slug oba
"""

from __future__ import annotations

import argparse
import json
import sys

from ..engine import suggestion_store
from ..grammar.loaders import delete_agency


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True, help="agency id to delete")
    args = p.parse_args(argv)

    try:
        result = delete_agency(args.slug)
        suggestion_store.delete_agency_suggestions(args.slug)
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e), "code": "missing"}))
        return 1
    except ValueError as e:
        print(json.dumps({"error": str(e), "code": "invalid"}))
        return 1
    except Exception as e:  # noqa: BLE001 — emit a JSON error the GUI can show
        print(json.dumps({"error": str(e)}))
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

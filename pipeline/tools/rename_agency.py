"""Rename an agency and migrate its data — backend process for the desktop
converter's "Manage agencies" dialog.

Derives a new slug from the new display name, rewrites the ``[agency]`` id +
description in the override config, and moves the override file, the learned
table, and the verified-suggestion tree to the new id. Prints
``{"id", "description", "old_id"}`` to stdout, or ``{"error", "code"?}``.

    ./venv/bin/python -m pipeline.tools.rename_agency --slug oba --name "Oklahoma Bar"
"""

from __future__ import annotations

import argparse
import json
import sys

from ..engine import suggestion_store
from ..grammar.loaders import rename_agency


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True, help="current agency id")
    p.add_argument("--name", required=True, help="new display name")
    args = p.parse_args(argv)

    try:
        result = rename_agency(args.slug, args.name)
        suggestion_store.move_agency_suggestions(result["old_id"], result["id"])
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e), "code": "missing"}))
        return 1
    except FileExistsError as e:
        print(json.dumps({"error": str(e), "code": "exists"}))
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

"""CLI wrapper around ``suggestion_store.accept_suggestion`` for the
desktop converter's auto-persist flow.

The Electron main process spawns this when the user commits an inline
edit. It does two things in order:

1. Prune any prior file in the same scope whose JDA match equals the
   incoming JDA but whose rewrite differs — so re-editing a chip
   replaces rather than accumulating files at the same priority.
2. Save the new mapping via ``accept_suggestion``.

Input is a single ``--payload`` JSON argument so argv escaping doesn't
become an issue when JDA / Pine tokens contain quotes, brackets, or
backslashes. Schema::

    {
      "agency":            "oba",
      "scope":          {"kind": "template", "value": "Letter to C.rtf"},
      "jda_tokens":     ["%[Cust_Name]"],
      "pine_tokens":    ["@[Complainant.first.NameFirst]", " ", "@[Complainant.first.NameLast]"],
      "source_template": "Letter to C.rtf",
      "segment_index":   12,
      "note":            "inline edit from converter_app"   (optional)
    }

The CLI prints a JSON status object to stdout on success and exits 0;
on failure prints a JSON ``{"error": "..."}`` to stdout and exits 1.
Designed to be machine-readable from the Electron side.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..engine import suggestion_store


def _parse_scope(payload_scope) -> suggestion_store.Scope:
    if payload_scope is None:
        return (suggestion_store.SCOPE_TEMPLATE, "")
    kind = payload_scope.get("kind")
    value = payload_scope.get("value", "")
    return (kind, value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Persist an inline edit as a scoped suggestion."
    )
    parser.add_argument(
        "--payload", required=True,
        help="JSON object — see module docstring for the schema."
    )
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad --payload JSON: {e}"}))
        return 1

    try:
        agency = payload["agency"]
        jda_tokens = payload["jda_tokens"]
        pine_tokens = payload["pine_tokens"]
        scope = _parse_scope(payload.get("scope"))
    except KeyError as e:
        print(json.dumps({"error": f"missing required field: {e}"}))
        return 1

    try:
        removed = suggestion_store.prune_conflicting_in_scope(
            jda_tokens, pine_tokens, agency, scope=scope,
        )
        path = suggestion_store.accept_suggestion(
            jda_tokens, pine_tokens, agency,
            scope=scope,
            source_template=payload.get("source_template"),
            source_segment_index=payload.get("segment_index"),
            note=payload.get("note"),
        )
    except ValueError as e:
        # accept_suggestion's deliberate guards (empty match, bad pine).
        print(json.dumps({"error": str(e)}))
        return 1
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"unexpected: {type(e).__name__}: {e}"}))
        return 1

    print(json.dumps({
        "ok": True,
        "path": str(path),
        "removed": [str(p) for p in removed],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

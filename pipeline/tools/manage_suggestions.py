"""CLI for the converter app's "Saved Mappings" panel.

Modes:
  --mode list   --agency <agency>
      Print a JSON array of every suggestion on disk for the agency.

  --mode delete --file <abs-path>
      Delete a single suggestion file. Returns {ok: true}.

  --mode update --file <old-abs-path> --payload <json>
      Replace a suggestion's Pine side. Payload schema:
        { agency, scope: {kind, value}, jda_tokens, pine_tokens }
      Prunes the old file, writes the new one, returns {ok, path}.

All output is a single JSON line to stdout; exit 0 on success, 1 on error.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from ..engine import suggestion_store


def _list_all(agency: str) -> list[dict[str, Any]]:
    root = suggestion_store._resolve_root(None)
    agency_safe = suggestion_store._safe_agency(agency)
    agency_dir = Path(root) / suggestion_store.VERIFIED_DIRNAME / agency_safe
    if not agency_dir.exists():
        return []

    subdirs: list[tuple[Path, str, str]] = []
    if (agency_dir / "global").exists():
        subdirs.append((agency_dir / "global", "global", ""))
    by_tmpl = agency_dir / "by_template"
    if by_tmpl.exists():
        for d in sorted(by_tmpl.iterdir()):
            if d.is_dir():
                subdirs.append((d, "template", d.name))
    by_aud = agency_dir / "by_audience"
    if by_aud.exists():
        for d in sorted(by_aud.iterdir()):
            if d.is_dir():
                subdirs.append((d, "audience", d.name))

    results = []
    for d, scope_kind, scope_value in subdirs:
        for toml_file in sorted(d.glob("*.toml")):
            try:
                with open(toml_file, "rb") as f:
                    data = tomllib.load(f)
                patterns = data.get("pattern", [])
                if not patterns:
                    continue
                p = patterns[0]
                match = p.get("match", "")
                rewrite = p.get("rewrite", [])
                jda_tokens = [match] if isinstance(match, str) else list(match)
                pine_tokens = [rewrite] if isinstance(rewrite, str) else list(rewrite)
                results.append({
                    "file": str(toml_file),
                    "scope_kind": scope_kind,
                    "scope_value": scope_value,
                    "agency": agency_safe,
                    "jda_tokens": jda_tokens,
                    "pine_tokens": pine_tokens,
                })
            except Exception:
                continue
    return results


def _delete(file: str) -> dict[str, Any]:
    p = Path(file)
    if not p.exists():
        return {"error": f"file not found: {file}"}
    try:
        p.unlink()
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def _update(file: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        agency = payload["agency"]
        scope_raw = payload.get("scope", {})
        jda_tokens = payload["jda_tokens"]
        pine_tokens = payload["pine_tokens"]
    except KeyError as e:
        return {"error": f"missing field: {e}"}

    scope = (scope_raw.get("kind", "global"), scope_raw.get("value", ""))

    try:
        suggestion_store.prune_conflicting_in_scope(
            jda_tokens, pine_tokens, agency, scope=scope,
        )
        # Also delete the old file directly in case prune didn't catch it
        # (e.g., the user changed only whitespace in the display).
        old = Path(file)
        if old.exists():
            old.unlink(missing_ok=True)

        new_path = suggestion_store.accept_suggestion(
            jda_tokens, pine_tokens, agency, scope=scope,
        )
        return {"ok": True, "path": str(new_path)}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["list", "delete", "update"])
    # Only ``list`` needs an agency (to scope the scan). ``delete``/``update``
    # act on an absolute file path and carry the agency inside ``--payload``,
    # so requiring it here would make those modes fail before they run.
    parser.add_argument("--agency", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--payload", default="{}")
    args = parser.parse_args(argv)

    if args.mode == "list":
        if not args.agency:
            print(json.dumps({"error": "--agency required for list"}))
            return 1
        result = _list_all(args.agency)
        print(json.dumps(result))
        return 0

    if args.mode == "delete":
        if not args.file:
            print(json.dumps({"error": "--file required for delete"}))
            return 1
        r = _delete(args.file)
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if args.mode == "update":
        if not args.file:
            print(json.dumps({"error": "--file required for update"}))
            return 1
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"bad --payload JSON: {e}"}))
            return 1
        r = _update(args.file, payload)
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

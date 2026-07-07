"""
Single entry point for the PyInstaller sidecar binary.

Usage: jda_pine_sidecar <tool> [args...]

  convert            -- run pipeline/tools/convert.py
  batch              -- run pipeline/tools/batch.py (collect | apply)
  persist_suggestion -- run pipeline/tools/persist_suggestion.py
  manage_suggestions -- run pipeline/tools/manage_suggestions.py
  update_prelude     -- run pipeline/tools/update_prelude.py
  list_agencies      -- run pipeline/tools/list_agencies.py
  create_agency      -- run pipeline/tools/create_agency.py
  delete_agency      -- run pipeline/tools/delete_agency.py
  rename_agency      -- run pipeline/tools/rename_agency.py
  learn_createvars   -- run pipeline/tools/learn_createvars.py

The tool name is stripped from sys.argv before delegating so each
tool's argparse sees a clean argv identical to being called directly.
"""

import sys


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    tool = sys.argv[1]
    sys.argv = [f"jda_pine_sidecar/{tool}", *sys.argv[2:]]

    if tool == "convert":
        from pipeline.tools.convert import main as _main
        sys.exit(_main() or 0)
    elif tool == "batch":
        from pipeline.tools.batch import main as _main
        sys.exit(_main() or 0)
    elif tool == "persist_suggestion":
        from pipeline.tools.persist_suggestion import main as _main
        sys.exit(_main() or 0)
    elif tool == "manage_suggestions":
        from pipeline.tools.manage_suggestions import main as _main
        sys.exit(_main() or 0)
    elif tool == "update_prelude":
        from pipeline.tools.update_prelude import main as _main
        _main()
    elif tool == "list_agencies":
        from pipeline.tools.list_agencies import main as _main
        sys.exit(_main() or 0)
    elif tool == "create_agency":
        from pipeline.tools.create_agency import main as _main
        sys.exit(_main() or 0)
    elif tool == "delete_agency":
        from pipeline.tools.delete_agency import main as _main
        sys.exit(_main() or 0)
    elif tool == "rename_agency":
        from pipeline.tools.rename_agency import main as _main
        sys.exit(_main() or 0)
    elif tool == "learn_createvars":
        from pipeline.tools.learn_createvars import main as _main
        sys.exit(_main() or 0)
    else:
        print(f"Unknown tool: {tool!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
Single entry point for the PyInstaller sidecar binary.

Usage: jda_pine_sidecar <tool> [args...]

  convert            -- run pipeline/tools/convert.py
  persist_suggestion -- run pipeline/tools/persist_suggestion.py
  manage_suggestions -- run pipeline/tools/manage_suggestions.py
  update_prelude     -- run pipeline/tools/update_prelude.py

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
    elif tool == "persist_suggestion":
        from pipeline.tools.persist_suggestion import main as _main
        sys.exit(_main() or 0)
    elif tool == "manage_suggestions":
        from pipeline.tools.manage_suggestions import main as _main
        sys.exit(_main() or 0)
    elif tool == "update_prelude":
        from pipeline.tools.update_prelude import main as _main
        _main()
    else:
        print(f"Unknown tool: {tool!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

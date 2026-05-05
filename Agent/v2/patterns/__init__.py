"""Phase 2 — pattern format and seed library.

Public API:

    from v2.patterns.engine import convert
    from v2.patterns.loader import load_library

    library = load_library()                          # walks library/**/*.toml
    result = convert(jda_token, library, org="oba")
    # result.outputs  → list[PineToken]   (empty if no pattern matched)
    # result.pattern  → matched Pattern (or None)
    # result.captures → dict of hole captures (or {})

See README.md in this directory for the format spec and authoring guide.
"""

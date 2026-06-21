"""Phase 3 — structured grammar / data-model / lint / agency assets.

The legacy ``pine_syntax_ground_truth.txt`` is preserved as the
narrative reference. The parts of it that downstream code needs to
*query* (presets, data sources, anti-patterns, agency vocabularies) are
split into TOML files here, with Pydantic-validated loaders.

Public API:

    from pipeline.grammar import loaders
    grammar = loaders.load_pine_grammar()
    data_model = loaders.load_pine_data_model()
    lints = loaders.load_lint_rules()
    oba = loaders.load_agency_overrides("oba")

See README.md in this directory for the file layout and what each
asset is for.
"""

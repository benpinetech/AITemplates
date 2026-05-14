# Disabled pattern libraries

TOML files outside `library/` aren't picked up by `pattern_loader.load_library()`,
which globs `library/**/*.toml`. Moving an org's patterns here is the
soft-delete mechanism for the **LLM-first, suggestion-store-driven** flow
agreed on 2026-05-13:

  - All entity-mapping decisions go through `LlmFallback` on first
    conversion (which only fires on unmatched chunks; with org-specific
    patterns gone, that's effectively every entity token).
  - When the human accepts or refines a chip in the converter app, the
    edit is persisted via `suggestion_store.accept_suggestion`, scoped
    to `template` / `audience` / `global` as appropriate.
  - On the next conversion, `pipeline.convert_template` loads the
    accepted suggestions on top of `library/common/` so previously-seen
    mappings short-circuit the LLM call.

The structural patterns in `library/common/` are kept — they encode
Pine grammar (control envelopes, `current_date`, prompt-variable
shape, defensive null wrappers, prosnum, subdocument, initials) that
the LLM doesn't reliably reproduce and that aren't org-specific.

To restore an org's hand-authored patterns, `git mv` the directory
back into `library/`.

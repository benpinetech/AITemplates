# Phase 3 — grammar / data model / lint / agency overrides

This directory replaces sections of the monolithic
`Agent/ground_truth/pine_syntax_ground_truth.txt` (1820 lines, three
mixed concerns) with **structured, queryable assets**. The original
file stays in place as the narrative reference.

| Asset | Replaces (in `pine_syntax_ground_truth.txt`) | Consumed by |
|---|---|---|
| `pine_grammar.toml` | sections 1–12 (token format, presets, FormatName, SetCasing, control keywords, operators) | parser, validator, pattern engine |
| `pine_data_model.toml` | sections 13–22 (data sources, query parameters, field references) | validator, pattern miner |
| `lint_rules.toml` | section 35 (anti-patterns) | validator (Phase 4) |
| `agency_overrides/<agency>.toml` | sections 31–34 (OBA-specific entities, conventions, vocabulary) | validator, pattern engine, future live-API stand-in |
| `pine_idioms.md` | sections 26, 27, 29 (template patterns, edge cases) | human reference, few-shot pool for LLM fallback |

### Why split?

The ground-truth file mixes three different concerns into one
RAG-fed document:

1. **Pine language reference** — facts about syntax (presets, format
   tokens, valid SetCasing args). These are *static* and don't change
   between conversions. Querying them as data is faster and cheaper
   than RAG-ing them.
2. **Conversion rules** (sections 30, 36, 37) — how legacy JDA maps
   to Pine. These are *executable* in v2 — the seed pattern library
   in `../patterns/library/` already encodes them.
3. **Anti-hallucination patches** (section 35) — invalid Pine
   patterns that should never be emitted. These belong in a
   *validator*, not a prompt warning.

Splitting them lets each concern be authored, queried, and tested
independently. It also means a regression in one (e.g. adding a new
lint rule) doesn't push the prompt over a token budget.

### File guide

#### `pine_grammar.toml`

Hand-edited language reference. Sections:

- `[date_presets]` — preset name → format string and example
- `[format_tokens]` — single-letter tokens for FormatName (F/M/L/etc.)
- `[setcasing]` — valid `SetCasing(...)` arguments
- `[control_keywords]` — canonical Pine control flow words
- `[operators]` — comparison / logical / membership operators

Loader: `loaders.load_pine_grammar()` returns a `PineGrammar` Pydantic
model. Useful for the validator (rejecting unknown presets) and for
the parser (canonicalising keyword case).

#### `pine_data_model.toml`

The shape of Pine's queryable data. Sections:

- `[[data_source]]` — one entry per source object (Case, CaseInvolvement, etc.)
- `[query_param]` — common GetByQuery key/value patterns
- `[[field_set]]` — fields available on each kind of record (Name, Personnel, Address, …)

Loader: `loaders.load_pine_data_model()`.

#### `lint_rules.toml`

Each `[[lint_rule]]` is an anti-pattern: an invalid Pine output the
validator should reject. A rule has:

- `id` — slug
- `description` — what the rule catches
- `match_regex` — regex over the unparsed Pine token text (Phase 4
  may add an AST-pattern form)
- `severity` — `error` (block) or `warning` (surface to mapper)
- `fix_hint` — what to do instead
- `notes` — provenance reference into the ground truth file

The validator implementation lives in Phase 4. This Phase 3 deliverable
just authors the rules.

#### `agency_overrides/<agency>.toml`

Per-agency metadata and vocabulary. The OBA file is the only one
authored today; criminal/PD will follow when needed.

Each file has:

- `[agency]` — id, description, notes
- `[agency.vocabulary]` — list of valid Pine entity names for this agency
- `[agency.conventions]` — preference defaults (e.g. SetCasing on address fields)
- `[agency.subdoc_path_family]` — path-prefix → numeric IDs (agency-aware Subdocument)

Until the live agency-variable API exists (deferred — see
`../README.md` §4.2), the validator reads its allow-list from
`agency.vocabulary`.

#### `pine_idioms.md`

Narrative reference. Plain markdown copied / paraphrased from
sections 26, 27, and 29 of the ground truth file. Two roles:

- Human reference for someone reading the codebase.
- Few-shot pool for the LLM fallback in Phase 4 (when no pattern
  matches, retrieve the most relevant idioms by similarity).

### Loaders

`loaders.py` exposes Pydantic-validated entry points:

```python
from v2.grammar.loaders import (
    load_pine_grammar,
    load_pine_data_model,
    load_lint_rules,
    load_agency_overrides,
)

grammar = load_pine_grammar()
print(grammar.date_presets["preset1"].format)   # "MMMM d, yyyy"

oba = load_agency_overrides("oba")
print("Defense" in oba.vocabulary)               # True
```

Each loader returns a typed model with errors surfaced as a
`PydanticValidationError` if the TOML is malformed.

### What this directory is NOT

- **Not the pattern library.** Patterns are in `../patterns/library/`.
  This directory is reference data; patterns are conversion rules.
- **Not a vector store.** No embeddings, no similarity search. Lookups
  are dict-shaped.
- **Not the validator.** `lint_rules.toml` defines the rules; the
  code that *applies* them is Phase 4 in `../engine/validator.py`.

### What's deferred

- The data model is authored at the **shape level** — list of fields per
  category. Detailed per-field type info (e.g. "DateOfBirth is a date
  format-aware field") is a future extension, added as the validator
  needs it.
- Lint rules currently use **regex over unparsed Pine text**. AST-based
  lint rules (e.g. "no SetCasing chained on a `*Number` path") are a
  future extension — add a new field to the schema and a new check
  in the validator when a regex form gets unwieldy.
- Criminal/PD agency overrides are not yet authored — wait until we
  start converting non-OBA templates.

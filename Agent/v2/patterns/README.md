# Phase 2 — pattern format and seed library

This directory holds the chunk-rewriting engine. A *pattern* is a
typed rule that says "if a JDA AST looks like X, rewrite it as the
Pine AST Y." Patterns are TOML files — hand-authored or, later,
mined automatically — that get loaded into a registry and applied
deterministically to incoming legacy templates.

The engine has no LLM in the matching path. The LLM only enters the
picture in Phase 4 as a *fallback* for chunks that no pattern matches.

## What this module replaces

In v1, every conversion rule lived as English prose either in
`pine_syntax_ground_truth.txt` (sections 30, 36, 37) or as one of the
14+ MANDATORY RULES in `prompts.py`. Adding a rule meant editing prose
in a 1820-line file or appending another rule to a prompt. Both grow
without bound and cause the LLM to drift.

In v2, every conversion rule is one TOML file. Adding a rule means
adding a file — not editing a prompt.

## What's in this directory

| File | Role |
|---|---|
| `schema.py` | Pydantic models for pattern files (the wire format). |
| `loader.py` | Read and validate `library/**/*.toml`; return `Pattern[]`. |
| `holes.py` | Helpers for working with holes (`$name`) embedded in pattern ASTs. |
| `transforms.py` | Named pure functions that derive holes from other holes. |
| `matcher.py` | Match a Pattern against a parsed JDA AST → captured holes. |
| `rewriter.py` | Apply a matched Pattern → list of Pine ASTs. |
| `engine.py` | Top-level: `convert(jda_ast, org='oba') → Pine AST list`. |
| `library/` | The actual pattern files, grouped by org context. |

## Pattern file format (TOML)

A single TOML file holds one or more `[[pattern]]` entries. Here's
the simplest shape, with every field labelled:

```toml
[[pattern]]
# IDENTITY ─────────────────────────────────────────────────────────
id          = "oba_fullname_titlecase"          # globally unique slug
description = "TitleCase(X.FullName) → X.first.FormatName(F L).SetCasing(Title)"

# PROVENANCE ──────────────────────────────────────────────────────
provenance   = "hand-written"   # | "mined" | "llm-generated"
verification = "verified"       # | "candidate" | "deprecated"
notes        = "Section 30 — standard OBA letter form"

# APPLICABILITY ───────────────────────────────────────────────────
org_context = "oba"             # | "criminal-pd" | "any"
priority    = 100                # higher wins when multiple match

# MATCH / REWRITE ─────────────────────────────────────────────────
# `match` is JDA source with $name placeholders for holes.
# `rewrite` is Pine source with the same placeholders.
# Both can also be a list of strings — for multi-token chunks
# (match) or multi-output rewrites (e.g. Subdocument expanding to
# two SubDocument tokens).
match   = "%[TitleCase($entity.FullName)]"
rewrite = "@[$entity_pine.first.FormatName(F L).SetCasing(Title)]"

# HOLES ────────────────────────────────────────────────────────────
# Each hole referenced in `match` or `rewrite` declares its expected
# shape and any transformation pipeline. Derived holes (those with
# `derive_from`) are computed at rewrite time from their source.
[pattern.holes.entity]
kind        = "path-segment"   # the hole captures a single identifier string
description = "JDA entity name like JW_Respondent or Cust_OBAAttorney"

[pattern.holes.entity_pine]
derive_from = "entity"
transform   = "translate_jda_entity_to_pine"
description = "Pine equivalent of the JDA entity (e.g. JW_Respondent → Respondent)"
```

### Hole kinds

A hole *constrains what the matcher accepts*. The engine refuses to
match a hole whose kind doesn't fit the source AST node.

| Kind | Captures | Match position |
|---|---|---|
| `path-segment` | a single identifier string | inside a path, as one segment |
| `path` | a `JdaPath` AST node (full dotted path) | atom position |
| `expression` | any AST node | atom position |
| `literal` | a `JdaLiteral` of any kind | atom position |
| `bool` | a bool literal | atom position |
| `format-string` | a LIT_FORMAT literal | atom position |
| `subdoc-path` | a LIT_PATH literal (Subdocument arg) | atom position |

Default kind, when the field is omitted, is `expression`.

### Holes referenced in match vs rewrite

- A hole in `match` is **bound** when the pattern matches: it captures
  the corresponding value from the source AST.
- A hole in `rewrite` is **substituted**: its captured value (possibly
  passed through a transform) is plugged in.
- A hole that appears only in `rewrite` (and not in `match`) must
  declare `derive_from` plus `transform`. It's computed from another
  hole.

### Derived holes and transforms

`transforms.py` registers named pure functions. The current set:

| Transform | Input | Output | Used by |
|---|---|---|---|
| `translate_jda_entity_to_pine` | string (JDA entity name) | string (Pine entity) | every pattern that maps an entity |
| `format_to_preset` | string (JDA date pattern) | string (Pine preset name) | FormatDate patterns |
| `subdocument_path_to_pine_ids` | string (Subdocument path arg) | list of strings | Subdocument expansion |
| `prompt_variable_camel_case` | string (X.X form) | string (Pine prompt name) | prompt-variable patterns |

Adding a transform = adding a function in `transforms.py` and
referencing it by name from a pattern.

### Match shape: single token or chunk

```toml
match = "%[TitleCase($entity.FullName)]"          # single token
match = ["%[If($cond)]", "%[Else]", "$body", "%[EndIf]"]  # chunk (multi-token)
```

**Single-token patterns are the Phase 2 deliverable**. The chunk form
is parsed and the schema accepts it, but the matcher implementation
for chunks is **deferred to Phase 2.5** — the existing matcher walks
one AST at a time. See [§Limitations](#limitations).

### Rewrite shape: single token or list

```toml
rewrite = "@[$entity_pine.first.FormatName(F L).SetCasing(Title)]"
rewrite = ["@[SubDocument(5)]", "@[SubDocument(3)]"]   # OBA letterhead
```

A rewrite list produces multiple Pine outputs in source order. The
pipeline (Phase 5) flattens them into the final RTF.

### Computed rewrites (escape hatch)

When the rewrite shape can't be expressed as a fixed template — for
example, Subdocument expansion produces a *variable* number of
outputs depending on the input path — the pattern can declare a
`rewrite_function` instead of `rewrite`:

```toml
[[pattern]]
id = "subdocument_path_expansion"
match = "%[Subdocument($path)]"
rewrite_function = "expand_subdocument_path"   # registered in transforms.py
[pattern.holes.path]
kind = "subdoc-path"
```

The function receives the captures dict and the org context, returns
a list of Pine ASTs.

## How patterns are loaded

```python
from v2.patterns.loader import load_library
from v2.patterns.engine import convert

lib = load_library()              # walks library/**/*.toml
result = convert(jda_ast, lib, org="oba")
# result.outputs  → list[PineToken]
# result.pattern  → which Pattern was applied (or None on no-match)
```

Files under `library/common/` are always available. Files under
`library/<org>/` apply only when the org context matches. Both apply
together when running for a specific org.

## Authoring workflow

1. Write a new TOML file under `library/<org>/`.
2. Run the test suite — `tests/test_patterns_engine.py` exercises
   every pattern's example.
3. Run `tools/corpus_round_trip.py` to confirm no regression.
4. (Future) the mining tool emits candidate patterns under
   `library/_candidates/`; you promote them to the appropriate org
   directory after review.

## Chunk patterns (multi-token)

A chunk pattern's `match` is a list of token-pattern strings. Each
element is one of:

- a JDA token pattern (`"%[…]"`) — must structurally match one source
  token at the current stream position, with single-token holes binding
  as usual;
- a bare hole reference (`"$name"`, no surrounding brackets) — captures
  the source token at the current position into `$name`. The hole must
  be declared with `kind = "token"` (or `"expression"`);
- a sequence-hole reference (`"$name..."`) — captures **zero or more**
  source tokens into a sequence hole. The matcher tries shortest
  capture first and backtracks if the rest of the pattern can't anchor.
  The hole must be declared with `kind = "token"`.

The engine's `convert_stream(tokens, library, org)` walks the JDA
token list and tries chunk patterns first at every position. On a
chunk match, the rewriter resolves any `"$name"` references in the
rewrite by recursively calling the single-token engine on the captured
sub-tokens. When that recursion has no matching sub-pattern, the chunk
falls through and the engine retries with single-token patterns.

Concrete example — defensive null wrapper (`library/common/defensive_null.toml`):

```toml
[[pattern]]
id = "defensive_null_wrapper_collapse"
match = [
  "%[If($entity.IsEmpty=true)]",
  "$empty_body",
  "%[Else]",
  "$has_entity_body",
  "%[EndIf]",
]
rewrite = ["$has_entity_body"]    # recursively convert via the single-token engine

[pattern.holes.entity]
kind = "path-segment"

[pattern.holes.empty_body]
kind = "token"

[pattern.holes.has_entity_body]
kind = "token"
```

Five JDA tokens collapse to one Pine token (whatever the single-token
engine produces for the kept body).

## Substitution in rewrites

Two layers of substitution run on every rewrite element, in order:

1. **Textual pre-substitution.** Before parsing, every `$name`
   reference whose resolved value is a *string* gets replaced with
   that string. This is the only way to plug a hole into a position
   where the Pine parser captures raw text and won't expose it to AST
   substitution — most importantly *inside Pine string literals* like
   `'@[$info_var.Gender]'`.
2. **AST substitution.** After parsing, the rewriter walks the AST
   and replaces any remaining hole nodes (atom-position holes that
   captured AST nodes; segment-name holes that captured strings).

Sequence-hole references (`$name...`) in the rewrite aren't part of
either substitution layer — they're handled separately, expanding to
the recursively-converted Pine outputs of every captured JDA token.

## Limitations (current state)

These are intentional cuts:

- **Hole constraint expressions.** Holes are `kind`-only. Future
  patterns may need things like "path must start with `JW_`". Add as
  needed via new `kind` values or a `where` clause.
- **No prose-aware matching.** Chunks match the *token* sequence and
  don't peek at the prose between tokens. That's a feature for now —
  the prose is preserved unchanged by the pipeline — but a future
  variant may want to match on prose context (e.g. "this If only fires
  in salutation lines").
- **Chunk matcher doesn't depth-track nested control flow.** A
  sequence hole will happily capture an unmatched `%[If(...)]` from a
  nested block. We haven't seen this break a pattern in the seed
  library, but a real-world template with nested defensive-null
  guards could surface a bug. Add an If/Else/EndIf depth check if
  this comes up.

## Glossary

| Term | Meaning |
|---|---|
| Pattern | A match-rewrite rule with metadata. The unit a TOML file holds one or more of. |
| Hole | A `$name` placeholder in match/rewrite that captures or substitutes a value. |
| Transform | A registered Python function that derives one hole's value from another. |
| Capture | The runtime mapping `{hole_name: captured_value}` produced by a successful match. |
| Library | The collection of TOML pattern files under `library/`. |
| Org context | Which legal-org the conversion is for (`oba`, `criminal-pd`, `any`). Filters which patterns apply. |

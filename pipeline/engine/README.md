# v2 engine

The non-pattern halves of the conversion pipeline. The deterministic
pattern matcher lives in `../patterns/`; this directory holds the
modules that wrap, augment, and constrain it.

| Module | Role |
|---|---|
| `audience.py` | Classify the document audience (complainant / respondent / none) from filename and JDA-token frequency. One classifier consumed by both the LLM prompt and the scoped suggestion-store loader so they always agree. |
| `llm_fallback.py` | LLM batch-convert for tokens the pattern engine doesn't match. Owns prompt assembly, OpenAI client adapter, structured parsing of the response. Privacy invariant: the prompt contains only AST + org vocabulary + few-shot patterns + role-enum + grammar fragment — never prose. |
| `prelude.py` | Generate the `CreateVar` prelude (parent + child entity declarations) from the Pine tokens the pipeline emitted. Keeps converted templates self-contained so they render against any Pine deployment regardless of variable-screen pre-declarations. |
| `suggestion_store.py` | Verified-suggestion overlay store: persist a converter-reviewed mapping (1:1, 1:N, N:M, or drop) under a scope (template / audience / global). Loaded by the pipeline at conversion time so an accepted suggestion fires deterministically on the next run. |
| `validator.py` | Lint Pine output against vocabulary, structural balance (If/EndIf, Foreach/EndForEach, …), and regex lint rules. Pure function; runs after conversion. |

For the design rationale behind the current prompt shape and the
suggestion-store scoping model, see
[`../../LLM_CAPABILITY_FINDINGS.md`](../../LLM_CAPABILITY_FINDINGS.md).

## Validator

Inputs:
- a list of `PineToken` ASTs (typically the outputs of one
  chunk-aware conversion stream)
- a vocabulary allow-list (`OrgRoot.vocabulary` from
  `../grammar/loaders.py`)
- optionally a `LintRules` set (default: load from disk)

Output: a list of `ValidationIssue` objects, each with severity,
rule id, message, and the index of the offending token.

Checks:

1. **Lint rules.** Every regex in `lint_rules.toml` is run against
   each token's `unparse()` text. `severity = "error"` blocks; `severity
   = "warning"` surfaces.
2. **Vocabulary.** Every chain base must appear in
   `vocabulary.entities`, `vocabulary.builtins` (head-segment match),
   `vocabulary.prompt_variables`, or be a `cu`-style shorthand.
   ForEach-loop variables introduced by `Charges.ForEach(c)` are
   tracked in scope and accepted.
3. **Structural balance.** `If`/`EndIf`, `Foreach`/`EndForEach`,
   `Cca`/`EndCca`, `Lb`/`EndLb`. The matcher only sees individual
   tokens, so balance is counted across the stream.

Pure function — same inputs → same outputs.

## LLM fallback

Runs only on tokens the pattern engine leaves unmatched. The prompt
the model sees is assembled from a small, audited set of inputs:

- the unmatched JDA tokens (their `.unparse()` text)
- the universal Pine role enum (16 involvement + 42 assignment codes)
- the org's allowed Pine vocabulary
- a few-shot subset of the active pattern library (Jaccard-ranked)
- a grammar fragment (when supplied)
- per-input entity hints + document-audience hint + sibling-entity
  counts for disambiguation

**Privacy invariant.** Template prose never enters the prompt. The
constant prefix is the framing header + role enum + vocabulary +
grammar fragment; the variable suffix is just the few-shot examples,
the audience hint, the doc-context summary, and the numbered list of
JDA expressions. A test in `tests/test_engine_llm_fallback.py` audits
the assembled prompt against this shape.

Module shape:

```python
class LlmClient(Protocol):
    def complete(self, prompt: str) -> str: ...

class OpenAILlmClient:
    # Adapter for OpenAI / OpenAI-compatible endpoints (Anyscale,
    # Together, etc.). Reads OPENAI_API_KEY / OPENAI_MODEL /
    # OPENAI_BASE_URL by default. Supports tool-call iteration for
    # search_pine_syntax.

class LlmFallback:
    def convert_batch(
        self,
        jda_tokens: Sequence[JdaToken],
        template_name: Optional[str] = None,
    ) -> List[List[PineToken]]: ...
```

For tests, `MockLlmClient` returns canned responses keyed by prompt
substring, so prompt-assembly and response-parsing paths run without
the network.

### What was tried and removed

Several prompt-side experiments were validated, then removed when the
data showed they didn't pay off. Headline: the OBA-specific 10-rule
block and the JDA→Pine entity translation table were stripped from
the prompt (the H1 experiment in `LLM_CAPABILITY_FINDINGS.md`),
lifting macro F1 by 0.020 and reducing unmatched count by 67% on the
16-template eval. The role enum + vocabulary + few-shot now carry the
load.

Don't re-propose: longer prompts, more rules, reasoning-model swaps
(o-series exhausts the token budget on internal reasoning before
producing output), or Microsoft Presidio PII redaction (the
placeholders confuse the model into bailing more often). The
findings doc has the receipts.

## Audience classification

Two signal sources, combined:

1. Template filename — matches like `Letter to C`, `Process Ltr R`,
   `Letter to Disbarred`. Strongest signal when present.
2. Token-frequency fallback — counts recognised involvement entities
   in the extracted JDA token stream. Returns the dominant class if
   it has ≥60% share over at least 3 references.

Returns `"complainant"` / `"respondent"` / `None`. The result threads
through to:
- the LLM prompt's `DOCUMENT AUDIENCE` hint
- `suggestion_store.load_verified_for_org(..., audience=...)` so
  audience-scoped suggestions only fire on documents the classifier
  agrees on

## Prelude generation

Pine templates that reference *child* entities (`*Address`, `*Phone`,
`*Email`) need a two-step lookup: first declare the Root entity
(`Complainant`, `Respondent`, …), then declare the child filtered by
`NameID` or `PersonnelID`. `generate_prelude` walks the converted
Pine stream, identifies the referenced child entities, and produces
the ordered list of `CreateVar` declarations to prepend. Parents
before children, deduplicated.

`prepend_prelude_to_rtf` then splices that prelude into the converted
RTF at the document's first `\par` so the output is self-contained.

The classification rules are universal Pine data-model facts —
involvement entities filter by `NameID`, assignment entities filter
by `PersonnelID`. The org overrides supply the deployment-specific
type codes.

## Scoped suggestion store

Each verified suggestion is one TOML file on disk under:

```
v2/suggestions/verified/<org>/{global | by_template/<name> | by_audience/<name>}/
```

`accept_suggestion(jda, pine, org, *, scope=...)` writes a file;
`load_verified_for_org(org, *, template_name=..., audience=...)`
returns only the patterns whose scope matches the current document
(`global` always loads; `by_template` only when the filename matches;
`by_audience` only when the classifier agrees).

The store supports the full range of mapping shapes:

| shape | example | use |
|---|---|---|
| 1:1 | `%[X.A]` → `@[Y.A]` | the common case |
| 1:N | `%[X.FullName]` → `@[Y.first.NameFirstName] @[Y.first.NameLastName]` | bare-name splitting |
| N:M | `[%[A], %[B]]` → `[@[A'], @[B']]` | literal chunk pattern from a hand-edit |
| N:0 | `%[X.FBINum]` → `[]` | drop pattern — emit nothing |

Scoped overrides get priority 500 (vs 150 global, 100 seed), so a
converter's "this template's `Cust_X` should map differently" edit
wins inside its scope without polluting other templates.

A documented hazard: global suggestions are context-blind. They re-
apply on every template that mentions the same JDA token, which can
poison F1 on templates where the original mapping was correct in a
different context. The HITL flow defaults to the narrowest available
scope (template > audience > global) precisely to avoid this.

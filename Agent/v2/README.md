# Agent v2 — chunk-based JDA → Pine template converter

The production conversion pipeline. v1 in `Agent/src/` is kept for
reference only.

## What this does

Convert legal document templates from the legacy **JDA** template
language (expressions wrapped in `%[…]`) into the **Pine** template
language (`@[…]`). Templates are RTF files mixing legal prose with
bracketed expressions; the prose is preserved byte-for-byte, only the
bracketed regions are translated.

The agent runs in a **human-in-the-loop** workflow: a converter opens
a template in the desktop app (`Agent/converter_app/`), reviews the
suggested Pine output inline, edits where needed, and persists
verified mappings so the next run uses them deterministically.

## Privacy posture

The LLM only ever sees:

- bracketed expressions (`%[…]` and `@[…]`) — AST-only
- the universal Pine role enum (16 involvement + 42 assignment codes)
- the org's allowed Pine vocabulary
- a small set of few-shot patterns from the active library
- optional grammar fragment + audience hint

It never sees template prose. Both the single-token and batch prompt
assembly enforce this structurally (see
[`engine/llm_fallback.py`](engine/llm_fallback.py); the
`FallbackRequest.assemble_prompt` / `BatchFallbackRequest.assemble_batch_prompt`
methods are the chokepoints, and tests audit the assembled prompt's
shape).

## Pipeline shape

```
RTF input
  ↓
rtf_extractor.normalize_rtf          (stitch fragmented %[ openers)
  ↓
branch_swap.swap_inverted_branches   (flip if/else for IsEmpty=true)
  ↓
extractor.extract → JDA tokens       (parse %[...] into JDA AST)
  ↓
classify_document_audience           (filename + token frequency)
  ↓
load_verified_for_org(scope=…)       (overlay scoped suggestions)
  ↓
patterns.engine.convert_stream       (apply seed + verified patterns)
  ↓                                   branches:
  ├─ matched     → ConversionSegment (PROV_PATTERN)
  └─ unmatched   → batch LLM call
       ↓
       LlmFallback.convert_batch     (single OpenAI call per template
                                       with optional RAG tool access)
       ↓
       ConversionSegment             (PROV_LLM, Pine tokens or empty)
  ↓
validator.validate_stream            (lint, vocabulary, structural)
  ↓
_reconstruct(rtf, hits, segments)    (rebuild RTF by replacing token
                                       byte ranges with Pine tokens)
  ↓
prelude.generate_prelude             (derive CreateVar declarations
                                       from referenced child entities)
  ↓
prelude.prepend_prelude_to_rtf       (splice prelude into final RTF)
  ↓
Pine RTF output
```

Each segment carries provenance (`pattern` / `llm-fallback` / `unmatched`
/ `edit`) so the GUI can surface where each Pine token came from.

## Directory layout

```
v2/
├── README.md                ← this file
├── pipeline.py              ← convert_template() + rebuild_result_with_edits()
├── parser/                  ← Phase 1 — JDA + Pine + RTF parsing, branch swap
│   ├── README.md
│   ├── jda_ast.py
│   ├── jda_parser.py
│   ├── pine_ast.py
│   ├── pine_parser.py
│   ├── rtf_extractor.py
│   └── branch_swap.py
├── patterns/                ← Phase 2 — pattern format + library + engine
│   ├── README.md
│   ├── schema.py            (Pydantic models)
│   ├── loader.py
│   ├── matcher.py / rewriter.py / engine.py
│   ├── transforms.py        (named transforms: entity translation, format presets, …)
│   ├── holes.py
│   └── library/             (seed patterns, organised by org)
│       ├── common/
│       └── oba/
├── grammar/                 ← Phase 3 — universal Pine grammar + org overrides
│   ├── README.md
│   ├── pine_grammar.toml
│   ├── pine_data_model.toml
│   ├── lint_rules.toml
│   ├── pine_idioms.md
│   ├── role_enum.py / role_index.py
│   ├── loaders.py
│   └── org_overrides/
│       └── oba.toml
├── engine/                  ← Phase 4 — audience, LLM fallback, prelude, validator, store
│   ├── README.md
│   ├── audience.py
│   ├── llm_fallback.py
│   ├── prelude.py
│   ├── suggestion_store.py
│   └── validator.py
├── suggestions/             ← Phase 4 — converter-verified suggestion store on disk
│   └── README.md
└── tools/
    ├── convert.py           (single-template CLI)
    ├── eval_v2.py           (batch eval against the ground-truth corpus)
    ├── corpus_round_trip.py (Phase 1 round-trip diagnostic)
    └── corpus_pipeline.py   (Phase 5 corpus runner with coverage stats)
```

## Read order

If you're picking up this code fresh:

1. This file.
2. [`../CLAUDE.md`](../CLAUDE.md) — orientation across the whole `Agent/` tree.
3. [`../LLM_CAPABILITY_FINDINGS.md`](../LLM_CAPABILITY_FINDINGS.md) — what was tried on the LLM side and what worked. Read before proposing changes to the prompt or the model.
4. `parser/README.md` — the AST shapes and parser strategy.
5. `patterns/README.md` — pattern TOML format + the matcher's match/rewrite model.
6. `grammar/README.md` — Pine grammar split + the org-override model.
7. `engine/README.md` — the LLM fallback, audience, prelude, suggestion-store details.
8. `tests/` — every supported construct has a test.

## How to run

```bash
# From the repo root. The venv at venv/ has pytest + the runtime deps.

# Run the full test suite (~2s, ~400 tests).
./venv/bin/pytest Agent/v2/tests/

# Convert a single template (writes Pine RTF to stdout or --output).
./venv/bin/python -m Agent.v2.tools.convert path/to/legacy.rtf --org oba

# Batch-evaluate against the ground-truth corpus (writes JSON to eval_runs/).
./venv/bin/python -m Agent.v2.tools.eval_v2 --org oba --label some_label
./venv/bin/python -m Agent.v2.tools.eval_v2 --org oba --use-llm --label some_label

# Run the desktop converter (PySide6 GUI — the production HITL surface).
make run:converter   # from repo root
```

The eval tool writes one JSON per run under `Agent/eval_runs/`; the
Streamlit dashboard (`Agent/gui/`) chart those runs over time.

## Org overrides

All deployment-specific knowledge — JDA→Pine entity aliases,
audience-classifier filename regexes (when needed), Pine vocabulary,
pre-declared variable lists, MasterCode mappings — lives in
`grammar/org_overrides/<org>.toml`. Code paths consume this as data;
nothing OBA-specific is hardcoded in Python in the production path
beyond a small number of entity-table entries in
`patterns/transforms.py` (kept for back-compat with the seed pattern
library; superseded over time by the org TOML).

To add a new org, create a new `<org>.toml` modeled on `oba.toml`.
No Python code changes should be required.

## Design ground rules

These come from corpus analysis and direct domain-expert input. Don't
quietly break them:

1. **Patterns are advisory in spirit.** Today, if a pattern matches,
   the LLM isn't called on that token. A "patterns advisory" mode —
   the LLM gets to override a pattern in scope — is deferred work.
2. **Pine data model is universal.** The role enum in
   `grammar/role_enum.py` is system-wide. What varies per org is
   variable naming and whether the deployment pre-declares child
   entities at the variable-screen level.
3. **Org config is the ONE place** for org-specific knowledge.
4. **The prelude generator** at `engine/prelude.py` produces self-
   contained Pine output by deriving the necessary `CreateVar`
   declarations from the entities referenced. Defaults to
   `pre_declared = false` on OBA → always emits, ensures valid Pine
   regardless of destination's variable-screen setup.
5. **The eval intentionally skips broken corpus templates** (9 of 294)
   so the macro F1 reflects measurable quality, not corpus rot.

## Recent direction

Two major arcs since the original phased build-out:

- The **LLM prompt was simplified** (the H1 experiment): the
  OBA-specific 10-rule block and the JDA→Pine entity translation
  table were removed from every LLM call. Lifted macro F1 by 0.020
  and cut unmatched count by 67%. See `../LLM_CAPABILITY_FINDINGS.md`.
- The **HITL flow moved to the converter_app**: scoped suggestion
  persistence (template / audience / global), multi-token mapping
  shapes (1:1, 1:N, N:M, N:0 drop), and inline edit popups anchored
  to bracketed tokens. The Streamlit page at
  `../gui/pages/4_v2_Pipeline.py` is kept for dev/debug; the
  production HITL surface is `Agent/converter_app/`.

## Open work (deferred)

- **Patterns advisory** — let the LLM override a pattern within a
  scope. Workstream #56 in the original plan.
- **Per-org RAG corpus** — `chroma_db_<org>/` lookup so a non-OBA org
  doesn't see OBA-flavoured Pine reference excerpts.
- **Auto org-detection** — currently `--org oba` is the only
  configured target; inference from JDA prefixes is straightforward
  but unfinished.
- **Promote a verified suggestion to a real pattern with holes** —
  today's verified suggestions are exact-match; a "generalise" GUI
  action would let one accept cover many similar future tokens.
- **Negative-shot prompting** — `rejected.log` is audit-only.

## Glossary

| Term | Meaning |
|---|---|
| **Chunk** | An AST pattern that may span multiple tokens (e.g. an If/Else/EndIf trio). v2's unit of mapping. |
| **Hole** | A named placeholder in a pattern (e.g. `$entity`) that captures an arbitrary subtree at match time; the rewrite consumes the captured subtree. |
| **Org context** | Which legal-org's templates we're converting (OBA, criminal/PD, …). Selects which patterns apply and which vocabulary the validator uses. |
| **Vocabulary / allow-list** | The set of `@[...]` names a target org actually has. The validator rejects any generated Pine that references something not in this set. |
| **Verified pair** | A `(legacy.rtf, pine.rtf)` pair where the Pine output has been hand-verified. The 285+ pairs in `ground_truth/evaluation_templates/jda_to_pine/` are the gold standard. |
| **Provenance** | Per-segment tag (`pattern`, `llm-fallback`, `unmatched`, `edit`) telling the GUI where each Pine token came from. |
| **Scope** | A persisted suggestion's reach: `template` (one filename), `audience` (one classifier output), `global` (every template). |

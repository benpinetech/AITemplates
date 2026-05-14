# Project Status

Current state of the JDA → Pine converter. Pair with
[`CLAUDE.md`](./CLAUDE.md) for orientation,
[`LLM_CAPABILITY_FINDINGS.md`](./LLM_CAPABILITY_FINDINGS.md) for the
empirical findings driving the current design, and the per-package
READMEs (`v2/README.md`, `v2/engine/README.md`,
`converter_app/README.md`, `v2/suggestions/README.md`) for module-
level detail.

---

## Update — 2026‑05‑13

Substantive work since the 2026‑05‑11 snapshot:

- **Prompt simplified (H1 strip).** The OBA-specific 10-rule
  translation block and the JDA→Pine entity translation table were
  removed from every LLM prompt. On the 16-template eval this lifted
  macro F1 from 0.526 → 0.546 (+0.020) and reduced unmatched count
  by 67%. The universal role enum + per-input entity hints + vocab +
  few-shot now carry the load. Several side experiments (two-pass
  plan-then-translate, structured JSON output, Presidio PII
  redaction, reasoning models at 4k/32k budgets, smaller chat
  models) were tested and removed when they didn't help — see
  `LLM_CAPABILITY_FINDINGS.md` for the receipts.
- **Verified suggestion store extended.** Now supports scoped
  persistence (template / audience / global), multi-token mapping
  shapes (1:1, 1:N, N:M, N:0 drop), and a priority hierarchy that
  lets scoped overrides win over seed patterns within their scope.
  See `v2/suggestions/README.md`.
- **Desktop GUI moved to Electron + Svelte.** `Agent/converter_app/`
  is now an Electron shell with a Svelte 5 renderer; the PySide6
  implementation was removed entirely. The renderer never touches
  Node — `electron/preload.cjs` exposes a typed `window.api`
  (openRtf / convertRtf / saveRtf), and `electron/main.cjs` spawns
  the v2 pipeline (`python -m Agent.v2.tools.convert ... --json` in
  dev, a PyInstaller sidecar in packaged builds). JSON wire format:
  `jda-pine-convert/v1`, defined in `convert.py::_result_to_json`.
  HITL features carried over from PySide6 (inline edit popup,
  themes, scoped-persistence UI, settings dialog) are not yet ported
  — see `converter_app/README.md` "What's not yet here".
- **Cleanup**: deleted abandoned corpus miner (`engine/miner.py`,
  `tools/mine_patterns.py`, `tests/test_engine_miner.py`); deleted
  the four GUI prototypes (Avalonia, Tauri, Flutter, Electron) after
  Electron was selected for production; removed the org dropdown
  from the toolbar (only one org configured); auto-migrated the
  legacy `gpt-4o-mini` default to `gpt-5.5`; removed Presidio +
  spaCy dependencies (only consumer was the failed PresidioScrubbed
  strategy); deleted ~30 MB of experiment artifact directories.

### Current headline numbers

| config | macro F1 | source |
|---|---|---|
| **Patterns + LLM** (production hybrid) | **~0.67** | full corpus eval, gpt-5.5 |
| Pure LLM, prompt-stripped (no patterns) | ~0.55 | 16-template eval after H1 |
| v1 (LangGraph + GPT-5-mini) | 0.627 | full corpus eval |

The pattern engine still carries ~0.12 F1 that no LLM intervention
has matched. The HITL workflow — converter accepts/edits per segment,
scoped persistence — is the path to higher production F1 over time as
accepted suggestions accumulate.

---

## Headline (2026‑05‑11 snapshot — historical)

| config | macro F1 | precision | recall | duration |
|---|---|---|---|---|
| **Patterns only** (v2, deterministic, recommended for fast iteration) | **0.670** | 0.644 | 0.698 | **9 s** / 294 templates |
| Patterns + LLM (v2, gpt-5.5 + RAG + universal role enum) | 0.662 | 0.632 | 0.696 | ~22 min |
| Baseline at start of 2026‑05‑06 session | 0.19 | 0.19 | 0.19 | — |
| Old v1 (LangGraph + GPT-5-mini, ~3.3 h) | 0.627 | 0.621 | 0.633 | 11,915 s |

**3.5× improvement** on the OBA corpus across the 2026‑05‑06 → 2026‑05‑11 session
(0.19 → 0.67), plus a full architectural rebuild of the LLM path.

## The pipeline (v2)

```
RTF input
  ↓
rtf_extractor.normalize_rtf         (stitch fragmented %[ openers)
  ↓
branch_swap.swap_inverted_branches  (flip if/else for IsEmpty=true conditions)
  ↓
extractor.extract → JDA tokens      (parse %[...] into JDA AST)
  ↓
patterns.engine.convert_stream      (apply OBA pattern library)
  ↓                                  branches:
  ├─ matched   → ConversionSegment (PROV_PATTERN, with Pine tokens)
  └─ unmatched → collected for batch LLM call
       ↓
       LlmFallback.convert_batch    (single OpenAI call per template
                                     with multi-turn RAG tool access)
       ↓
       ConversionSegment            (PROV_LLM, Pine tokens, or empty)
  ↓
validator.validate_stream           (lint the Pine output)
  ↓
_reconstruct(rtf, hits, segments)   (rebuild RTF by replacing token
                                     byte ranges with Pine tokens)
  ↓
prelude.generate_prelude            (derive CreateVar declarations from
                                     referenced child entities)
  ↓
prelude.prepend_prelude_to_rtf      (insert preludes before first \par)
  ↓
Pine RTF output
```

## Key concepts (read before changing anything)

### Pine data model — universal across deployments

Pine has a fixed schema. People-on-a-case live in two tables:

- **CaseInvolvement** — public parties (victims, witnesses, defendants,
  complainants, claimants). Their child records (addresses, phones,
  emails) hang off the **Name** table → child CreateVars filter by
  `NameID`.
- **CaseAssignment** — legal-side actors (attorneys, judges, clerks,
  law enforcement, court staff). These usually have Pine user accounts.
  Their child records hang off the **Personnel** table → child
  CreateVars filter by `PersonnelID`.

Each row has both a **Type code** (specific identifier like `DEPPROS`,
`RESPONDENT`) and a **MasterCode** (a group label like `Prosecution`,
`Law Enforcement`, `Defense`). Pine variables can filter by either:

- `"Type":"DEPPROS"` → exactly one specific row family
- `"MasterCode":"Law Enforcement"` → any row whose type belongs to
  that group

The full enum is in [`v2/grammar/role_enum.py`](./v2/grammar/role_enum.py).
16 involvement codes, 42 assignment codes, source-of-truth for what
the LLM sees in its prompt.

### Root entities vs. child entities

- **Root entities** (CaseInvolvement, CaseAssignment, CaseAgency,
  CaseCharge) have a `CaseID` foreign key. They're reachable in one
  hop from `@[builtin.CaseID]` and can be pre-declared at the
  variable-screen level OR `CreateVar`'d inline.
- **Child entities** (NameAddress, PersonnelAddress, NamePhone,
  NameEmail, PersonnelNumber) have a foreign key to a root entity.
  They need a **two-step lookup** — first get the root, then query
  the child by `NameID` / `PersonnelID`. Pine can't fetch them
  directly from CaseID.

The prelude generator at `v2/engine/prelude.py` exploits this rule:
when the converted output references e.g. `@[ComplainantAddress.first.City]`,
it auto-emits:

```
@[CreateVar(@Complainant, @CaseInvolvement.GetByQuery("CaseID":@[builtin.CaseID],"Type":"CIT01"))]
@[CreateVar(@ComplainantAddress, @NameAddress.GetByQuery("NameID":@[Complainant.first.NameID]))]
```

Parents-before-children, deduplicated.

### Convention A vs. Convention B

The OBA corpus has two valid Pine conventions and **uses both**:

- **Convention A** — inline `CreateVar` preludes at the top, then
  reference local vars. ~37% of ground-truth templates (lettered
  series: `C2`, `C3`, `LOA`, `PR Offer`, …).
- **Convention B** — direct `@[Entity.first.Field]` references,
  relying on the destination Pine's variable-screen to pre-declare
  the variables. ~63% of templates (numbered hearing templates,
  simpler letters).

The agent currently defaults to **Convention A** (`pre_declared = false`
in `oba.toml`) → always emits the prelude → output is self-contained
and works on any deployment. The F1 cost is ~0.012 on the OBA eval
(because the eval ground truth is mixed) but production output is
always-valid Pine.

## What's in each layer

### `src/` (v1, kept for reference)

LangGraph agent: extract → load cache → LLM mapping with RAG tool
calls (up to 10 turns) → mapping finalize → save cache → replace
fillpoints. Uses GPT-5-mini, runs ~3.3 hours for 294 templates.
**Don't ship this; use v2.**

### `v2/parser/`

- `rtf_extractor.py` — finds and parses `%[...]` and `@[...]` tokens
  in RTF, handling fragmented openers (`%}{...\n[`).
- `branch_swap.py` — pre-pass that swaps if/else bodies for
  `If(X.<NameField>.IsEmpty[=true])` conditions, since Pine's
  `Any()==true` equivalent flips polarity.
- `jda_parser.py` / `pine_parser.py` — hand-rolled recursive-descent
  parsers producing AST.

### `v2/patterns/`

- `engine.py` / `matcher.py` / `rewriter.py` — pattern engine that
  walks the JDA AST against TOML pattern definitions.
- `transforms.py` — value-transforms patterns can call
  (`translate_jda_entity_to_pine`, `format_to_preset`, etc.).
  Currently consults the org config first, falls back to legacy
  hardcoded tables.
- `library/common/` and `library/oba/` — TOML pattern files. The OBA
  library has 13 files covering name forms, address forms, IsEmpty
  conditions, gender-pronoun blocks, subdocument expansion, prompt
  variables, drops, etc.

### `v2/engine/`

- `llm_fallback.py` — `OpenAILlmClient` with tool-call iteration,
  `FallbackRequest` / `BatchFallbackRequest` with rich prompts
  (translation rules + universal role enum + entity table + vocab
  + few-shots + document-audience hint).
- `prelude.py` — derives CreateVar declarations from referenced child
  entities; topologically sorted; pre-declared filter from org config.
- `validator.py` — lint rules over Pine output.
- `suggestion_store.py` — verified-suggestion cache (auto-accept).
  See "Cache hazard" below.

### `v2/grammar/`

- `role_enum.py` — universal Pine role enum (16 involvement, 42
  assignment types, with MasterCode groupings). Add a new row here
  when Pine adds a new type code.
- `role_index.py` — builds runtime lookup tables from an OrgRoot.
- `loaders.py` — pydantic models for the grammar TOMLs.
- `org_overrides/oba.toml` — OBA's org config: role mappings, child
  entities, conventions, pre-bound collections, vocabulary, prompt
  variables. Source of truth for OBA-specific labels.
- `pine_grammar.toml`, `pine_data_model.toml`, `lint_rules.toml` —
  universal Pine grammar.

### `v2/tools/`

- `eval_v2.py` — batch eval, writes JSON to `eval_runs/`. Loads
  `.env` for `OPENAI_API_KEY`. Auto-skips broken templates by default.
- `convert.py` — single-file conversion CLI. `--json` mode emits the
  `jda-pine-convert/v1` bundle the Electron converter app consumes.

### `gui/`

Streamlit multi-page app:
- `1_API_Tester.py` — v1 FastAPI client
- `2_Eval_Dashboard.py` — main eval dashboard, runs batch evals,
  shows macro-F1 trend chart, per-run breakdown, skipped-broken
  panel
- `3_Template_Runner.py` — v1 single-template runner
- `4_v2_Pipeline.py` — v2 single-template runner with per-segment
  provenance and accept/reject UI

## Cache hazard — don't auto-accept LLM suggestions in production

The "auto-accept" feature (writes LLM responses to
`v2/suggestions/verified/<org>/` as new patterns) **degrades F1**
because the cached suggestions are context-blind:

> Cached suggestions hurt 181 templates and helped only 7.

The LLM might correctly translate `Cust_Address.City → ComplainantAddress.first.City`
in one template (where Complainant is the addressee) and wrongly
apply it everywhere. The cache stores one mapping per JDA token, so
all subsequent templates with the same JDA token reuse the wrong
translation.

The dashboard's auto-accept checkbox is labeled "DANGEROUS for
production" and defaults off. Clear the cache via
`find Agent/v2/suggestions/verified -name "*.toml" -delete`.

## Templates the eval can't measure well

Three buckets of low-scoring templates (analysis in conversation
logs):

1. **9 broken corpus templates** (e.g. `C2 - Revised.rtf` — legacy
   has 0 JDA tokens due to RTF mangling). Auto-skipped by eval as
   of W11; they're listed in each run JSON under `skipped_broken`.
2. **~88 templates where the Pine ground truth is a substantial
   expansion of the JDA source** — human translator added CreateVar
   blocks, ForEach loops, conditional branches that don't exist in
   JDA. Not reproducible from the JDA alone.
3. **~5 templates with template-specific entity choices** (e.g.
   FilingComplainant vs Complainant for the same JDA token in
   different documents) — context-dependent in ways the LLM can't
   currently infer.

Excluding (1) lifts macro F1 by ~0.014. Buckets (2) and (3) are
inherent to the corpus / metric, not fixable without changing the
ground truth or the scoring.

## Generalization story for new orgs

The architecture is generalizable; the *content* is partially
OBA-flavored. To add a new org effectively:

| layer | what's needed | effort |
|---|---|---|
| Role labels and aliases | new `v2/grammar/org_overrides/<org>.toml` | half a day with the right data |
| RAG corpus | per-org Pine reference doc indexed into a per-org `chroma_db_<org>/` | depends on availability of docs |
| Drop list / prompt variables / date presets | currently in code as OBA-leaning defaults | half a day to move to config |
| High-frequency patterns | OBA has 13 hand-written pattern files; LLM fallback substitutes for new orgs | optional, incremental |

The universal role enum in the LLM prompt (added W14/W15) means a
new org gets **competent default translations** out of the box even
without per-org patterns — the LLM does semantic match against the
enum. Estimated F1 for a fresh org with only the org config: 0.50–0.60.
With per-org RAG: 0.60–0.70. With patterns: parity with OBA.

## Workstream log (this session, 2026-05-06 / 2026-05-11)

| label | what | F1 impact |
|---|---|---|
| morning | bare-form name patterns, branch swap, FullName splitting, address-field patterns | 0.19 → 0.63 |
| W1 | drop-list patterns (StateIDNum, OBAAttorney.Title, pronouns) | +0.04 |
| W2 | entity-table expansion (KF_* variants, AgencyNum→ProsNum) | included in W3 |
| W3 | prompt-variable patterns (PRCMeeting, DateOfLetter, etc.) | included |
| W4 | corpus mining — *abandoned, miner alignment too noisy* | n/a |
| W5 | conservative LLM prompt + DOCUMENT CONTEXT entity counts | included |
| W6 | long-tail entity patterns (Builtin.CaseID, JW_DocumentEvents) | 0.643 → 0.670 |
| W7 | document-classifier from filename + token frequency | flat — already captured by W5 |
| W8 | **gpt-5.5 + RAG tool + multi-turn agent loop** (ported from v1) | 0.663 → 0.674 (LLM finally net-positive) |
| W9 | relax conservative bias for unknown entities | -0.003 — reverted in W10 |
| W10 | CreateVar prelude generator + integration | flat on F1 (CV-excluded metric), but production-correct output |
| W11 | flip eval to **include CreateVars** in token comparison | metric now honest; reveals -0.014 from over-emitted preludes |
| W12 | consolidate org-specific labels into `oba.toml` | architecture win; F1 flat |
| W13 | flip `pre_declared = false` for OBA → emit prelude always | matches W11 behavior |
| W14 | universal role enum (16 involvement + 42 assignment codes) added to LLM prompt | helped long-tail templates (Truancy, Process Card, DV Contract); offset by prelude cost |
| W15 | MasterCode-aware framing for the role enum | F1 essentially flat at 0.662; architecture more correct |

## Deferred / open

- **Workstream #56 — patterns advisory rather than authoritative.**
  Today if a pattern matches, the LLM is not consulted on that token.
  Letting the LLM also evaluate pattern-matched tokens (and pick the
  better output) could help on templates where the pattern's default
  disagrees with the corpus's stylistic choice (e.g. SetCasing on
  City). Not started.
- **Per-org RAG support.** The current RAG database is OBA-flavored
  (`pine_syntax_ground_truth.txt`). For another org, the LLM gets
  misleading examples. Fix: `_get_rag_db()` in `llm_fallback.py`
  could look for `chroma_db_<org>/` and fall back to default.
- **Move drop list / prompt variables / date presets to org config.**
  Currently hardcoded in `transforms.py`. Generalization gap for new
  orgs.
- **Second org config as proof-point.** Even a stub `criminal_defense.toml`
  would validate the consolidation work — no eval needed.

## Working agreements with the user

These are stable conventions surfaced during the session:

- Run patterns-only eval (no `--use-llm`) for fast iteration.
- LLM-enabled run is the deliverable for actual conversions.
- Clear the verified-suggestion cache before each "honest" eval so
  context-blind cache entries don't poison the run.
- Ground truth files are read-only — never write to
  `ground_truth/evaluation_templates/`.
- Don't push, commit, or rename branches without explicit OK.
- For ambiguous "yes please" responses to multi-option questions,
  default to the first option mentioned or the most likely-intended
  one; ask if both interpretations are equally plausible.

## File map for fast orientation

```
Agent/
├── CLAUDE.md                      ← start here
├── PROJECT_STATUS.md              ← this file
├── PLAN.md                        ← earlier session's workstream plan (largely done)
├── PIPELINE_CRITIQUE.md           ← architectural critique from earlier
├── src/                           ← v1 (deprecated, kept for reference)
├── v2/                            ← production pipeline
│   ├── parser/                    ← RTF/JDA/Pine parsing + branch swap
│   ├── patterns/                  ← pattern engine + OBA library + transforms
│   ├── engine/                    ← LLM fallback, prelude, validator, suggestion store
│   ├── grammar/                   ← role enum, org configs, lint rules
│   ├── tools/                     ← eval_v2, convert
│   └── tests/                     ← ~400 tests, fast (~2s)
├── converter_app/                 ← Electron + Svelte production GUI
├── gui/                           ← Streamlit dashboard (dev tooling only)
├── ground_truth/                  ← read-only eval corpus
├── eval_runs/                     ← every run as JSON, dashboard reads from here
├── chroma_db/                     ← OBA Pine reference vector DB (used by RAG tool)
├── template_output/               ← v1 single-template outputs
└── evaluation_output/             ← v1 batch eval outputs
```

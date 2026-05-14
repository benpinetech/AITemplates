# JDA → Pine Template Converter — Project Guide

This codebase converts legacy **JDA template syntax** (`%[...]`) to
**Pine template syntax** (`@[...]`) for migrating case-management
document templates from the JDA platform to Pine CMS. The active org
is the **Oklahoma Bar Association (OBA)** — disciplinary-case
templates — but the architecture is designed to generalize to other
deployments (criminal-defense, public-defender, etc.).

## Where to start

| if you want to… | go to |
|---|---|
| understand the current state of the project | [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) |
| read the prompt / model / strategy findings | [`LLM_CAPABILITY_FINDINGS.md`](./LLM_CAPABILITY_FINDINGS.md) |
| run the desktop converter (production HITL surface) | `make run:converter` from the repo root |
| run the eval, dashboard, or convert a single file | "Running things" below |
| see the v1 vs v2 split and which one ships | "Two pipelines" below |
| understand Pine's data model (roles, MasterCode, CreateVar) | [`v2/grammar/role_enum.py`](./v2/grammar/role_enum.py) + `PROJECT_STATUS.md` |
| add a new org | [`v2/grammar/org_overrides/oba.toml`](./v2/grammar/org_overrides/oba.toml) as the template |

## Two pipelines

The repo has **v1** (the original LangGraph + GPT-5-mini agent in
`src/`) and **v2** (the chunk-pattern engine + LLM fallback in `v2/`).
**v2 is the production target.** v1 remains for comparison / regression.

```
src/                        v1 — LangGraph agent + RAG, F1~0.63, slow (~3h)
v2/                         v2 production pipeline
  parser/                   JDA + Pine AST parsers, RTF extractor, branch swap
  patterns/                 pattern engine, OBA pattern library, transforms
  engine/                   audience, LlmFallback, prelude, validator, suggestion_store
  grammar/                  org configs, lint rules, role enum, vocabulary
  suggestions/              converter-verified suggestion store on disk (HITL closed loop)
  tools/                    eval_v2.py, convert.py, corpus_pipeline.py, corpus_round_trip.py
  tests/                    ~400 tests, all passing
converter_app/              Electron + Svelte desktop GUI — production HITL surface
  electron/                 main.cjs (BrowserWindow + IPC + Python spawn), preload.cjs
  src/                      App.svelte (the whole UI), main.js (mount)
gui/                        Streamlit dashboard (dev tooling only)
ground_truth/               eval corpus (legacy/pine paired RTFs)
eval_runs/                  every eval run as a JSON file
```

The **production HITL surface is `converter_app/`** (Electron + Svelte
desktop). The Streamlit `gui/` app is dev tooling: API tester, eval
dashboard, debugging the v2 pipeline page. User-facing converter
features (editing, scoped persistence, themes) belong in
`converter_app/`, not in `gui/`.

## Running things

All commands run from `/home/Chase/Repos/AITemplates/`.

```bash
# Desktop converter (production HITL surface).
make run:converter

# v2 batch eval (default org=oba, patterns-only = fast deterministic ~9s)
./venv/bin/python -m Agent.v2.tools.eval_v2 --org oba --label some_label

# v2 batch eval with LLM (~22 min for the full OBA corpus)
./venv/bin/python -m Agent.v2.tools.eval_v2 --org oba --use-llm --label some_label

# Single-template conversion (writes Pine RTF to disk)
./venv/bin/python -m Agent.v2.tools.convert path/to/file.rtf --org oba -o /tmp/out.rtf

# Streamlit dashboard (dev tooling — eval charts, API tester)
./venv/bin/python -m streamlit run Agent/gui/Home.py

# Full test suite (~2s, ~400 tests)
./venv/bin/pytest Agent/v2/tests -q
```

LLM keys are loaded from the repo-root `.env` (`OPENAI_API_KEY`). The
Electron main process propagates `process.env` to the child Python
pipeline, so the same `.env` works for the desktop app in dev. A
keychain-backed Settings dialog for the packaged installer is on the
TODO list.

## Architectural ground rules

These come from corpus analysis, direct domain-expert input, and
empirical findings (see `LLM_CAPABILITY_FINDINGS.md`):

1. **Patterns are advisory in spirit.** Today, if a pattern matches,
   the LLM is not called on that token. Workstream #56 (deferred)
   would change that.
2. **The Pine data model is universal across deployments.** The role
   enum in `v2/grammar/role_enum.py` is system-wide. What varies per
   org is variable naming and whether the variable screen pre-declares
   child entities.
3. **Org config (`v2/grammar/org_overrides/<org>.toml`) is the ONE
   place** to encode org-specific knowledge. Adding a new org should
   be a config-only change. Any new `if org == "..."` branch in
   Python is a step backwards.
4. **The CreateVar prelude generator** at `v2/engine/prelude.py`
   produces self-contained Pine output by deriving the necessary
   variable declarations from the entities referenced. Defaults to
   `pre_declared = false` on OBA → always emits, ensures valid Pine
   regardless of destination's variable-screen setup.
5. **The eval intentionally skips broken corpus templates** (9 of 294)
   so the macro F1 reflects measurable quality, not corpus rot.
6. **Prompt simplicity over rule accretion.** The OBA-specific
   translation-rules block + entity translation table were removed
   after the H1 experiment showed they hurt F1. Re-adding rules
   should clear a high bar — see the findings doc before proposing
   new ones.

## What's the best F1 today?

| config | F1 | duration |
|---|---|---|
| Patterns-only | ~0.67 | 9s |
| Patterns + LLM (gpt-5.5, prompt-stripped) | ~0.67 | ~22min |
| Pure LLM (no patterns, gpt-5.5, prompt-stripped) | ~0.55 | ~22min |
| v1 (original LangGraph agent) | 0.627 | ~3.3 hours |

The pattern engine carries ~0.12 F1 that no LLM intervention has been
able to match. The 16-template prompt-stripped eval came out at
macro F1 = 0.546 pure-LLM. The HITL flow (converter accepts/edits
per segment, scoped persistence) is the path to higher production
quality over time as accepted suggestions accumulate.

## Conventions

- **Tests pass.** ~400 tests; the suite must stay green before any
  change is considered done.
- **Don't write to ground truth.** `ground_truth/evaluation_templates/`
  is read-only across the codebase.
- **Default suggestion scope = narrowest available.** The inline edit
  popup defaults to `template` if a filename is set, else `audience`
  if classified, else `global`. Global is the documented context-
  blind cache hazard — use sparingly.
- **Use the org config, not code, to add labels and aliases.**
- **The user-facing converter is `converter_app/`, not `gui/`.**
  Streamlit is for dev/debug. Adding HITL features there is a wrong
  target.

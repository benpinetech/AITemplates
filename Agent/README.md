# Agent — JDA → Pine template converter

Converts legacy **JDA** legal-document templates (`%[...]` syntax) to
**Pine** templates (`@[...]`). Active org is the Oklahoma Bar
Association (OBA) — disciplinary-case templates — with an architecture
designed to generalize to other deployments.

## Where to start

| if you want to… | go to |
|---|---|
| read the project guide | [`CLAUDE.md`](./CLAUDE.md) |
| see the current state + recent changes | [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) |
| understand the empirical findings driving the design | [`LLM_CAPABILITY_FINDINGS.md`](./LLM_CAPABILITY_FINDINGS.md) |
| run the desktop converter | `make run:converter` from the repo root |
| browse the pipeline code | [`v2/README.md`](./v2/README.md) |
| build the desktop GUI | [`converter_app/README.md`](./converter_app/README.md) |
| add a new org | [`v2/grammar/org_overrides/oba.toml`](./v2/grammar/org_overrides/oba.toml) as the template |

## Setup

```bash
# From the repo root.
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/pip install -r Agent/v2/requirements.txt   # adds pytest etc.
```

Put your OpenAI key in `.env` at the repo root for the eval CLI:

```bash
OPENAI_API_KEY="sk-..."
```

For dev mode, the Electron main process propagates `process.env` to
the child Python pipeline, so the same `.env` works. (A future
in-app Settings dialog will store the key in the OS keychain — not
yet implemented.)

## Quick commands

```bash
# Desktop converter — production HITL surface.
make run:converter

# Batch eval on the OBA corpus (patterns-only, ~9s).
./venv/bin/python -m Agent.v2.tools.eval_v2 --org oba --label some_label

# Batch eval with LLM (~22 min).
./venv/bin/python -m Agent.v2.tools.eval_v2 --org oba --use-llm --label some_label

# Single template.
./venv/bin/python -m Agent.v2.tools.convert path/to/legacy.rtf --org oba -o /tmp/out.rtf

# Test suite (~2s, ~400 tests).
./venv/bin/pytest Agent/v2/tests -q

# Streamlit dev dashboard (eval charts, API tester).
./venv/bin/python -m streamlit run Agent/gui/Home.py
```

## Production pipeline

The production code is **v2** at [`v2/`](./v2/). v1 in
[`src/`](./src/) is kept for reference / regression only — it's the
original LangGraph + RAG + GPT-5-mini agent (~F1=0.63, ~3 hours on
the corpus).

v2 in one line: deterministic chunk-pattern engine + batched LLM
fallback + per-converter scoped suggestion overlay, all driven from
an Electron + Svelte desktop app.

```
v2/
├── parser/       — JDA + Pine + RTF parsers, branch swap
├── patterns/     — pattern format, library, matcher, rewriter
├── grammar/      — universal Pine grammar + org overrides
├── engine/       — audience classifier, LLM fallback, prelude, validator, suggestion store
├── suggestions/  — converter-verified suggestion store on disk
├── tools/        — eval_v2.py, convert.py, corpus_pipeline.py
└── pipeline.py   — top-level convert_template() + rebuild_result_with_edits()
```

The desktop app (`converter_app/`) is what the converter actually
uses day to day. The Streamlit `gui/` app is dev tooling (API tester,
eval dashboard, debugging the v2 pipeline page) — user-facing
converter features belong in `converter_app/`, not in `gui/`.

## Headline F1

| config | macro F1 |
|---|---|
| Patterns + LLM (production hybrid) | ~0.67 |
| Pure LLM, prompt-stripped (no patterns) | ~0.55 |
| v1 (LangGraph + GPT-5-mini) | 0.63 |

The pattern engine carries ~0.12 F1 that no LLM intervention has been
able to match. The HITL workflow — converter accepts/edits per
segment, scoped persistence — is the path to higher production
quality over time as accepted suggestions accumulate.
[`LLM_CAPABILITY_FINDINGS.md`](./LLM_CAPABILITY_FINDINGS.md) has the
full experiment record.

## Privacy posture

The LLM only sees bracketed JDA / Pine syntax, the universal Pine
role enum, the org's vocabulary allow-list, a small set of few-shot
patterns, and (optionally) a grammar fragment. **Never template
prose.** The prompt-assembly methods in
[`v2/engine/llm_fallback.py`](./v2/engine/llm_fallback.py) are the
structural chokepoints, with tests that audit the prompt's shape.

In dev, the OpenAI key comes from the repo-root `.env`, which the
Electron main process passes through to the spawned Python pipeline.
A keychain-backed Settings dialog is on the TODO list for the
packaged installer — see `converter_app/README.md` "What's not yet
here".

## Adding a new org

Adding a new org should be a config-only change:

1. Copy `v2/grammar/org_overrides/oba.toml` → `v2/grammar/org_overrides/<org>.toml`.
2. Fill in the JDA→Pine entity aliases, vocabulary, audience-classifier
   inputs, drop list, and pre-declared variable list for the new org.
3. (Optional) Add seed patterns under `v2/patterns/library/<org>/`. Not
   required — the HITL flow lets the converter grow this content over
   time.

No Python code changes should be necessary. If you find yourself
adding an `if org == "..."` branch, that's a step backwards — push the
behavior into the org TOML instead.

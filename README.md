# JDA → Pine Template Converter

Converts legacy JDA legal-document templates (`%[...]` syntax) to Pine templates (`@[...]`). Built for the Oklahoma Bar Association's disciplinary-case templates, but designed to work with any org.

## Getting started

You need Python 3.11+ and Node.js. That's it.

```bash
git clone <repo>
cd AITemplates
make run
```

`make run` handles everything on first launch — creates the Python virtual environment, installs dependencies, and opens the converter app. On subsequent runs it just opens the app.

### OpenAI key (optional)

The converter works without a key using the pattern engine alone. To enable the LLM fallback, add your key to a `.env` file at the repo root:

```
OPENAI_API_KEY=sk-...
```

The app picks it up automatically — no restart needed.

## What it does

The converter opens a two-pane desktop app. Load a legacy JDA template on the left, hit Convert, and the Pine output appears on the right. You can click any converted token to edit it, and corrections are remembered for future conversions.

Under the hood it's a deterministic pattern engine (fast, ~9s for a full corpus run) with an optional LLM fallback for tokens the patterns don't cover (~22 min with LLM). The pattern engine alone gets F1 ≈ 0.67 — adding the LLM doesn't move the needle much, so patterns-only is the default.

## Other commands

```bash
# Run the converter (after first setup)
make run:converter

# Eval dashboard (Streamlit — for development)
make run:dashboard

# Run the test suite
make test

# Single template conversion (CLI)
./venv/bin/python -m pipeline.tools.convert path/to/legacy.rtf --org oba -o /tmp/out.rtf

# Batch eval against the ground truth corpus
./venv/bin/python -m pipeline.tools.eval_v2 --org oba --label my_run
./venv/bin/python -m pipeline.tools.eval_v2 --org oba --use-llm --label my_run_llm
```

## Repo layout

```
pipeline/         core conversion engine (pattern matcher, LLM fallback, RTF parser)
converter_app/    Electron + Svelte desktop app
dashboard/        Streamlit eval dashboard (dev tooling)
ground_truth/     paired legacy/pine RTF corpus used for eval
```

## Adding a new org

Should be a config-only change — no Python required:

1. Copy `pipeline/grammar/org_overrides/oba.toml` → `pipeline/grammar/org_overrides/<org>.toml`
2. Fill in the JDA→Pine entity aliases, vocabulary, and audience classifier inputs
3. Optionally seed patterns under `pipeline/patterns/library/<org>/`

If you find yourself adding an `if org == "..."` branch in Python, push it into the TOML instead.

## Further reading

- [`CLAUDE.md`](./CLAUDE.md) — architecture decisions and ground rules
- [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) — current state and recent changes
- [`LLM_CAPABILITY_FINDINGS.md`](./LLM_CAPABILITY_FINDINGS.md) — what we tried and what the numbers showed

# LLM Capability Experiments — Findings

Last updated: 2026-05-12. Covers the LLM-first investigation arc that
ran across this session: privacy strategies, prompt-rule changes, the
entity-table extension, and the four-model OpenAI sweep including a
reasoning-model budget bump.

## The question that drove this

"Can the LLM do more of the conversion work — and if so, what does
that take?"

Going in, the production system was a hybrid (deterministic patterns +
LLM fallback) scoring **macro F1 = 0.67** on the OBA evaluation corpus.
Patterns-only alone scored **0.67**, and patterns + LLM scored **0.66**
— meaning the LLM was a small net negative against the metric. Three
hypotheses to test:

1. The LLM is starved of context (no prose allowed under the privacy
   rule). More context → better F1.
2. The conservative-bias prompt rule is forcing the LLM to bail too
   often. Loosen it → recall up.
3. A stronger or more reasoning-capable model would close the gap
   against patterns.

## Experimental setup

All experiments isolate the LLM by running with `--no-patterns`, so
the pipeline forwards every JDA token to the LLM with no deterministic
help. This is the cleanest measure of LLM capability.

- **Corpus**: 16 templates sampled from the OBA evaluation set,
  spanning 1–23 tokens, mixed audience signals (C/R/none), mixed
  conditional usage, mixed legacy prefix conventions (`JW_`, `Cust_`,
  `KF_`).
- **Metric**: token-level macro F1 against the human-translated Pine
  ground truth, computed via the v1 eval's `_sequence_f1`.
- **Default model**: `gpt-5.5` (chat completion, batch input format)
  unless otherwise noted.
- **Tool**: `Agent/v2/tools/compare_privacy.py` — runs the pipeline
  per template per strategy, capturing prompt, raw response, converted
  RTF, and F1.

## Headline result

**Pure-LLM macro F1 against the corpus tops out at ~0.53.** Every
intervention tried left the LLM-only number in the 0.42–0.53 range.
The deterministic pattern engine carries about 0.14 F1 worth of value
that the LLM at any tested configuration cannot match on this metric.

| configuration | macro F1 | notes |
|---|---:|---|
| `gpt-5.5` + ast_only context + Phase A code | 0.533 | baseline |
| `gpt-5.5` + ast_only + Phase C code (aggressive rule, entity entries) | 0.523 | recall up, precision down |
| `gpt-5.5` + structural context | 0.522 | structural context ≈ baseline |
| `gpt-5.5` + Presidio-scrubbed prose | 0.517 | placeholders confused the model |
| `gpt-5.5` + full prose (privacy violation) | 0.522 | full prose ≈ AST-only |
| `o3` reasoning model, 4k budget | 0.446 | empties on long templates |
| `o3` reasoning model, 32k budget | 0.422 | worse than 4k |
| `gpt-5.4-mini` | 0.438 | competitive at 8× speed |
| `gpt-5.4-nano` | 0.154 | collapses |
| **Hybrid (patterns + LLM, production)** | **~0.67** | for comparison |

## Experiment 1 — Privacy strategies

Four context-building strategies tested, ranging from "send nothing
but the AST" to "send the entire template prose":

| strategy | what the LLM sees | privacy-safe? |
|---|---|---|
| `ast_only` | just the numbered list of JDA tokens | yes |
| `structural` | + token positions, neighbor tokens, paragraph index | yes |
| `presidio_scrubbed` | + ± 300 chars surrounding prose with PII reversibly redacted via Microsoft Presidio | arguable |
| `full_prose` | + the whole template's prose, RTF stripped, brackets preserved | no |

### What I expected

More context = better disambiguation = better F1. The full-prose
strategy should win; structural and Presidio should clearly beat
ast_only.

### What actually happened

Across 16 templates, all four strategies clustered within ~0.016 F1 of
each other:

- ast_only: 0.533 (best on macro mean)
- structural: 0.522
- full_prose: 0.522
- presidio_scrubbed: 0.517 (worst)

Drilling into specific templates:

- **`215 - Letterhead.rtf`** — ast_only emitted `@[PromptName]`,
  `@[PromptAddress]` etc. for the else-branch (correct: prompt
  variables), while `full_prose` returned `<no mapping found>` for the
  same slots. The surrounding prose biased the LLM into not
  recognizing the X.X prompt-variable pattern. F1: 0.43 (ast_only) vs
  0.25 (full_prose).
- **`Affidavit of Witness.rtf`** — Presidio's PII placeholders
  (`<PERSON_1>`, `<LOCATION_2>`) triggered the LLM's conservative
  bias, causing it to bail on tokens the other strategies translated.
  F1: 0.40 (ast_only) vs 0.18 (presidio).
- **`12C.rtf` slot 40 — `%[DateOfLetter.DateOfLetter]`**:
  - ast_only: `@[DateOfLetter]` — correct (prompt variable per rule 9)
  - structural: `@[DateOfLetter.FormatDate(preset1)]` — added a wrong wrapper
  - full_prose: `@[DateLetterReceived.FormatDate(preset1)]` — wrong entity entirely

The model interprets prose as a license to be more creative or more
conservative depending on what the prose looks like. Both directions
cost F1.

### Why this is happening

The batch input format already provides implicit cross-token context
— the LLM sees all the unmatched tokens as a numbered list, which
lets it cross-reference siblings. Adding prose on top doesn't
disambiguate; it distracts.

Presidio specifically hurts because the placeholders look like
in-band syntax to the model. It treats `<PERSON_1>` as suspicious
unknown content and gets more conservative everywhere.

## Experiment 2 — Aggressive translation rule + entity table additions

Two changes to test whether the prompt was biasing the LLM toward
under-translation:

1. **Rule 7 rewrite**: changed the prompt's conservative-bias rule
   ("when in doubt, prefer `<no mapping found>`") to an aggressive
   rule ("emit a translation when there's a clear semantic match; a
   human reviewer catches mistakes").
2. **Entity table additions**: added `KF_PhoneNumberActive →
   ProsecutorPhone` and `KF_Atty_Pros_Active_AgencyNum → ProsNum`
   based on ground-truth observations. (Also tried `Cust_CIPs →
   Involvement` and rolled it back — see below.)

### Result

Phase C macro F1 (16 templates × 4 strategies × `--no-patterns`):

| strategy | Phase A | Phase C | Δ | unmatched A → C |
|---|---|---|---|---|
| ast_only | 0.533 | 0.523 | -0.010 | 23 → 16 (-30%) |
| structural | 0.522 | 0.527 | +0.005 | 31 → 23 (-26%) |
| presidio_scrubbed | 0.517 | 0.531 | +0.014 | 27 → 23 (-15%) |
| full_prose | 0.522 | 0.526 | +0.004 | 30 → 16 (-47%) |

**Recall went up across the board. F1 did not.** The aggressive rule
shifted the LLM from "bailing too often" to "guessing more often,
with wrong guesses about as often as right ones." Net F1 ≈ zero.

### The Cust_CIPs lesson (one entry rolled back)

Adding `Cust_CIPs → Involvement` was over-eager. In the source
template, `%[Cust_CIPs.FullName]` translates to:
- `@[Involvement.FormatName(...)]` inside `If(Cust_CIPs.[criterion])`
- `@[PromptName]` inside the `Else` branch (prompt variable)

The mapping forced both branches into the same Pine output, losing
the context-sensitive behavior. F1 on `215 - Letterhead.rtf` crashed
from 0.43 to 0.20 across strategies. The entry was removed.

**Lesson**: entity-table additions can shadow legitimate
context-dependent behavior. Each addition needs to be validated
against the templates where the entity appears in multiple
branches/contexts.

## Experiment 3 — Model spread

Four OpenAI models on the same 16 templates with ast_only strategy
only and patterns off (16 LLM calls × 4 models = 64 calls).

| model | macro F1 | unmatched | wall-clock | notes |
|---|---:|---:|---:|---|
| `gpt-5.5` | 0.528 | 28 | 385s | best chat model available |
| `o3` (4k completion budget) | 0.446 | 63 | 537s | empties on 3 templates |
| `gpt-5.4-mini` | 0.438 | 66 | 49s | 8× faster than flagship |
| `gpt-5.4-nano` | 0.154 | 87 | 37s | collapses |

`gpt-5.5-pro` was eliminated — it lives behind a non-chat endpoint
and isn't a drop-in. The flagship `gpt-5.5` is the strongest OpenAI
chat model accessible.

### What this tells us

- **The model dimension is tapped.** `gpt-5.5` is the ceiling under
  current OpenAI access. Nothing else available beats it.
- **`gpt-5.4-mini` is competitive at 8× speed.** Could replace flagship
  in cost-sensitive deployments at a 0.09 F1 cost.
- **`nano` is not viable.** Drops below random for several templates.

## Experiment 4 — Reasoning-model budget bump

`o3`'s 4k-budget run returned empty output for 3 templates because the
model's hidden reasoning tokens consumed the entire completion budget
before any visible output was generated. Hypothesis: bumping the
budget to 32k would let reasoning + output both fit, unlocking the
model's value.

`max_completion_tokens` was changed from 4096 to 32768 for
reasoning-model names (`^o\d`).

| run | macro F1 | unmatched | wall-clock | empty templates |
|---|---:|---:|---:|---:|
| o3 (4k) | 0.446 | 63 | 537s | 3 |
| **o3 (32k)** | **0.422** | 55 | 762s | **4** |
| gpt-5.5 (baseline) | 0.528 | 28 | 385s | 1 |

### What happened

The 32k budget made o3 **worse**, not better. Specifically:

- **Some templates fixed**: `215 - Letterhead.rtf` went from 0.00 →
  0.24 (the model no longer ran out of budget mid-template).
- **Some templates regressed**: `Affidavit of Witness.rtf` went from
  0.40 → 0.00 — the model that succeeded at 4k now reasons itself
  into producing nothing.
- **Some templates still empty even at 32k**: `LOA - Simple.rtf` spent
  108 seconds reasoning and returned 0 characters. The model never
  decided its answer was good enough to commit.
- **Total wall-clock jumped 42%** (537s → 762s) without F1 benefit.

### Diagnosis

Reasoning models are designed for problems where chain-of-thought
helps — math, multi-step planning, code with non-obvious solutions.
JDA-to-Pine token translation is a **lookup-with-disambiguation**
task, not a reasoning task. Each token has a small set of plausible
Pine targets; the choice depends on local context, not deep inference.

When given more rope, o3 doesn't reason better. It overthinks. The
budget bump didn't unlock latent capability; it gave the model more
room to second-guess.

The 32k change is currently still in place at
`Agent/v2/engine/llm_fallback.py`. It only fires on reasoning-model
names (`^o\d` regex), so it does not affect production `gpt-5.5`
runs. Worth rolling back or keeping as a future-experiment knob — not
load-bearing for any current code path.

## What we learned overall

1. **Privacy strategy is a near-wash for the LLM.** Going from
   AST-only to full-prose moves macro F1 by ~0.016 in either
   direction across 16 templates. The "no prose to LLM" privacy
   constraint, if relaxed, would not unlock significant F1.
   ↳ The conservative privacy posture costs essentially nothing
     against this metric.

2. **The implicit context already in the batch input format is doing
   more work than expected.** Putting all unmatched tokens in one
   numbered list lets the LLM cross-reference siblings, which
   substitutes for most of what prose context would provide.

3. **Presidio (reversible PII redaction) actively hurts.** The
   placeholders look like in-band syntax to the model and trigger its
   conservative bias. Don't ship Presidio for this use case.

4. **The conservative-bias rule trades recall for precision 1:1.**
   Flipping it to aggressive translation reduced unmatched output by
   30–47% but the new guesses were wrong as often as right. Net F1
   moved by less than 0.01. The bias rule is a knob for what shape of
   error you'd rather have, not a quality lever.

5. **The OpenAI model dimension is exhausted under the current API
   key.** `gpt-5.5` is the best available, `o3` doesn't help even
   with budget tuning, smaller models drop F1 without offsetting
   gains. Different provider (e.g. Claude) is the only model-side
   lever still in play, and it requires a non-OpenAI API key.

6. **The pattern engine adds ~0.14 F1** over pure-LLM (0.53 vs 0.67).
   That's not noise; it's deterministic precision the LLM cannot
   match on the LCS-token metric. The patterns are doing real work.

7. **The LCS-token F1 metric undersells the LLM.** Many of the LLM's
   "wrong" outputs are *plausible candidates* a human converter would
   accept-then-edit. The unmatched count dropping from 30 to 16 (Phase
   A → C) means 14 fewer blanks the converter has to fill in — that's
   value the F1 metric does not reflect.

## Implications for the project

Restating the three thesis options from the privacy/architecture
discussion in light of this data:

### Option A — Pure-LLM, delete the pattern engine

**Macro F1 in production: ~0.53 (cold), grows with HITL accumulation.**

- The pattern engine (~1500 lines) becomes dead code.
- A new template / new agency starts at the LLM ceiling and gets better
  over time as the converter accepts/edits suggestions.
- F1 drops ~0.14 vs hybrid, day-one.
- Most generalizable: the LLM is content-agnostic; whatever the JDA
  template looks like, it gets translated.
- Best fit if the operational reality is "lots of novel templates,
  little maintenance, accept HITL throughput as the closer."

### Option B — Keep parser, evict OBA content from code

**Macro F1 in production: ~0.67 (unchanged from today).**

- Pattern engine stays as content-agnostic infrastructure.
- The OBA-specific hardcodes (translation rules, audience regexes,
  drop list, type codes, entity table) move from Python into
  `agency_overrides/oba.toml`.
- New agencies are a TOML file under `agency_overrides/`, no Python edit.
- A `fake_org.toml` end-to-end test acts as a tripwire — fails if
  someone reintroduces an OBA hardcode.
- HITL persistence-as-patterns (already built) is how new agencies grow
  beyond the default config.
- Best fit if maintaining the current F1 quality matters and the
  successor is a non-developer operator who edits TOMLs but not
  Python.

### Option C — Status quo + HITL closer

**Macro F1 in production: ~0.67, climbing with HITL usage.**

- Don't refactor; trust the patterns to keep working on OBA.
- Use the HITL workflow's scoped suggestions to fix individual
  templates as they come up.
- New agencies require a developer to hand-author a new pattern library
  or accept the patterns-OFF F1 (~0.53) until enough HITL edits
  accumulate.

## Follow-up: H1 prompt-strip experiment (2026-05-12)

After the initial findings, I tested whether the prompt itself was
fighting itself. Three new hypotheses:

  H1 stripped   — strip the OBA-specific 10-rule block (`_TRANSLATION_RULES_OBA`)
                  and the JDA→Pine entity translation table from the
                  prompt. Lean on the universal role enum + vocabulary
                  + few-shot alone.
  H2 two_pass   — split the LLM call in two: pass 1 produces a document
                  plan (type, audience, entities referenced); pass 2
                  translates each token with the plan injected as a
                  compact context block.
  H3 structured — force a JSON-schema response via OpenAI's
                  ``response_format``, parse the resulting object
                  instead of the numbered-list text.

Results (16 templates × gpt-5.5 × patterns off):

| variant | macro F1 | unmatched | wall-clock |
|---|---:|---:|---:|
| baseline (pre-H1 prompt) | 0.526 | 24 | 254s |
| **H1 stripped** | **0.546** | **8** | 390s |
| H1b selective (keep rules 1, 6, 9, 10) | 0.526 | 10 | 355s |
| H2 two_pass | 0.527 | 23 | 454s |
| H3 structured | 0.433 | 69 | 306s |

**H1 stripped won.** Removing the rules block + entity table:
- Lifted macro F1 by 0.020
- Cut unmatched count by 67% (24 → 8 — significant recall gain)
- Removed ~150 lines of OBA-specific text from every prompt
- Per-template: won on 7, tied on 7, regressed on 2 (`LOA - Simple
  -0.19`, `UPL INV ROI -0.08`). The regressions trace to losing rule 1
  (bare-name splitting) and rule 6 (drop list).

**H1b selective failed.** Keeping just rules 1, 6, 9, 10 fixed the LOA
regression (0.727 → 0.912) but lost the gains on 5 other templates,
landing at the baseline 0.526. The OBA-specific rules — even just the
load-bearing ones — bias the model toward OBA-specific behavior in
ways that hurt other templates. The full strip is the right answer.

**H2 two_pass was a wash.** Plan extraction worked (model correctly
identified document types, audiences, entities) but didn't change
translation choices. Doubled wall-clock for nothing.

**H3 structured actively hurt.** F1 dropped 0.093, unmatched nearly
tripled. The strict JSON schema forced the model into terse outputs
without enough thinking — five templates returned all-empty results.

### Applied to production

The H1 strip was applied to `engine/llm_fallback.py`:

- Deleted `_SECTION_RULES`, `_SECTION_ENTITY_MAP`, `_TRANSLATION_RULES_OBA`, and `_build_entity_table`.
- Removed the corresponding section appends from `FallbackRequest.assemble_prompt` and `BatchFallbackRequest.assemble_batch_prompt`.
- Kept `_JDA_TO_PINE_ENTITY` (still used by the per-input entity hint, audience classifier, doc-context summary, and pattern transforms).
- Updated the two stale tests that asserted the now-removed sections.

Production-prompt diff: ~150 fewer lines per LLM call. Expected
pre-eval impact: pure-LLM macro F1 ~0.55 (up from 0.53); hybrid F1
(patterns + LLM) should be unchanged or slightly improved because the
LLM path now has better recall on the residual unmatched tokens.

## Recommended next steps (independent of which option)

These hold regardless of A/B/C:

1. **Roll back the o3 32k budget change** (or keep as a knob, see
   above) since it didn't pay off.
2. **Drop Presidio from any future plan** — it hurts.
3. **Don't add more entity-table entries without context validation.**
   The `Cust_CIPs` example shows shadowing risk. Any addition needs
   to be checked against templates where the entity appears in
   multiple conditional branches.
4. **The HITL workflow is the real production answer.** F1 is a
   proxy; the converter's edit count and acceptance rate are the
   metrics that matter. Worth instrumenting.

## Artifacts on disk

All raw data, prompts, responses, and converted RTFs:

```
Agent/comparison_runs/                ← initial 4-template comparison
Agent/comparison_runs_llm_only/       ← initial 4-template, patterns off
Agent/comparison_runs_phase_a/        ← 16-template Phase A (current code)
Agent/comparison_runs_phase_c/        ← 16-template Phase C (improved code)
Agent/model_compare/{o3,o3_32k,flagship,mini,nano}/   ← model spread
```

Each subdirectory has per-template `__summary.md` for quick scanning
and per-strategy `.prompt.txt` / `.response.txt` / `.converted.rtf`
for deep inspection.

## Files changed during the investigation

After the investigation, the experimental scaffolding was removed
since it had no production benefit. What remains, kept because it
proved useful in production HITL paths:

- `Agent/v2/engine/audience.py` — extracted from `llm_fallback.py`
  for reuse by the suggestion-store scope loader.
- `Agent/v2/engine/suggestion_store.py` — extended for scoped
  suggestions and multi-token (list) match/rewrite forms.
- `Agent/v2/pipeline.py` — accepts audience hint, exposes
  `rebuild_result_with_edits`, classifies audience for the
  suggestion loader.
- `Agent/v2/engine/llm_fallback.py` — rewrote rule #7 from
  conservative-bias to aggressive-translation framing.
- `Agent/v2/patterns/transforms.py` — added 2 confirmed entity
  entries (`KF_PhoneNumberActive`, `KF_Atty_Pros_Active_AgencyNum`).
  `Cust_CIPs` was tried and rolled back — see "context-shadowing"
  lesson above.
- `Agent/gui/pages/4_v2_Pipeline.py` — edit-all-segments UI + scoped
  persistence (used by HITL).
- Tests added: `test_suggestion_store_scope.py`,
  `test_suggestion_store_multitoken.py`, plus rebuild tests in
  `test_pipeline.py`.

**Removed during cleanup** (experiments concluded, no production
value):

- `Agent/v2/engine/privacy_strategies.py` — four context strategies.
  All within 0.016 F1 of AST-only baseline.
- `Agent/v2/tools/compare_privacy.py` — comparison harness for the
  strategies.
- `Agent/v2/tests/test_privacy_strategies.py` — tests for the above.
- Privacy strategy wiring in `llm_fallback.py` (`privacy_strategy`
  kwarg, `privacy_context` field, RTF/hits plumbing).
- Reasoning-model 32k token budget bump in `llm_fallback.py` — made
  o3 worse, not better.
- `presidio-analyzer` / `presidio-anonymizer` / `spacy` /
  `en_core_web_lg` packages — only the removed Presidio strategy
  consumed them.

Raw experimental data on disk (`Agent/comparison_runs*`,
`Agent/model_compare/`) is preserved as evidence for the findings
above. Safe to delete once this document has been distributed.

409 tests passing post-cleanup (was 419; the 10 removed were privacy
strategy tests).

# Pipeline Critique — 2026-04-20

Honest assessment of the JDA→Pine agent pipeline. The goal: take a legacy
template and produce a valid Pine template. This document is a frozen snapshot
of architectural concerns at this date — verify against current code before
acting on any specific recommendation.

## Structural problems (ranked by impact)

### 1. Prompt micromanagement is a losing battle
`prompts.py` has 14+ MANDATORY RULES and growing. Every failed eval adds a new
rule. This pattern doesn't converge — it fights itself:
- Rule 4 vs Rule 2 on OBAAttorney.Title: one says "check RAG," the other says
  "drop it."
- Rule 7's entity table duplicates the one in `pine_context.md` — two sources
  of truth, both consulted by the same LLM.
- Each added rule shifts attention and risks the LLM forgetting earlier rules
  mid-generation.

This is *prompt-based exception handling*. It works until it doesn't, and
regressions become "did I break rule 9 when I tightened rule 3?"

### 2. Token-at-a-time mapping is the wrong granularity
`mapping_call` maps each `%[...]` in isolation, but several transformations
are inherently contextual:
- **MrMs → NameFirstName** — only correct in a salutation.
- **c/o reversal** — requires analyzing three tokens + surrounding text (the
  `_reverse_co_direction` heuristic).
- **`DocumentEvents.EventDt`** — prompt variable in a letter header, data
  binding elsewhere.
- **Branch content normalization** — handled by pre-processing but only for
  patterns matching `_INVERTED_IF_RE`.

Everything that doesn't fit in a single token gets squeezed into a regex
heuristic or a prompt rule. Both are brittle.

Stronger shape: extract token + surrounding context window (~200 chars), pass
that to the mapper, and let it reason about context. The missing signal is
"what does the text around this token say."

### 3. RAG is the wrong retrieval shape for most of this
`pine_syntax_ground_truth.txt` is mostly a lookup table with exception rules.
Vector retrieval over that is:
- **Expensive** — embedding + chroma call per search, plus LLM tokens for the
  text chunks.
- **Unreliable** — semantic similarity can miss the exact match and surface a
  near-miss.

For the deterministic 1:1 portion (which is most of it), a **structured
JSON/YAML rule table** beats RAG on every axis: faster, cheaper,
deterministic, diffable. RAG makes sense when the mapping shape isn't known
ahead of time — but here it is.

### 4. LCS F1 evaluates tokens, not correctness
`_sequence_f1` measures token presence and sequence. It will:
- **Over-reward** a template with right tokens in the wrong conditional branch
  (already seen — this is why the c/o bug had to be hunted manually).
- **Under-reward** a semantically-equivalent but differently-structured output
  (FormatName vs split tokens — both valid in some cases).
- Not catch **rendering failures**: malformed RTF, unbalanced
  `@[If]`/`@[EndIf]`, invalid preset numbers.

95% F1 is compatible with a non-rendering output. For a production tool, the
true metric is "does Pine render this and produce the same visual output as
the ground truth." Render-and-diff beats token F1.

### 5. Cache is doing little work
`_is_context_dependent` filters out conditionals, DocumentEvents, FormatDate.
`_is_prompt_variable_mapping` filters out prompt variables. What's left — pure
entity.field mappings — is exactly the set that could be a 200-line JSON file.
The cache is re-learning facts that should be static.

## Smaller but real issues

- **Two sources of truth for entity translation.** `pine_context.md` and
  `prompts.py` both list the JDA→Pine entity table. They can drift. Pick one
  (ideally a data file, not prose).
- **`_strip_invented_structure` is a symptom, not a fix.** Cleaning up LLM
  output because it sometimes invents `@[If]`/`@[Else]` wrappers. Root cause
  is ambiguous prompting around what counts as a "token." Constrain output
  schema harder.
- **`make_pine_template_generation_call` exists but unused.** The real path is
  `replace_fillpoints`. Deterministic replacement is the right call — delete
  the unused node for clarity.
- **No test coverage on the regex nodes.** `_swap_inverted_branches`,
  `_reverse_co_direction`, `extract_fillpoints` are load-bearing. Unit tests
  with hand-crafted RTF snippets would catch regressions.
- **No feedback loop from eval failures into few-shot examples.** Fixes are
  hand-edited prompts. A better system would auto-surface failures as few-shot
  examples for the next run.

## What to do differently

1. **Extract once, classify, then route.** Classify each token: pure-lookup,
   contextual, structural, prompt-variable. Route each class through its own
   mapper. Don't make one LLM call handle all four.
2. **Lookup table for the deterministic 80%.** A flat `mappings.yaml` covers
   the bulk. LLM only runs on tokens not in the table.
3. **Pass context to the LLM for contextual tokens.** ±200 chars around the
   token so the model can distinguish salutation vs body, header vs inline.
4. **Render-and-diff eval.** If Pine has a CLI, render both templates with
   synthetic data and compare. Token F1 becomes a secondary signal.
5. **Move pre-processing out of `nodes.py`.** RTF normalization, branch swap,
   c/o reversal are a "legacy-to-canonical" pass independent of the agent.
   Separate module, unit tested.
6. **Freeze the prompt.** Before adding rule #15, ask: "can this live in the
   lookup table or in a pre-processing pass instead?" Usually yes.

## What's already right

- Deterministic extraction via balanced-bracket scan.
- Structured-output mapping (no LLM in the final output path).
- Deterministic `replace_fillpoints` for final assembly.
- Pre-processing branch swap for `IsEmpty=true` conditionals.
- Separation: facts in ground truth, directives in prompts/context.

The spine is correct. The weak parts are how much work the LLM does per
token, how much knowledge lives in prompts vs data, and an eval metric that
doesn't measure what matters.

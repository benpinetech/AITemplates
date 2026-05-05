# Agent v2 — chunk-based JDA → Pine template converter

This directory holds the in-progress refactor of the JDA → Pine converter.
The existing pipeline in `Agent/src/` keeps running unchanged while v2 grows
alongside; both can be driven from the same eval harness so we can compare
their output token-by-token against the verified corpus.

This README is the single entry point. Read it top to bottom before reading
any code — it explains *what* exists, *why* each piece exists, and *what
order* to read things in.

---

## 1. What this project does

The agent converts legal document templates from the legacy **JDA** template
language into the **Pine** template language.

- JDA expressions are wrapped in `%[...]`.
- Pine expressions are wrapped in `@[...]`.
- Templates are stored as RTF files. A single template is a mix of legal
  prose (names, addresses, paragraphs) and bracketed expressions.

The agent operates in a human-in-the-loop workflow: a *mapper* runs it
locally, reviews the converted template in a diff UI, and accepts or
rejects each conversion. Accepted templates form the verified-pair corpus
that the agent learns from.

### Privacy boundary (non-negotiable)

The LLM may **only ever see template syntax**:

- bracketed expressions (`%[...]`, `@[...]`)
- the Pine grammar
- the JDA grammar
- the org's allowed Pine vocabulary

The LLM must **never** see:

- the surrounding legal prose
- party names, addresses, case numbers, or any other template content

All prose handling happens in local code. Pure-syntax content (the ground
truth file, the verified pair corpus' bracketed expressions, the brackets
themselves) is fine to send to the LLM. **This boundary is enforceable: the
extractor in v2 emits only bracketed expressions before any LLM call.**

---

## 2. Why v2 exists

v1 (`Agent/src/`) maps tokens one-at-a-time using a single LLM call per
token, with RAG over a 1820-line ground-truth file plus a growing list of
"MANDATORY RULES" in the prompt. It works, but every new failure mode
becomes either a new prompt rule or a new regex pre-processing pass. The
ground-truth file mixes three concerns into one document: the Pine
language reference, JDA→Pine conversion rules, and accumulated
anti-hallucination patches.

v2 changes the unit of translation from the **token** to the **chunk** — a
parameterized AST pattern. A defensive-null wrapper like
`%[If(X.IsEmpty)] %[Else] X %[EndIf]` is one chunk that becomes one Pine
expression, not five separate token mappings the LLM has to reassemble.
The pipeline is layered so most of the conversion is deterministic
pattern-rewriting on ASTs, and the LLM is only invoked on chunks that
don't match any known pattern.

The full critique that motivated this is in `../PIPELINE_CRITIQUE.md` —
read it for the long-form reasoning.

---

## 3. Architecture (5 phases)

```
                      +-------------------+
  legacy.rtf  ──►     |  RTF extractor    |  (Phase 1, deterministic)
                      |  pulls %[...]     |
                      |  with positions   |
                      +---------+---------+
                                │
                                ▼
                      +-------------------+
                      |  JDA AST parser   |  (Phase 1, deterministic)
                      |  %[...] → AST     |
                      +---------+---------+
                                │
                                ▼
                      +-------------------+
                      |  Pattern matcher  |  (Phase 4, deterministic)
                      |  AST → Pine AST   |
                      |  via pattern lib  |◄── patterns/*.toml  (Phase 2/3)
                      +---------+---------+
                                │
                  unmatched     │
                  chunks ──►    │
                                ▼
                      +-------------------+
                      |  LLM fallback     |  (Phase 4, LLM, syntax-only)
                      |  AST + allow-list |
                      +---------+---------+
                                │
                                ▼
                      +-------------------+
                      |  Pine validator   |  (Phase 4, deterministic)
                      |  grammar + lint   |
                      |  + vocab gate     |
                      +---------+---------+
                                │
                                ▼
                      +-------------------+
                      |  Pine RTF writer  |  (Phase 5, deterministic)
                      |  prose untouched  |
                      +-------------------+
                                │
                                ▼
                          generated.rtf
```

### Phase status

| Phase | What it produces | Status |
|---|---|---|
| 1 — Parsers and extractors | JDA AST parser, Pine AST parser, RTF expression extractor | **Done** — 99.95% round-trip on the corpus (20,380 / 20,391 expressions). 91 unit + integration tests passing. |
| 2 — Pattern format and seed library | TOML pattern DSL + seeded patterns from ground truth section 30 | **Done.** Schema, loader, matcher (single-token + chunk + sequence holes), rewriter (with textual + AST substitution), transform registry, single-token engine, stream engine. 15 seed patterns: ProsNum, FormatDate(CurrentDate), Subdocument, Initials/cu, prompt variables, OBA FullName/LastName, defensive null wrapper, OBA address block, OBA gender pronoun block. 134 tests total. |
| 3 — Reorganize ground truth | Split the monolith into `pine_grammar.toml`, `pine_data_model.toml`, `patterns/*.toml`, `org_overrides/oba.toml`, `lint_rules.toml` | **Done.** Five queryable assets under `grammar/` with Pydantic loaders + tests. Original `pine_syntax_ground_truth.txt` preserved as narrative reference. 154 tests total. |
| 4 — Mining + matching + fallback + validation | Pattern engine, mining pipeline, LLM fallback, validator | **Validator + LLM fallback done; miner slice 1 done.** Validator covers lint rules, vocabulary, and structural balance. LLM fallback has prompt assembly + privacy-invariant tests + Anthropic SDK adapter (gated on API key) + mock client. Miner slice 1 walks the corpus with index alignment; slice 2 (tree alignment) needed for the bulk. Pattern matching engine was built in Phase 2/2.5/2.6. 205 tests total. |
| 5 — Org context + end-to-end pipeline | Wire phases 1–4 together with the existing mapper diff UI | **Pipeline + CLI + corpus runner done.** `pipeline.convert_template(rtf, org, ...)` extracts → matches → optional LLM fallback → validates → reconstructs RTF (prose untouched). Per-segment provenance attached. Org context required. 14 pipeline tests + 219 total. GUI integration not yet wired. |

### Phase 1 result

Both parsers run cleanly over the full verified-pair corpus:

```
LEGACY: 9143/9149 round-trip (99.93%) — 6 failure classes, all genuinely
        malformed source (empty function args, multi-word unquoted RHS
        containing the `in` keyword, comma-bearing unquoted values).
PINE:   11237/11242 round-trip (99.96%) — 5 failure classes (`Else If`
        typo with literal space, IN's RHS interleaved with `&&`,
        unquoted multi-word RHS).
OVERALL: 20380/20391 (99.95%)
```

The 11 remaining failures are documented in `tools/corpus_round_trip.py`
output. They're genuinely-broken sources, not parser bugs. They surface
during pattern-matching as "no pattern matched" and become LLM fallback
candidates in Phase 4.

The corpus regression test in `tests/test_corpus_round_trip.py` gates
the round-trip rate at ≥99.5% so a future parser regression breaks the
test before it ships.

After each phase the artifact is independently testable — see
[Section 7](#7-how-to-run-things).

---

## 4. What is NOT built and what depends on someone else

### 4.1 Accept / reject feedback loop (deferred)

The plan calls for "the mapper's accepted edits flow back into the
verified-pair corpus and (when applicable) into the pattern library." That
loop does **not exist today**. The current Streamlit dashboard (`gui/`)
lets the mapper see a diff but has no path that writes the accepted Pine
RTF anywhere. Designing and building that loop is **deferred to a later
phase** after the core pipeline is in place. When it is built, it touches
the GUI, not the pattern engine.

### 4.2 Live org-variable API (deferred — needs collaborator input)

The plan calls for the org's allowed Pine vocabulary to come from a live
API, so different orgs (OBA, criminal/PD, etc.) can have different allow
lists. **That API does not exist today.** Building it requires back-and-
forth with the collaborator on this project, and the contract is not yet
settled. Until it lands, v2 will use a static stand-in:

- For the OBA org, the allow list is derived from the verified Pine
  templates in `Agent/ground_truth/evaluation_templates/jda_to_pine/pine/`
  by extracting every distinct `@[...]` token. (This is the OBA
  vocabulary in practice — the corpus is OBA.)
- For other orgs, no allow list is configured yet. v2 will report
  "vocabulary not configured" rather than silently allowing everything.

When the live API is ready, the validator's `vocabulary_for(org)` call
will swap from the static loader to the API client. The rest of the
engine is unaffected.

### 4.3 LLM-suggestion closed loop (slice 1 done)

The promote-from-LLM-output loop's first slice is **built**:

  - The pipeline merges every TOML under
    ``Agent/v2/suggestions/verified/<org>/`` into the pattern library
    on each run.
  - The v2 GUI page renders an "LLM suggestions" panel for any
    LLM-fallback segment, with **Accept** / **Reject** buttons.
  - **Accept** writes a verified-pattern TOML to
    ``suggestions/verified/<org>/`` so the next run matches that JDA
    token deterministically — no LLM call, no risk of the LLM
    changing its mind.
  - **Reject** appends to ``suggestions/rejected.log`` (audit trail
    only — does not affect future matching today).

The closed loop is exercised end-to-end by
``tests/test_pipeline_suggestion_loop.py``.

#### What's NOT yet built (saved for later)

These were called out in the planning conversation and intentionally
deferred:

  - **Richer LLM context** — the prompt sees one token at a time;
    surrounding-token context (±5) and chunk-shape hints would
    improve novel-template suggestions for context-dependent tokens.
  - **Promote-to-pattern action** — verified suggestions are exact-
    match today. A "generalize this verified mapping into a $entity
    pattern" GUI action would let one accept cover many similar
    future tokens.
  - **RAG over `pine_idioms.md`** for additional few-shot.
  - **Negative-shot prompting from rejected.log** — today the
    rejection log is audit-only.
  - **(JDA, Pine) corpus pairs as few-shot** — needs Phase 4 mining
    slice 2 (tree alignment).
  - **Auto org-detection** — explicit ``--org`` is still required.

### 4.4 Promote-from-aligned-corpus mining (still deferred)

The miner's slice-2 work — tree alignment over the verified-pair
corpus — is a separate path to growing the pattern library. Slice 1
mining is in place; slice 2 unlocks the ~270 templates skipped today
because their token counts differ between sides.

The miner today only works on **paired** corpus inputs (legacy.rtf +
verified pine.rtf). For a **novel** template with no Pine
counterpart, the miner can't propose patterns — it has nothing to
align against. The pipeline will still convert the template (15 seed
patterns + LLM fallback per token), but those LLM-produced outputs
don't feed back into the pattern library today.

The natural extension: log every (jda_token, llm_pine_output) pair
the LLM fallback produces; when the same JDA shape produces the
same Pine output across N templates, propose a candidate pattern.
This closes the loop between LLM fallback and pattern growth without
needing paired corpus inputs. Phase 6 territory.

### 4.4 Anything outside `Agent/`

The repo contains `phase1-jda-to-pine-extractor/` (a C# RTF extractor)
and `AiInCourtAssistant.{Server,Client,Shared}/` (a separate .NET app
with its own simpler migration pipeline). **v2 ignores all of these.**
Decisions for v2 are based exclusively on `Agent/`. If integration with
those is needed later, it will be a separate workstream.

---

## 4.5 Where the LLM is used (and where it isn't)

The LLM appears in **exactly one place** in v2:

> ``engine/llm_fallback.py`` — invoked by ``pipeline.convert_template``
> on segments the deterministic pattern engine could not match.

That's it. The miner is fully deterministic (index alignment +
structural generalization, no LLM calls). The validator is fully
deterministic (regex + structural checks). The pattern matcher is
fully deterministic.

When the LLM fallback runs, it sees a single JDA AST + the org's
vocabulary + 3-5 retrieved few-shot patterns from the active
library + an optional grammar fragment. **No template prose.** It
returns a single Pine token's worth of text, which the parser then
validates and the pipeline splices in with provenance
``llm-fallback``.

The LLM is never called per-template (only per-unmatched-token), and
it never sees more than one token at a time — so a multi-token
unmatched chunk that needs to become one Pine token via LLM is **not
possible today**. That's a real limitation; multi-token LLM fallback
would be a Phase 6 extension.

## 4.6 How chunks are determined

A chunk is **declared by a pattern's TOML file**. A pattern whose
``match`` is a *list* is a chunk pattern:

```toml
match = [
  "%[If($entity.IsEmpty=true)]",
  "$empty_body...",
  "%[Else]",
  "$has_entity_body...",
  "%[EndIf]",
]
```

The matcher walks the JDA token stream and tries every chunk pattern
at every position (highest priority first), with sequence-hole
backtracking. Whatever matches first wins. If no chunk fires at a
position, the engine falls back to single-token patterns; if those
also miss, the segment is unmatched and (optionally) goes to the
LLM.

There is no automatic chunking of unmatched tokens — the engine does
not say "these 3 unmatched tokens look related, send them as a
chunk." Each unmatched token is processed in isolation.

## 5. Glossary

Read this once before you read code. The plan uses these terms with
specific meanings.

| Term | Meaning |
|---|---|
| **Token** (in v1) | A single bracketed expression like `%[X.FullName]`. v1's mapping unit. |
| **Chunk** (in v2) | A larger AST pattern that may span multiple tokens (e.g. an If/Else/EndIf trio). v2's mapping unit. |
| **AST** | Abstract syntax tree — the parsed form of a bracketed expression. Each AST node has a Python class with named fields, not a string. |
| **Pattern** | A pair of (match-AST-shape, rewrite-AST-shape) with named *holes*, plus conditions and metadata. Lives in a TOML file. |
| **Hole** | A named placeholder in a pattern that can match an arbitrary subtree (e.g. `$entity`, `$field`). When the pattern matches, holes capture the actual subtree; the rewrite uses the captured subtree. |
| **Org context** | Which legal-org's template we're converting (OBA, criminal/PD, etc.). Some patterns differ by org; the org context selects which patterns apply and which vocabulary the validator uses. |
| **Vocabulary / allow list** | The set of `@[...]` names the target org actually has. The validator rejects any generated Pine that references something not in this set. |
| **Verified pair** | A `(legacy.rtf, pine.rtf)` pair where the Pine output has been hand-verified by the mapper. The 300+ pairs in `Agent/ground_truth/evaluation_templates/jda_to_pine/` are the gold standard. |
| **Few-shot pool** | A subset of verified pairs retrieved by similarity to use as in-context examples for the LLM fallback. Same data; different role. |
| **LLM fallback** | The LLM call made only for chunks that no pattern matches. Sees only the AST subtree, the allow list, and 3–5 few-shot examples — never prose. |
| **Lint rule** | An anti-pattern that the validator rejects. Replaces section 35 of the old ground-truth file. |

---

## 6. Directory layout

```
Agent/v2/
├── README.md                    ← you are here
├── parser/                      ← Phase 1
│   ├── README.md                ← parser-layer doc
│   ├── jda_ast.py               ← AST node classes for JDA
│   ├── jda_parser.py            ← %[...] → JDA AST
│   ├── pine_ast.py              ← AST node classes for Pine
│   ├── pine_parser.py           ← @[...] → Pine AST
│   └── rtf_extractor.py         ← scan RTF, yield (pos, expr, AST)
├── tests/                       ← pytest suite
│   ├── conftest.py              ← shared fixtures (corpus paths, etc.)
│   ├── test_jda_parser.py
│   ├── test_pine_parser.py
│   ├── test_rtf_extractor.py
│   └── test_corpus_round_trip.py  ← runs parsers over the full corpus
└── tools/
    └── corpus_round_trip.py     ← human-runnable round-trip report

├── patterns/                    ← Phase 2 / 2.5 / 2.6 pattern library and engine
│   ├── README.md
│   ├── schema.py / loader.py
│   ├── matcher.py / rewriter.py / engine.py
│   ├── transforms.py / holes.py
│   └── library/                 ← seed patterns (organised by org)
│       ├── common/
│       └── oba/
├── grammar/                     ← Phase 3 split assets
│   ├── README.md
│   ├── pine_grammar.toml        ← sections 1–12 of the legacy ground truth
│   ├── pine_data_model.toml     ← sections 13–22
│   ├── lint_rules.toml          ← section 35 anti-patterns
│   ├── pine_idioms.md           ← sections 26, 27, 29 (narrative)
│   ├── loaders.py               ← Pydantic loaders + queries
│   └── org_overrides/
│       └── oba.toml             ← sections 31–34 + OBA vocabulary

├── engine/                      ← Phase 4 validator / LLM fallback / miner
│   ├── README.md
│   ├── validator.py             ← lint rules + vocabulary + structural checks
│   ├── llm_fallback.py          ← prompt assembly + Anthropic SDK adapter + mock
│   └── miner.py                 ← slice 1 index-aligned single-token mining

├── pipeline.py                  ← Phase 5 end-to-end driver: convert_template(rtf, org)
└── tools/
    ├── corpus_round_trip.py     ← Phase 1 round-trip diagnostic
    ├── corpus_pipeline.py       ← Phase 5 corpus runner with coverage stats
    ├── convert.py               ← single-template CLI
    └── mine_patterns.py         ← Phase 4 miner CLI
```

### Where to read code in what order

If you are coming back to this code fresh:

1. This file (`README.md`).
2. `parser/README.md` — AST design and parser strategy.
3. `parser/jda_ast.py` — JDA AST node classes with examples.
4. `parser/jda_parser.py` — recursive-descent parser. Read top-to-bottom.
5. `parser/pine_ast.py` and `parser/pine_parser.py` — richer grammar.
6. `parser/rtf_extractor.py` — RTF scanning and position tracking.
7. `patterns/README.md` — pattern format spec and authoring guide.
8. `patterns/schema.py` — Pydantic models (the wire format).
9. `patterns/library/**/*.toml` — the seed patterns themselves.
10. `patterns/transforms.py` — named transforms (entity translation, format presets, etc.).
11. `patterns/matcher.py` and `patterns/rewriter.py` — match/substitute walkers.
12. `patterns/engine.py` — top-level entry point.
13. `tests/` — every supported construct has a test.
14. `tools/corpus_round_trip.py` — diagnostic report on the corpus.

---

## 7. How to run things

v2 uses the same Python venv as v1 (`venv/` at the repo root). v2 adds
`pytest` for the test suite. From the repo root:

```bash
# one-time setup (adds pytest)
./venv/bin/pip install -r Agent/v2/requirements.txt

# run all v2 tests
./venv/bin/pytest Agent/v2/tests/

# run the round-trip report against the full corpus
./venv/bin/python Agent/v2/tools/corpus_round_trip.py
```

The round-trip script reads `Agent/ground_truth/evaluation_templates/jda_to_pine/{legacy,pine}/*.rtf`,
extracts every bracketed expression, parses it, unparses it, and reports:

- how many parsed
- how many round-tripped (parse → unparse → parse again is structurally equal)
- which expressions failed and why

Failing expressions are the to-do list for the parser. **A small failure
tail is expected** — the parser starts with the common cases and grows.

---

## 8. Design decisions that need to stay consistent

These are baked into the v2 architecture. Changing one of these later
would require revisiting every downstream component.

### 8.1 Hand-rolled recursive-descent parser

When this refactor was scoped, I proposed using Lark (a parser generator)
to write the grammar declaratively. **I changed my mind during
implementation.** The two languages are small enough that hand-rolled
parsers come out to ~300 lines each, and a hand-rolled parser is more
transparent for someone reading the code without Lark expertise. Every
parsing decision lives in step-debuggable Python; nothing is hidden in a
grammar engine.

Trade-off: the grammar is encoded across many small functions instead of
one EBNF file. We've documented every function with what it matches and
an example.

### 8.2 ASTs are immutable dataclasses

Each AST node is a `@dataclass(frozen=True)`. Pattern matching, mining,
and rewriting all produce new ASTs rather than mutating in place. This
makes equality comparisons and round-tripping trivial.

### 8.3 Round-trip is structural, not byte-exact

Legacy templates have inconsistent whitespace inside expressions
(`%[ JW_Respondent.FullName ]` vs `%[JW_Respondent.FullName]`). The
parser normalizes these. Round-trip means
`parse(unparse(parse(s))) == parse(s)`, not `unparse(parse(s)) == s`.

### 8.4 The parser is permissive about whitespace; the unparser is canonical

Inputs may have extra whitespace anywhere; outputs use one canonical
spacing. This means we can hand-author patterns in a clean form without
worrying about how the corpus was indented.

### 8.5 Phase 1 is independent of phase 2+

The parsers and extractor have no dependency on patterns, engines, or
LLMs. Anyone can use the parser without the rest of v2. This is intentional
— it lets us ship and validate phase 1 before any decisions on the engine
are locked in.

---

## 9. Open questions and future work

These are explicit hooks where decisions are needed before later phases.

- **Pattern format**: TOML, with a Pydantic schema. To be implemented in
  Phase 2.
- **Org context conditionals**: per-pattern `org_context` field plus
  optional whole-pattern overrides in `org_overrides/<org>.toml`. To be
  implemented in Phase 2.
- **Mining aggressiveness**: human review by default in Phase 4; auto
  promotion is deferred until we have telemetry on accept rates.
- **Live org-variable API contract**: deferred (see §4.2). The validator
  in Phase 4 will call a function `vocabulary_for(org)` that for now
  reads the static stand-in; later that function swaps to an HTTP client.
- **Render-and-diff eval**: not in scope for this refactor; the existing
  token LCS-F1 stays in place. `PIPELINE_CRITIQUE.md` flags this as a
  future improvement.

---

## 10. How to extend each phase

Each phase has a contract. Adding new functionality means adding to a
single file or a single TOML, not editing prose in a giant prompt:

- **Add a new conversion rule** → add a TOML file under `patterns/`.
- **Fix a hallucination** → add an entry to `lint_rules.toml`.
- **Support a new org** → add `org_overrides/<org>.toml` and connect that
  org's variable allow list (currently a stand-in; later the live API).
- **Support a new JDA construct** → extend `parser/jda_parser.py` and add
  a test in `tests/test_jda_parser.py`.

The day-to-day workflow is "add a pattern, run the test suite, run the
corpus round-trip, look at the diff."

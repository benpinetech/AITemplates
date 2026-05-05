# Phase 4 — validator, LLM fallback, miner

The Phase 2 / 2.5 / 2.6 pattern engine handles the bulk of conversion
deterministically. Phase 4 adds three things around it:

| Module | Role | Status |
|---|---|---|
| `validator.py` | Check generated Pine output against lint rules, vocabulary, structural balance | **Done** |
| `llm_fallback.py` | Convert unmatched chunks via an LLM call, with a constrained prompt (no prose) | **API + mock done; live client adapter wired but unexercised** |
| `miner.py` | Walk the verified-pair corpus, propose new candidate patterns | **Slice 1: index-aligned single-token mining** |

The pattern matcher (`../patterns/engine.py`) is the third leg of
"Phase 4" in the original spec — it already lives in the patterns
directory because that's where its data and tests are.

## Validator

Inputs:
  - a list of `PineToken` ASTs (typically the outputs of one
    chunk-aware conversion stream)
  - a vocabulary allow-list (`OrgRoot.vocabulary` from
    `../grammar/loaders.py`)
  - optionally a `LintRules` set (default: load from disk)

Output: a list of `ValidationIssue` objects, each with severity,
rule id, message, and the index of the offending token.

What it checks:

1. **Lint rules.** Every regex in `lint_rules.toml` is run against
   each token's `unparse()` text. `severity = "error"` blocks; `severity
   = "warning"` surfaces.
2. **Vocabulary.** Every entity-shaped chain base (the `base` of a
   `PineChain` when it's a string) must appear in
   `vocabulary.entities`, `vocabulary.builtins` (matched on the head
   segment), `vocabulary.prompt_variables`, or be a known
   `cu`-style shorthand. ForEach loop variables introduced by a chain
   like `Charges.ForEach(c)` are tracked in scope and accepted.
3. **Structural balance.** `If` / `EndIf`, `Foreach` / `EndForEach`,
   `Cca` / `EndCca`, `Lb` / `EndLb` — the matcher only sees individual
   tokens, so we count opens/closes across the stream.

Validator is a pure function — same inputs → same outputs. Run it
after the conversion pipeline; surface the issues to the mapper.

## LLM fallback

The fallback runs only when the pattern engine produces an unmatched
segment. The prompt sent to the LLM contains only:

  - the unmatched JDA AST (its `.unparse()` text)
  - the org's allowed Pine vocabulary
  - 3–5 verified-similar (JDA, Pine) examples retrieved from the
    pattern library (the patterns themselves serve as the few-shot
    pool — they're hand-verified)
  - a short grammar fragment relevant to the input

**Privacy invariant**: the prompt never contains template prose. The
`FallbackRequest.assemble_prompt()` method is the chokepoint —
auditable, unit-testable. A test asserts that the assembled prompt
contains no characters outside the bracket alphabet plus the bounded
vocabulary / grammar fragments.

The module exposes:

```python
class LlmClient(Protocol):
    def complete(self, prompt: str) -> str: ...

class LlmFallback:
    def __init__(self, client: LlmClient, library: list[Pattern], vocabulary): ...
    def convert(self, jda_token: JdaToken, org: str) -> Optional[PineToken]: ...
```

The default real client uses the Anthropic SDK (`claude-opus-4-7`).
For tests, `MockLlmClient` returns canned responses keyed by prompt
substring, so we can exercise the assembly + parsing path without
network or API keys.

## Miner

Slice 1 (current): walks the verified-pair corpus with simple index
alignment. For each template where `len(jda_tokens) == len(pine_tokens)`
and the existing pattern engine cannot already convert all of them,
the miner proposes a candidate pattern by:

  1. parsing both sides;
  2. checking what already converts via the existing engine — those
     are skipped;
  3. for the remainder, attempting structural generalization (replace
     the entity-name segments with `$entity` holes, look for
     consistent transforms);
  4. writing each candidate to `../patterns/_candidates/<id>.toml`
     for human review before promotion to the active library.

Slice 1 won't catch chunk patterns or patterns where the JDA and Pine
token counts differ — those need real tree alignment. That's slice 2.

The miner is **read-only against the active library**: it never
silently registers a pattern. Every candidate is reviewed before
promotion.

### Important caveat: slice 1 candidates are noisy

Index alignment is wrong whenever the legacy and pine templates have
the same token count *but the order doesn't correspond*. Real example
from the OBA corpus: address fields in the legacy template are
`Address / City / StateCode / Zip`; the Pine template has them in the
same order but a stray `CreateVar` filtered earlier shifts the index
mapping by one, producing a bogus pair like `JDA .StateCode → Pine
.City`. The miner can't see this — it trusts the index.

Mitigation: every mined candidate is `verification = "candidate"` and
sits in `_candidates/` until a human inspects it. The reviewer is
expected to catch and discard wrong pairings. Slice 2 will use tree
alignment on the AST shapes to filter these automatically.

Running the miner on the full ~300-pair corpus today produces:

```
Templates processed:        298
Token-pairs examined:       290   (from same-count templates only)
Already covered:            20    (existing patterns work)
Skipped (count mismatch):   270   (slice 2 territory — most templates)
Un-mineable (slice 1):      229   (single-entity heuristic didn't fire)
Distinct candidates:        14    (after dedup; some are wrong, see above)
```

Most templates differ in token count between sides because Pine
typically has CreateVar prelude declarations that JDA doesn't, plus
patterns like `.FullName` splitting into two Pine tokens. Slice 2's
tree alignment is what unlocks meaningful mining for those.

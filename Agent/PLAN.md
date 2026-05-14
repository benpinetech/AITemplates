# Plan — Improving conversion accuracy

## Where we are

| run | macro F1 | precision | recall | duration | LLM |
|---|---|---|---|---|---|
| BaseRun (before today's work) | 0.19 | 0.19 | 0.19 | — | off |
| Patterns only (after pattern additions) | **0.643** | **0.635** | 0.650 | 7 s | off |
| Patterns + LLM (batched, rich prompt) | 0.634 | 0.599 | 0.673 | 295–738 s | on |

Two surprising findings the data shows, both important:

1. **The LLM, as currently configured, is roughly net-zero.** It helps 56 templates (avg +0.05) and hurts 57 (avg -0.04). On templates that patterns already nail, the LLM tends to *add* spurious extras, dragging precision down faster than the wins on harder templates.
2. **Patterns-only is the current best.** 0.643 macro F1 with 7-second runtime is the floor we should be improving from, not the LLM run.

## Where the errors come from

I categorized every LLM-introduced extra (across 135 affected templates):

| category | count | what it is |
|---|---|---|
| Well-formed Pine but wrong-context | **296** | `@[respondentaddress.first.city]` emitted in a template whose ground truth is for the complainant, etc. |
| Shallow path (missing `.first.`) | 45 | `@[builtin.caseid]` instead of the corpus convention |
| JDA prefix leaked | 18 | `@[jw_wit_addy.first.zip]` — entity not in our table |
| Other | 52 |  |

**The dominant precision killer is the LLM "translating too eagerly" tokens that the human translator chose to drop.** Concretely:

| top untranslated JDA token | count | does Pine ground truth ever contain this? |
|---|---|---|
| `Cust_Complainant.StateIDNum` | 181 | **No** — corpus has zero StateID-related Pine tokens |
| `Cust_OBAAttorney.Title` | 147 | **No** — humans drop it |
| `KF_AttyProsTitle.NameAttributeValue` | 68 | **No** — humans drop it |
| `DCNumber.DCNumber` | 21 | **No** |
| `*.HisHer` / `*.HeShe` (gender pronouns) | 12 + 9 | **No** — pronouns get inlined as literal text |

These five classes alone account for **~440 of 1116 unmatched tokens (39%)**. Every one becomes a false-positive Pine token when the LLM tries to translate it. v1's prompt explicitly told the model to drop them — v2 doesn't have explicit drop patterns.

The remaining unmatched tokens fall into three groups:

| group | example | size |
|---|---|---|
| **Easy entity-table additions** | `KF_Atty_Def_Active`, `KF_Investigator`, `kf_Atty_Pros_Active_AgencyNum`, `KF_Atty_Pros_Inactive` | ~220 |
| **Prompt-variable shape (X.X)** | `PRCMeeting.DateofPRC`, `DateofCtOrder.DateofCourtOrder`, `DCNumber.DCNumber` | ~60 |
| **Long tail / exotic** | `JW_CaseStatusEvents.EventID`, `kf_Defendant.FBINum`, `JW_Wit_Addy.*` | ~400 |

## Plan

Five workstreams, ordered by leverage. Each item is a separate change so we can measure the contribution of each.

---

### 1. **Drop-list patterns** for `StateIDNum`, `Title`, `AttyProsTitle.NameAttributeValue`, `DCNumber`, `HisHer`, `HeShe` &nbsp;⭐ highest leverage

**What:** add patterns that match these JDA tokens and emit zero Pine outputs. Engine treats them as PROV_PATTERN with empty rewrite; eval ignores them; LLM is never asked.

**Expected impact:** removes ~440 LLM extras → precision rises substantially. Today these tokens are about a third of all LLM calls, so latency on late templates drops too.

**Pattern shape:**

```toml
[[pattern]]
id = "oba_drop_stateidnum"
match = "%[$entity.StateIDNum]"
rewrite = []          # empty list = "intentionally no Pine output"
[pattern.holes.entity]
kind = "path-segment"
```

The engine already supports multi-output rewrite; we just need empty-list behavior added to the rewriter (currently a list with zero elements would crash).

**Effort:** ~half a day. The patterns are mechanical; the engine change is small.

**Validation:** unit tests for each drop; full eval — precision should rise by 0.05+.

---

### 2. **Entity-table expansion + standard patterns for the K-family entities**

**What:** add `KF_Atty_Def_Active`, `KF_Atty_Pros_Active`, `KF_Atty_Pros_Inactive`, `KF_Investigator`, `kf_Defendant`, plus their `_Address` variants. Also `*_AgencyNum.CaseAgencyNumber → ProsNum.first.Number`.

**Expected impact:** ~220 unmatched tokens become deterministic translations. Recall jumps; LLM doesn't need to handle them.

**Effort:** ~2 hours. Mostly transforms-table edits + a few patterns for `AgencyNumber → ProsNum`.

---

### 3. **Prompt-variable patterns** for `X.X` (both segments equal)

**What:** add a generic pattern that matches `%[$name.$name]` (with both holes equal) and emits `@[$pine_name]` via a registered transform. Plus the explicit table for known names: `DateOfLetter`, `PRCMeeting`, `DateLetterReceived`, etc.

**Expected impact:** ~60 unmatched → mapped, with high confidence (these are mechanical).

**Effort:** ~half a day. The hole-equality match is a tiny new feature in the matcher; the rest is data.

**Risk:** false positives on truly-unmatched X.X tokens. Mitigate by raising `UnknownTransformInputError` when the name isn't tabulated, so the engine falls through.

---

### 4. **Pattern mining from the paired corpus** &nbsp;(promotion to deterministic patterns)

**What:** the corpus has 294 paired (legacy.rtf, pine.rtf) templates. Most JDA tokens in those templates have matching Pine tokens nearby — mineable. The `v2/engine/miner.py` already has a candidate generator that I haven't run against the latest patterns.

Run the miner, review the top 50 candidates by frequency, promote the high-confidence ones (those that show up in ≥10 templates with consistent rewrite).

**Expected impact:** unknown until we run it, but candidates will likely cover several of the remaining 400 long-tail tokens.

**Effort:** ~half a day to mine + review. Promotion is per-candidate decision (~5 min each).

**Risk:** mined candidates can be context-dependent; review must be careful. Don't auto-promote.

---

### 5. **Make the LLM more conservative + give it document context**

**What:** two prompt changes:
- (a) Update the rules to **explicitly list the drop set** (`StateIDNum`, `Title`, gender pronouns, etc.) so the LLM emits `<no mapping found>` for them. (Workstream 1 makes this redundant for those specific classes, but the same rule type applies to the long tail.)
- (b) Add a "DOCUMENT CONTEXT" section before the inputs listing the **dominant entity** of the template (e.g., "primary involvement: Complainant"). Compute it cheaply by counting which involvement entity appears most among the JDA tokens. This nudges the LLM away from "wrong-context" extras.

**Expected impact:** drops the 296 well-formed-but-wrong-context errors significantly. Even halving them is a 0.04 precision gain.

**Effort:** prompt changes are small (~1 hour), but careful evaluation needed.

**Risk:** larger prompt = slightly higher per-call cost; document-context computation is heuristic and could be wrong on multi-party templates.

---

## What I'd do first

**Workstream 1 alone**, then re-eval. It's mechanical, low-risk, and the data says it directly addresses ~40% of the precision-killing extras. After that, decide whether 2 + 3 + 4 + 5 are worth doing in sequence based on the F1 numbers from each step.

A reasonable target: **patterns-only macro F1 → 0.75** after workstreams 1-3, with the LLM contributing the last 0.05 once it has fewer junk-tokens to translate.

## What I would NOT do (yet)

- **Don't add auto-accept caching back.** The LLM is too eager; cached suggestions just propagate context-blind translations.
- **Don't add a learned classifier for "should this token be dropped".** A static drop list from the data is enough to capture 90% of the value.
- **Don't switch to a more expensive model.** GPT-4o-mini's syntactic outputs are already correct; the failure mode is over-eagerness, which a stronger model would also have.

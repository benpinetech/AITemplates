# LLM-suggested mappings

This directory is the runtime store for **LLM-derived mappings** that
have been accepted (or rejected) by the mapper. It's the closed-loop
half of the v2 design — see `../README.md` §4.3.

## Layout

```
suggestions/
├── verified/
│   └── <org>/
│       └── verified_<hash>.toml     # one accepted suggestion per file
└── rejected.log                      # JSONL audit log of rejected suggestions
```

## How it works

When the pipeline runs and an unmatched JDA token goes through the
LLM fallback, the resulting Pine output appears in the v2 GUI page as
an **LLM Suggestion** with **Accept** / **Reject** buttons.

- **Accept** writes a single-pattern TOML to `verified/<org>/`. The
  pattern has no holes — it's an exact-match cache for that JDA
  token. The pipeline loads `verified/<org>/*.toml` alongside the
  hand-authored library, so the next run matches this token
  deterministically (no LLM call, no risk of the LLM changing its
  mind).
- **Reject** appends a JSON record to `rejected.log` so we have an
  audit trail. Negative-shot prompting (feeding rejected examples
  back to the LLM) is a future option — right now rejections are
  just logged.

The store never modifies the hand-authored library. Verified
suggestions are layered *on top of* the library by the loader; you
can review them, delete them, or hand-edit them as needed without
touching the seed patterns.

## Promoting a verified suggestion to a generalized pattern

Verified suggestions are **exact matches** — they only fire on the
literal token they were derived from. To turn one into a generalized
pattern with `$entity` holes that covers many similar tokens, hand-
edit the TOML (or use a future `promote-to-pattern` action) and move
the file under `../patterns/library/<org>/`.

## File naming

`verified_<hash>.toml` where `<hash>` is the first 8 hex chars of
sha1(jda_text + "→" + pine_text). Stable across runs — accepting the
same suggestion twice overwrites the same file (idempotent).

## Privacy

Files in this directory contain only bracketed expression text, not
template prose. The privacy invariant holds at this layer too — none
of the on-disk artifacts contain document content beyond the
bracketed JDA / Pine syntax the user has explicitly authorized.

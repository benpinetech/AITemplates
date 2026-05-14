# Verified-suggestion store

Runtime on-disk store for **converter-verified mappings**. Each
accepted suggestion is one tiny TOML file the pattern loader picks up
at conversion time — the next run matches that input deterministically
with no LLM call.

The store is the closed-loop half of the HITL workflow in
`Agent/converter_app/`. Editing a token in the inline popup and
clicking **Save edit + persist** writes one of these files. The
[engine README](../engine/README.md) covers the API
(`suggestion_store.accept_suggestion` / `load_verified_for_org`); this
README covers the on-disk layout.

## Layout

```
suggestions/
├── verified/
│   └── <org>/
│       ├── global/
│       │   └── verified_<hash>.toml          ← always re-applies
│       ├── by_template/
│       │   └── <template_filename.rtf>/
│       │       └── verified_<hash>.toml      ← only on that template
│       └── by_audience/
│           └── <complainant|respondent>/
│               └── verified_<hash>.toml      ← only when audience matches
└── rejected.log                              ← JSONL audit of rejections
```

`<hash>` is the first 8 hex chars of
`sha1(scope_kind:scope_value:jda_text→pine_text)`. Stable across runs
— re-accepting the same mapping at the same scope overwrites the same
file (idempotent). Different scopes for the same (jda, pine) get
different files (no collision).

## Scope semantics

When the converter clicks **Save edit + persist**, they pick one:

| Scope | Re-applies on | Use when |
|---|---|---|
| `this template` | only templates with the same filename | the edit is specific to this letter (e.g. an else-branch's prompt variable that maps differently than the if-branch's data field) |
| `this audience` | only templates the audience classifier labels with the same value (`complainant`, `respondent`) | the edit reflects an audience-specific convention (e.g. `Cust_Address` binds differently in letters to the complainant vs the respondent) |
| `global` | every template, ever | the mapping is a universal truth (e.g. a `KF_*` prefix the LLM has been bailing on) — use sparingly; this is the documented context-blind cache hazard |

The loader at conversion time only walks the scope subdirs that match
the current document, so a suggestion written under
`by_template/Letter_to_C.rtf/` is invisible when the converter opens
`Letter_to_R.rtf`. The inline-edit popup defaults to the narrowest
available scope (template > audience > global) precisely to avoid
over-broad persistence.

## Mapping shapes

The store handles every shape the pattern engine supports:

| TOML on disk | Shape | Notes |
|---|---|---|
| `match = '%[X.A]'` / `rewrite = '@[Y.A]'` | 1:1 | the common case |
| `match = '%[X.FullName]'` / `rewrite = ['@[Y.first.NameFirstName]', '@[Y.first.NameLastName]']` | 1:N | bare-name splitting |
| `match = ['%[A]', '%[B]']` / `rewrite = ['@[A2]', '@[B2]']` | N:M | literal chunk pattern |
| `match = '%[Cust_OBAAttorney.Title]'` / `rewrite = []` | 1:0 (drop) | consume JDA, emit nothing |

The converter never has to choose the shape — `accept_suggestion(jda,
pine, …)` accepts a string OR a list for either side and figures out
the TOML form automatically.

## Priority

Pattern priority in the engine controls match precedence (higher
wins). The store assigns:

- `global` suggestions: priority **150** — above the seed library
  (default 100), so a verified mapping wins on its JDA shape
- `template` and `audience` scoped suggestions: priority **500** —
  significantly above seed patterns, so an in-scope override beats a
  generic pattern that would otherwise fire

## Rejections

`reject_suggestion` appends a JSON record to `rejected.log`:

```json
{"timestamp": "...", "org": "oba", "jda": "%[...]", "pine": "@[...]",
 "reason": "", "source_template": "...", "source_segment_index": 4}
```

Rejections are audit-only today — they don't influence future LLM
calls. Negative-shot prompting from this log is a future option.

## Privacy

Files in this directory contain only bracketed JDA / Pine syntax — no
template prose. The privacy invariant from the LLM path extends here:
nothing on disk reveals document content beyond what the converter
has explicitly persisted.

## Migrating

Pre-scope files (a flat layout from earlier versions where
`verified/<org>/verified_<hash>.toml` had no scope subdir) still
load. The loader treats them as `global`-scope. Migration is silent;
no action required.

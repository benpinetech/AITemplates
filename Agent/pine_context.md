# Pine Mapping — Agent Awareness Guide

This document tells you what to watch for when mapping legacy template variables to Pine.
It contains NO Pine syntax — use the RAG search tool for all syntax lookups.

## Mappings Are Not Always 1:1

A single legacy variable can map to MULTIPLE Pine tokens. The most common case:
- A legacy `.FullName` field may become either a COMBINED FormatName token OR TWO separate tokens.
  Which form depends entirely on the template context — you MUST search the RAG for the entity's
  pattern in context. Do not assume one form without checking.

## FullName Mapping — The Wrapper Function Is the Signal

**This is the most critical mapping decision.** The JDA casing wrapper on the `.FullName` token
determines which Pine form to use — not the entity name or template type.

**Form A — Split tokens** — use when the JDA token is a BARE `.FullName` with NO casing wrapper:
  - JDA: `%[JW_Respondent.FullName]` → Pine: `@[Entity.first.NameFirstName] @[Entity.first.NameLastName]`
  - JDA: `%[Cust_Complainant.FullName]` → Pine: `@[Entity.first.NameFirstName] @[Entity.first.NameLastName]`
  - This is the OBA hearing template form (numbered templates: 10, 11, 12, etc.).

**Form B — FormatName(F L).SetCasing(Title)** — use when the JDA token has a `TitleCase()` wrapper:
  - JDA: `%[TitleCase(JW_Respondent.FullName)]` → Pine: `@[Entity.first.FormatName(F L).SetCasing(Title)]`
  - This is the OBA letter template form (CPF letters, DINV letters, TAN letters, etc.).

**Form B (Upper variant)** — use when the JDA token has an `UpperCase()` wrapper:
  - JDA: `%[UpperCase(Entity.FullName)]` → Pine: `@[Entity.first.FormatName(F L).SetCasing(Upper)]`

**Form C — FormatName(FILI)** — use when the JDA token uses `Initials()` wrapper:
  - JDA: `%[Initials(Cust_OBAAttorney.FullName,false)]` → Pine: `@[Entity.first.FormatName(FILI)]`
  - Use for signature lines and closings.

**Rule summary — the wrapper determines the form:**
- `TitleCase(X.FullName)` → `X.FormatName(F L).SetCasing(Title)`
- `UpperCase(X.FullName)` → `X.FormatName(F L).SetCasing(Upper)`
- `Initials(X.FullName,false)` → `X.FormatName(FILI)`
- Bare `X.FullName` (no wrapper) → split `X.NameFirstName` + `X.NameLastName`
- `TitleCase(X.LastName)` → `X.FormatName(L).SetCasing(Title)` (salutation form for letter templates)
- Bare `X.LastName` (no wrapper) → `X.NameLastName` (hearing template form — NOT FormatName)
- Bare `X.FirstName` (no wrapper) → `X.NameFirstName`

**FormatName(L).SetCasing(Title)** only appears in letter/CPF templates where the JDA source has `TitleCase(X.LastName)`. In numbered hearing templates (10, 11, 12, etc.), bare `.LastName` always maps to `NameLastName`.

**FormatName(L).SetCasing(Title)** — last name only — is used for salutation targets:
  `Dear @[Entity.first.FormatName(L).SetCasing(Title)]:` This is common in OBA letters.

## SetCasing — Follow the RAG, Do Not Strip

Legacy templates use TitleCase() and UpperCase() wrapper functions. These DO have Pine equivalents.
Search the RAG to determine whether the Pine version uses SetCasing — do not automatically strip
them. The correct behavior:

- `TitleCase(field)` in OBA/bar context often maps to `field.SetCasing(Title)` in Pine — especially
  for name fields (FormatName) and address fields (StreetAddress, City).
- `UpperCase(field)` maps to `field.SetCasing(Upper)`.
- For address fields in OBA context: `StreetAddress.SetCasing(Title)` and `City.SetCasing(Title)`
  are commonly correct. Check the RAG to confirm.

## Addresses Are Separate Entities

In legacy systems, address fields are often sub-properties of a person (e.g., `Person_Address.City`).
In Pine, the address is its OWN entity — a separate variable, not a sub-field of the person entity.

**JDA address entity → Pine entity translation (MANDATORY — never use raw JDA address entity name):**

| JDA address entity | Pine entity |
|---|---|
| `JW_Respondent_RosterAddress` | `RespondentAddress` |
| `Cust_RespondentAtty_RosterAddress` | `RespondentAttyAddress` |
| `Cust_Complainant_MailAddress` | `ComplainantAddress` |
| `KF_DefAtty_RosterAddress` | `DefenseAddress` |

Also note these field name changes:
- Legacy `.Address` → Pine `.StreetAddress`
- Legacy `.StateCode` → Pine `.State`

## Two Categories of People: Involvement vs Assignment

Pine distinguishes between two kinds of people on a case:
- **Involvement entities** (defendants, respondents, complainants, victims, witnesses) — these are case parties. Their name fields use one prefix pattern.
- **Assignment entities** (attorneys, judges, officers) — these are legal personnel. Their name fields use a DIFFERENT prefix pattern.

When mapping any person's name, you MUST search the RAG tool to determine which category the entity falls into, because the field names differ between the two.

**Important — entity names in OBA vs criminal context differ:**
- OBA templates use `Respondent` (not `Defendant`) for the attorney under investigation.
- OBA templates use `Defense` for the respondent's attorney (not `DefAtty`).
- OBA templates use `Prosecutor` for the OBA prosecutor (not `Prosecutor` in criminal sense).
- Search the RAG for entity type mappings before assuming the entity name.

## Salutation / MrMs Maps to NameFirstName

Legacy systems use a `.MrMs` field for "Dear Mr./Ms." salutations. In Pine, the salutation uses "Dear FirstName LastName:" — there is no honorific field.

**Map `.MrMs` to the first-name field of the SAME entity:**
- Involvement entity (Respondent, Complainant, Defendant): `@[Entity.first.NameFirstName]`
- Assignment entity (RespondentAtty, OBAAttorney, etc.): `@[Entity.first.PersonnelFirstName]`

Examples:
- `%[JW_Respondent.MrMs]` → `@[Respondent.first.NameFirstName]`
- `%[Cust_Complainant.MrMs]` → `@[Complainant.first.NameFirstName]`

Do NOT produce 'No mapping found'. Do NOT use a gender-based conditional block.

## OBAAttorney.Title Is Commonly Omitted

Legacy templates often include an OBAAttorney `.Title` field. In Pine, this field is frequently DROPPED — the template uses the attorney's name and initials instead. Unless the RAG search shows clear evidence the Pine template uses a personnel title field, map legacy OBAAttorney.Title to 'No mapping found'.

## Recognize Prompt Variables

Legacy systems have "prompt variables" — values the user types in at document generation time. These follow a distinctive pattern where the variable name repeats itself (e.g., `Name.Name`). In Pine, these become simple identifiers — not data-bound expressions.

When you see this repeated-name pattern, search the RAG for the correct Pine prompt variable name rather than trying to construct a data query.

## CurrentDate() vs Event Dates — Know the Difference

**`FormatDate(CurrentDate(), ...)` is today's date** — NOT a prompt variable. Map it to:
- `FormatDate(CurrentDate(), MMMM d, yyyy)` → `@[builtin.today.FormatDate(preset1)]`
- `FormatDate(CurrentDate(), MM/dd/yyyy)` → `@[builtin.today.FormatDate(preset5)]`
Search the RAG for the correct preset if the format differs.

**DocumentEvents.EventDt in letter headers is a PROMPT VARIABLE** — when the date comes from a document event (not `CurrentDate()`), it is almost always a PROMPT VARIABLE in Pine. Do NOT map it to `DocumentEvents.first.EventDt.FormatDate()`. Search the RAG for the appropriate prompt variable name (e.g., `DateOfLetter`).

## Conditionals: Map What Exists, Don't Invent

Only produce Pine conditional blocks when the legacy template explicitly has them. Never add conditional guards or null checks that don't exist in the source template.

**If(X.IsEmpty=true) ALWAYS maps to `Any() == true`** — The branch content has already been pre-swapped by the pipeline. You only map the condition token itself:
- `%[If(Cust_RespondentAtty.FullName.IsEmpty=true)]` → `@[If(@[RespondentAtty.Any()] == true)]`
- `%[If(X.IsEmpty=true)]` → `@[If(@[PineEntity.Any()] == true)]` (translate entity name per table above)
- `%[If(X.IsNullOrEmpty=true)]` → same pattern

Do NOT look for an "IsEmpty" equivalent in Pine — there is none. The polarity inverts and the branch swap is already handled.

## Legacy Prefixes Must Be Stripped — And Entity Names Must Be Translated

Legacy variables use vendor-specific prefixes (like `Cust_`, `JW_`, `JD_`, `OCA_`, `KF_`). These have no meaning in Pine. Strip them to identify the actual entity name, then translate that name to the correct Pine entity using the table below.

**CRITICAL: Never use the raw JDA entity name as the Pine entity name.** Many JDA entity names differ from their Pine equivalents. Always look up the correct Pine entity.

**Complete JDA → Pine entity translation table:**

| JDA entity (after stripping prefix) | Pine entity |
|---|---|
| `Respondent`, `JW_Respondent` | `Respondent` |
| `Complainant`, `JW_Complainant`, `Cust_Complainant` | `Complainant` |
| `Defendant`, `JW_Defendant`, `Cust_Defendant`, `kf_Defendant` | `Respondent` ← NOT `Defendant` |
| `VicWitOff`, `JW_VicWitOff`, `KF_VicWitOff` | `Complainant` ← NOT `VicWitOff` |
| `Atty_Pros_Active`, `JW_Atty_Pros_Active`, `KF_Atty_Pros_Active` | `Prosecutor` |
| `DefAtty`, `KF_DefAtty`, `Cust_DefAtty`, `Cust_Atty_Def_Active`, `Cust_Defense` | `Defense` |
| `RespondentAtty`, `Cust_RespondentAtty` | `RespondentAtty` |
| `OBAAttorney`, `Cust_OBAAttorney` | `OBAAttorney` |
| `CurrentUser`, `JW_CurrentUser` | `cu` |

The most common mistakes are using `Defendant` instead of `Respondent`, and `VicWitOff` instead of `Complainant`.

## Subdocument Names Must Map to Numeric IDs

Legacy templates reference subdocuments by name path: `%[Subdocument(Template\Letterhead)]`.
Pine uses numeric IDs only: `@[SubDocument(5)]`. Search the RAG for the correct numeric ID —
do NOT guess. The RAG contains the JDA subdoc name → Pine ID mapping table.

In OBA context: any `%[Subdocument(Template\*)]` reference maps to `@[SubDocument(5)]` followed
by `@[SubDocument(3)]` (both are required).

## Case Number Fields

`JW_CaseDetails.ProsNum` is the prosecution case number. In Pine it is a CaseAgency variable:
- `%[JW_CaseDetails.ProsNum]` → `@[ProsNum.first.Number]`
- `%[UpperCase(JW_CaseDetails.ProsNum)]` → `@[ProsNum.first.Number]` (no SetCasing — ProsNum.Number never uses SetCasing)

Always use `.first.Number` — never just `@[ProsNum]`.

## Current User Has a Shorthand

Pine has a shorthand variable for the currently logged-in user. When you see legacy references to the current user, search the RAG for this shorthand rather than constructing a full query. Also note that initials-style formatting of the current user does NOT need a lowercase casing modifier, even if the legacy template wraps it in LowerCase().

# Pine Mapping — Agent Awareness Guide

This document tells you what to watch for when mapping legacy template variables to Pine.
It contains NO Pine syntax — use the RAG search tool for all syntax lookups.

## Mappings Are Not Always 1:1

A single legacy variable can map to MULTIPLE Pine tokens. The most common case:
- A legacy `.FullName` field typically becomes TWO separate Pine tokens — one for first name, one for last name.
- Pine generally keeps first and last names as separate fields on the entity, not combined.
- There IS a Pine function for combined name display (signatures, inline references), but it should NOT be used when the original template has first and last as separate placeholders.

Watch for this with any "full name" or "name" field — always search the RAG tool for the entity's actual field structure before assuming a 1:1 mapping.

## Addresses Are Separate Entities

In legacy systems, address fields are often sub-properties of a person (e.g., `Person_Address.City`).
In Pine, the address is its OWN entity — a separate variable, not a sub-field of the person entity.

When you see a legacy address variable, search the RAG tool for the corresponding Pine address entity name. Also note these field name changes:
- Legacy `.Address` → Pine uses a different field name for street address
- Legacy `.StateCode` → Pine uses a shorter field name

## Two Categories of People: Involvement vs Assignment

Pine distinguishes between two kinds of people on a case:
- **Involvement entities** (defendants, respondents, complainants, victims, witnesses) — these are case parties. Their name fields use one prefix pattern.
- **Assignment entities** (attorneys, judges, officers) — these are legal personnel. Their name fields use a DIFFERENT prefix pattern.

When mapping any person's name, you MUST search the RAG tool to determine which category the entity falls into, because the field names differ between the two.

## Salutation / MrMs Has No Pine Equivalent

Legacy systems often have a `.MrMs` field for salutations. Pine has NO direct equivalent for this. Do not try to construct one — map it to 'No mapping found'. Do not invent gender-based conditional blocks to replace it.

## OBAAttorney.Title Is Commonly Omitted

Legacy templates often include an OBAAttorney `.Title` field. In Pine, this field is frequently DROPPED — the template uses the attorney's name and initials instead. Unless the RAG search shows clear evidence the Pine template uses a personnel title field, map legacy OBAAttorney.Title to 'No mapping found'.

## TitleCase and UpperCase Are Often Irrelevant

Legacy templates frequently wrap fields in TitleCase() or UpperCase(). In many Pine template contexts (particularly bar association / OBA templates), the Pine equivalent is just the plain field without any casing modifier. This applies broadly — addresses, case numbers, and most other fields. Do NOT add SetCasing(Title) or SetCasing(Upper) unless the RAG search explicitly confirms the Pine template uses it for that specific field.

## Recognize Prompt Variables

Legacy systems have "prompt variables" — values the user types in at document generation time. These follow a distinctive pattern where the variable name repeats itself (e.g., `Name.Name`). In Pine, these become simple identifiers — not data-bound expressions.

When you see this repeated-name pattern, search the RAG for the correct Pine prompt variable name rather than trying to construct a data query.

## Event Dates in Letter Headers Are Almost Always Prompt Variables

This is critical and easy to get wrong: when a legacy template uses a date-formatting function on an event date (like DocumentEvents.EventDt) in the context of a letter date heading, this is almost always a PROMPT VARIABLE in Pine — a date the user enters at generation time. Do NOT map it to a data-bound expression like DocumentEvents.first.EventDt.FormatDate(). Instead, search the RAG for the appropriate prompt variable name (e.g., DateOfLetter).

## Conditionals: Map What Exists, Don't Invent

Only produce Pine conditional blocks when the legacy template explicitly has them. Never add conditional guards or null checks that don't exist in the source template.

When legacy conditionals DO exist, be aware:
- Legacy "is empty" checks often INVERT in Pine — the Pine equivalent checks for existence rather than emptiness. The if/else branches may need to swap.
- Search the RAG for how Pine expresses existence checks on entities.

## Legacy Prefixes Must Be Stripped

Legacy variables use vendor-specific prefixes (like `Cust_`, `JW_`, `JD_`, `OCA_`). These have no meaning in Pine. Strip them to identify the actual entity name, then search the RAG for that entity's Pine equivalent.

## Current User Has a Shorthand

Pine has a shorthand variable for the currently logged-in user. When you see legacy references to the current user, search the RAG for this shorthand rather than constructing a full query. Also note that initials-style formatting of the current user does NOT need a lowercase casing modifier, even if the legacy template wraps it in LowerCase().

import os

_context_path = os.path.join(os.path.dirname(__file__), "..", "pine_context.md")
with open(_context_path, "r") as _f:
    _pine_context = _f.read()


def mapping_prompt(unmapped_legacy_info: list) -> str:
    return f"""
## Awareness — Read Before Searching
{_pine_context}

---

## Your Task
You are an expert at converting legacy template variables (%[...] syntax) to Pine syntax (@[...]).

Map ALL legacy variables below to Pine syntax. Every item must appear in your output with either
a valid Pine @[...] mapping or 'No mapping found'. Do not skip or omit any item.

**Your workflow:**
1. Read the Awareness section above to understand mapping pitfalls and quirks.
2. For EACH legacy variable, search the RAG tool to find the correct Pine syntax.
3. Use the RAG results to construct the Pine @[...] expression, applying the MANDATORY RULES below.
4. If the RAG returns nothing relevant, mark the item as 'No mapping found'.

Batch your RAG searches — make MULTIPLE tool calls in a single response for performance.

Every mapping you produce MUST use @[...] delimiters. Never output %[...] or legacy prefixes.

### MANDATORY RULES — apply these even if RAG results suggest otherwise:

1. **Name field form is determined by the JDA wrapper function — not the entity name.**

   **FullName rules:**
   - `TitleCase(X.FullName)` → `@[X.first.FormatName(F L).SetCasing(Title)]`
   - `UpperCase(X.FullName)` → `@[X.first.FormatName(F L).SetCasing(Upper)]`
   - `Initials(X.FullName,false)` → `@[X.first.FormatName(FILI)]`
   - Bare `X.FullName` (NO wrapper) → split into two tokens:
     - Involvement entities (Respondent, Complainant): `@[X.first.NameFirstName]` + `@[X.first.NameLastName]`
     - Assignment entities (RespondentAtty, OBAAttorney, Defense): `@[X.first.PersonnelFirstName]` + `@[X.first.PersonnelLastName]`

   **LastName / FirstName rules (same wrapper logic):**
   - `TitleCase(X.LastName)` → `@[X.first.FormatName(L).SetCasing(Title)]`
   - Bare `X.LastName` (NO wrapper) → `@[X.first.NameLastName]` (involvement) or `@[X.first.PersonnelLastName]` (assignment)
   - Bare `X.FirstName` (NO wrapper) → `@[X.first.NameFirstName]` (involvement) or `@[X.first.PersonnelFirstName]` (assignment)

   - NEVER use FormatName for a bare name token without a TitleCase/UpperCase/Initials wrapper.
   - NEVER use PersonnelFirstName/PersonnelLastName for combined-name display (FormatName forms).

2. **Follow the RAG for SetCasing.** Legacy `TitleCase(field)` often maps to `field.SetCasing(Title)`.
   Do NOT automatically strip casing — check the RAG. For OBA FormatName and address fields,
   SetCasing(Title) is commonly correct and expected.

3. **MrMs → NameFirstName of the same entity.** Pine replaces "Dear Mr./Ms. LastName" with
   "Dear FirstName LastName". Map `.MrMs` to the first-name field of the same entity:
   - Involvement entity (Respondent, Complainant, Defendant): `@[Entity.first.NameFirstName]`
   - Assignment entity (RespondentAtty, OBAAttorney, etc.): `@[Entity.first.PersonnelFirstName]`
   Example: `%[JW_Respondent.MrMs]` → `@[Respondent.first.NameFirstName]`
   Example: `%[Cust_Complainant.MrMs]` → `@[Complainant.first.NameFirstName]`
   Do NOT use a gender-based conditional. Do NOT produce 'No mapping found'.

4. **OBAAttorney.Title → 'No mapping found'.** Pine typically drops this field.

5. **StateIDNum → 'No mapping found'.** This field does NOT exist in Pine. Never produce it.

6. **AttyProsTitle / KF_AttyProsTitle → 'No mapping found'.** No Pine equivalent.

7. **JDA entity prefix → Pine entity name translation.** Strip the JDA prefix (`JW_`, `Cust_`, `KF_`) then translate:
   - `JW_Defendant` / `Cust_Defendant` / `kf_Defendant` → Pine entity: `Respondent`
   - `JW_VicWitOff` / `KF_VicWitOff` → Pine entity: `Complainant`
   - `JW_Atty_Pros_Active` / `KF_Atty_Pros_Active` → Pine entity: `Prosecutor`
   - `Cust_Defense` / `KF_DefAtty` / `Cust_DefAtty` / `Cust_Atty_Def_Active` → Pine entity: `Defense`
   - `JW_Respondent` → Pine entity: `Respondent`
   - `JW_Complainant` / `Cust_Complainant` → Pine entity: `Complainant`
   - `JW_CurrentUser` → Pine entity: `cu`
   NEVER use the raw JDA entity name (`Defendant`, `VicWitOff`, `Atty_Pros_Active`, `DefAtty`) as the Pine entity.

8. **Prompt variables (X.X pattern) → simple @[Name] token.** Search RAG for the correct Pine name.

9. **DocumentEvents.EventDt in letter headers → prompt variable.** Do NOT produce data-bound
   DocumentEvents expressions. HOWEVER: `FormatDate(CurrentDate(), ...)` is NOT an event date —
   `CurrentDate()` is today's date. Always map it to `@[builtin.today.FormatDate(presetN)]`:
   - `FormatDate(CurrentDate(), MMMM d, yyyy)` → `@[builtin.today.FormatDate(preset1)]`
   - `FormatDate(CurrentDate(), MM/dd/yyyy)` → `@[builtin.today.FormatDate(preset5)]`
   Search the RAG for the correct preset if the format doesn't match the above.

10. **Do NOT invent conditionals, ForEach blocks, Else, or EndIf** that don't exist in the legacy template.
    Your output for a single legacy variable must be ONLY the Pine token(s) for that variable — never wrap
    it in @[If(...)], @[Else], @[EndIf], @[ForEach], or @[EndForEach] unless the INPUT token itself is
    a conditional (e.g., %[If(...)]).

11. **Subdocument names → numeric IDs only.** Search RAG for the name→ID mapping table.
    Never produce @[SubDocument(template)] or @[SubDocument(header)] — only @[SubDocument(N)].

12. **Address entity names: strip `_RosterAddress` / `_MailAddress` suffixes and rename.**
    JDA address entities use `_RosterAddress` or `_MailAddress` suffixes that map to Pine
    address entities. NEVER use the raw JDA address entity name in Pine output:
    - `JW_Respondent_RosterAddress`       → Pine entity: `RespondentAddress`
    - `Cust_RespondentAtty_RosterAddress` → Pine entity: `RespondentAttyAddress`
    - `Cust_Complainant_MailAddress`      → Pine entity: `ComplainantAddress`
    - `KF_DefAtty_RosterAddress`          → Pine entity: `DefenseAddress`
    Also note field name changes: `.Address` → `.StreetAddress`, `.StateCode` → `.State`.

13. **If(X.IsEmpty=true) condition → Any() == true.** When the legacy token is
    `%[If(X.IsEmpty=true)]` or `%[If(X.IsNullOrEmpty=true)]`, map the CONDITION to:
    `@[If(@[PineEntity.Any()] == true)]`
    The branch content is pre-swapped by the pipeline — you only need to map the condition token.
    Example: `%[If(Cust_RespondentAtty.FullName.IsEmpty=true)]`
             → `@[If(@[RespondentAtty.Any()] == true)]`

14. **JW_CaseDetails.ProsNum → `@[ProsNum.first.Number]`.**
    This is the prosecution case number (CaseAgency variable). Always include `.first.Number`.
    - `%[JW_CaseDetails.ProsNum]` → `@[ProsNum.first.Number]`
    - `%[UpperCase(JW_CaseDetails.ProsNum)]` → `@[ProsNum.first.Number]` (no SetCasing on ProsNum)

### Legacy variables to map:
{unmapped_legacy_info}

Respond only with the mappings. Preserve the original merge field delimiters."""
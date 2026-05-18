# Pine idioms — narrative reference

Plain-language reference for common Pine template patterns. Sourced
from sections 26, 27, and 29 of `pine_syntax_ground_truth.txt`.
This file is intentionally markdown rather than TOML — it's for
humans to read and for the LLM fallback (Phase 4) to retrieve as
few-shot examples by similarity.

When you add a pattern that exemplifies a common idiom, mirror the
example here so future maintainers and the LLM see both the
executable form (in `../patterns/library/`) and the narrative form.

---

## OBA / bar association idioms (§26)

Standard letter header — most OBA letters open with two SubDocuments,
then today's date:

```pine
@[SubDocument(5)]
@[SubDocument(3)]
@[builtin.today.FormatDate(preset1)]
```

Address block using FormatName for the recipient:

```pine
@[Respondent.first.FormatName(F L).SetCasing(Title)]
@[RespondentAddress.first.StreetAddress]
@[RespondentAddress.first.City], @[RespondentAddress.first.State] @[RespondentAddress.first.Zip]

Re: @[Complainant.first.FormatName(F L).SetCasing(Title)]
Prosecution No.: @[ProsNum.first.Number]
```

Closing block:

```pine
Sincerely,
@[OBAAttorney.first.FormatName(FILI)]
@[cu.FormatName(FILI)]
@[OBAAttorney.first.PersonnelFirstName] @[OBAAttorney.first.PersonnelLastName]
```

Conditional respondent-attorney address block:

```pine
@[If(@[RespondentAtty.Any()] == true)]
  @[RespondentAtty.first.PersonnelFirstName] @[RespondentAtty.first.PersonnelLastName]
  @[RespondentAttyAddress.first.StreetAddress]
  @[RespondentAttyAddress.first.City], @[RespondentAttyAddress.first.State] @[RespondentAttyAddress.first.Zip]
@[EndIf]
```

Gender pronoun block using a pre-loaded Name record:

```pine
@[CreateVar(@RespondentInfo, @Name.GetByID(@[Respondent.first.NameID]))]
@[If('@[RespondentInfo.Gender]' == 'M')]his@[ElseIf('@[RespondentInfo.Gender]' == 'F')]her@[Else]his/her@[EndIf]
@[If('@[RespondentInfo.Gender]' == 'M')]Mr.@[ElseIf('@[RespondentInfo.Gender]' == 'F')]Ms.@[Else]Mr./Ms.@[EndIf]
```

Phone iteration with type filter:

```pine
@[RespondentPhone.ForEach(RP)]
  @[If(@[RP.Type] == 'W')]@[RP.PhoneNumber.FormatPhoneNumber()]@[EndIf]
@[RespondentPhone.EndForEach]
```

---

## Criminal / public-defender idioms (§27)

Defendant block:

```pine
@[CreateVar(@Defendant, @CaseInvolvement.GetByQuery("CaseID":@[builtin.CaseID],"Type":"DEF","IsActive":true))]
@[Defendant.first.FormatName(F M L)]
@[Defendant.first.FormatName(F M L).SetCasing(Upper)]
@[CreateVar(@DefName, @Name.GetByID(@[Defendant.first.NameID]))]
@[if('@[DefName.Gender]'=='M')]Mr.@[elseif('@[DefName.Gender]'=='F')]Ms.@[else]Mr./Ms.@[endif]
```

Charges with Oxford-style comma list:

```pine
@[CreateVar(@Charges, @CaseCharge.GetByQuery("CaseID":@[builtin.CaseID],"OrderBy":"CountNumber"))]
@[Charges.ForEach(c)]
  @[if(@[c.CaseChargeID]==@[Charges.First().CaseChargeID])]
    COUNT @[c.CountNumber]: @[c.SystemStatuteOffense] a @[c.Severity.GetLabel(217)], in violation of MCA § @[c.SystemStatuteCode]
  @[elseif(@[c.CaseChargeID]==@[Charges.Last().CaseChargeID])], and COUNT @[c.CountNumber]: @[c.SystemStatuteOffense]
  @[else], COUNT @[c.CountNumber]: @[c.SystemStatuteOffense]
  @[endif]
@[Charges.EndForEach]
```

Defense attorney with fallback to defendant directly:

```pine
@[if(@[DefAtty.Any()] = true)]
  @[DefAtty.first.FormatName(F M L)]
  @[DefAttyAddress.first.StreetAddress]
  @[DefAttyAddress.first.City], @[DefAttyAddress.first.State] @[DefAttyAddress.first.Zip]
  @[Defendant.first.FormatName(F M L)] c/o counsel
@[else]
  @[Defendant.first.FormatName(F M L)]
  @[DefAddress.first.StreetAddress]
  @[DefAddress.first.City], @[DefAddress.first.State] @[DefAddress.first.Zip]
@[endif]
```

Future-event filter:

```pine
@[CreateVar(@FutureEvents, @Event.GetByQuery("CaseID":@[builtin.CaseID],"Status":"P","StartDateGreaterThan":@[builtin.Today]))]
@[FutureEvents.ForEach(e)]@[if(@[e.Type]=='JURYW')]@[e.StartDate.FormatDate(preset1)]@[else]@[endif]@[FutureEvents.EndForEach]
```

---

## Edge cases and syntax notes (§29)

**Casing is flexible.** `@[if()]`, `@[If()]`, `@[IF()]` are equivalent.
`.first` and `.First()` are equivalent. Pine's parser is
case-insensitive in practice; the unparser canonicalises to one form.

**Equality has two spellings.** Both `==` and `=` are accepted. `==`
is preferred for string comparisons in modern templates; `=` is
common in older ones.

**ForEach loop variable does not take .first.** Inside
`@[Charges.ForEach(c)]`, access fields directly as
`@[c.SystemStatuteOffense]` — `@[c.first.SystemStatuteOffense]` is
wrong.

**EndForEach has two forms.** `@[Collection.EndForEach]` (the standard
method form) and `@[EndForEach]` (the standalone form, paired with
`@[Foreach(x IN y)]`).

**CaseID supports two notations** in query parameters:
`"CaseID":@[builtin.CaseID]` (standard) and
`"CaseID":[@[builtin.CaseID]]` (array-wrapped — also valid).

**`builtin.Today` and `builtin.today` are interchangeable.** So is
`builtin.CaseID` vs `builtIn.CaseID`. Casing is flexible for builtin
prefixes too.

**Some CreateVar declarations omit the leading `@`** before the data
source — e.g.
`@[CreateVar(@JudgeDiv, PersonnelElement.GetByQuery(...))]`. Both
forms appear and both are accepted. The canonical form has the `@`.

**Direct field access on a `.first`-fetched CreateVar** doesn't need
`.first` again — `@[CreateVar(@Sentence, ...).first]` lets you write
`@[Sentence.FieldName]` afterward without `.first`.

**`FormatName` works directly on a CaseAssignment loop variable.**
Inside `@[EA.ForEach(Assign)]`, write `@[Assign.FormatName(F M L)]`
without first looking up the Personnel record.

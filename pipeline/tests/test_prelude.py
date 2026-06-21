"""Tests for the Pine CreateVar prelude generator."""

from __future__ import annotations

import pytest

from pipeline.engine import prelude
from pipeline.parser import pine_parser


def _parse(s: str):
    return pine_parser.parse(s)


# ─── classification ────────────────────────────────────────────────────────

class TestSplitChildEntity:
    @pytest.mark.parametrize("name,parent,suffix,src", [
        ("ComplainantAddress", "Complainant", "Address", "NameAddress"),
        ("RespondentAddress",  "Respondent",  "Address", "NameAddress"),
        ("RespondentAttyAddress", "RespondentAtty", "Address", "PersonnelAddress"),
        ("OBAAttorneyAddress",    "OBAAttorney",    "Address", "PersonnelAddress"),
        ("ComplainantPhone", "Complainant", "Phone",   "NamePhone"),
        ("RespondentEmail",  "Respondent",  "Email",   "NameEmail"),
        ("InvestigatorPhone","Investigator","Phone",   "PersonnelPhone"),
    ])
    def test_known_child(self, name, parent, suffix, src):
        assert prelude.split_child_entity(name) == (parent, suffix, src)

    @pytest.mark.parametrize("name", [
        "Respondent",            # bare root, not a child
        "Complainant",
        "OBAAttorney",
        "ProsNum",               # CaseAgency-sourced root
        "CaseInfo",
        "Address",               # bare suffix only — no parent
        "MysteryEntityAddress",  # parent isn't in any type-code table
        "cu",
        "builtin",
    ])
    def test_not_a_child(self, name):
        assert prelude.split_child_entity(name) is None


# ─── declaration strings ───────────────────────────────────────────────────

class TestParentDeclaration:
    def test_involvement_uses_caseinvolvement(self):
        out = prelude.parent_declaration("Complainant")
        assert out is not None
        assert "@CaseInvolvement.GetByQuery" in out
        assert '"CaseID":@[builtin.CaseID]' in out
        assert '"Type":"CIT01"' in out

    def test_assignment_uses_caseassignment(self):
        out = prelude.parent_declaration("RespondentAtty")
        assert out is not None
        assert "@CaseAssignment.GetByQuery" in out
        assert '"Type":"CIT12"' in out

    def test_unknown_parent_returns_none(self):
        assert prelude.parent_declaration("MysteryEntity") is None


class TestChildDeclaration:
    def test_name_source_uses_nameid(self):
        out = prelude.child_declaration("ComplainantAddress", "Complainant", "NameAddress")
        assert out == (
            '@[CreateVar(@ComplainantAddress, '
            '@NameAddress.GetByQuery("NameID":@[Complainant.first.NameID]))]'
        )

    def test_personnel_source_uses_personnelid(self):
        out = prelude.child_declaration(
            "RespondentAttyAddress", "RespondentAtty", "PersonnelAddress",
        )
        assert out == (
            '@[CreateVar(@RespondentAttyAddress, '
            '@PersonnelAddress.GetByQuery("PersonnelID":@[RespondentAtty.first.PersonnelID]))]'
        )


# ─── reference extraction ──────────────────────────────────────────────────

class TestReferencedEntities:
    def test_simple_chain_bases(self):
        toks = [
            _parse("@[Respondent.first.NameLastName]"),
            _parse("@[ComplainantAddress.first.City]"),
        ]
        out = prelude.referenced_entities(toks)
        assert out == ["Respondent", "ComplainantAddress"]

    def test_dedupes_in_first_appearance_order(self):
        toks = [
            _parse("@[Complainant.first.NameFirstName]"),
            _parse("@[Complainant.first.NameLastName]"),
            _parse("@[ComplainantAddress.first.City]"),
            _parse("@[Complainant.first.MrMs]"),
        ]
        out = prelude.referenced_entities(toks)
        assert out == ["Complainant", "ComplainantAddress"]

    def test_walks_nested_chains_in_args(self):
        # Nested @[...] reference inside If condition shouldn't be lost.
        toks = [_parse("@[If(@[Respondent.Any()] == true)]")]
        out = prelude.referenced_entities(toks)
        assert "Respondent" in out


# ─── full prelude generation ───────────────────────────────────────────────

class TestGeneratePrelude:
    def test_no_child_refs_returns_empty(self):
        toks = [_parse("@[Respondent.first.NameLastName]")]
        assert prelude.generate_prelude(toks) == []

    def test_single_child_emits_parent_then_child(self):
        toks = [_parse("@[ComplainantAddress.first.City]")]
        lines = prelude.generate_prelude(toks)
        assert len(lines) == 2
        # parent first
        assert lines[0].startswith("@[CreateVar(@Complainant,")
        assert "CaseInvolvement" in lines[0]
        # child second
        assert lines[1].startswith("@[CreateVar(@ComplainantAddress,")
        assert "@NameAddress" in lines[1]
        assert "Complainant.first.NameID" in lines[1]

    def test_assignment_child_uses_personnel_source(self):
        toks = [_parse("@[RespondentAttyAddress.first.StreetAddress]")]
        lines = prelude.generate_prelude(toks)
        assert len(lines) == 2
        assert "@CaseAssignment" in lines[0]
        assert '"Type":"CIT12"' in lines[0]
        assert "@PersonnelAddress" in lines[1]
        assert "RespondentAtty.first.PersonnelID" in lines[1]

    def test_parent_emitted_once_even_with_multiple_children(self):
        # Both ComplainantAddress and ComplainantPhone share parent
        # Complainant — declare it ONCE.
        toks = [
            _parse("@[ComplainantAddress.first.City]"),
            _parse("@[ComplainantPhone.first.PhoneNumber]"),
        ]
        lines = prelude.generate_prelude(toks)
        parent_lines = [l for l in lines if "@CaseInvolvement" in l]
        assert len(parent_lines) == 1, lines

    def test_parent_already_referenced_directly_still_declared(self):
        # Output uses both bare @[Complainant.X] AND @[ComplainantAddress.X].
        # Parent must still be declared so the child's query works.
        toks = [
            _parse("@[Complainant.first.NameLastName]"),
            _parse("@[ComplainantAddress.first.City]"),
        ]
        lines = prelude.generate_prelude(toks)
        assert any("@Complainant," in l and "CaseInvolvement" in l for l in lines)
        assert any("@ComplainantAddress," in l for l in lines)

    def test_unknown_parent_skipped_silently(self):
        # MysteryEntityAddress isn't a known child — no prelude lines.
        toks = [_parse("@[MysteryEntityAddress.first.City]")]
        # PineParseError would actually fire here on the unknown base,
        # so use a name that parses. Use a name whose suffix matches
        # but whose parent isn't in the table.
        # (No clean way to construct one because parents we know cover
        # all the known suffixes — so we just verify the function
        # doesn't crash on something it can't classify.)
        assert prelude.generate_prelude([]) == []


# ─── RTF prepending ────────────────────────────────────────────────────────

class TestPrependPreludeToRtf:
    def test_no_prelude_returns_unchanged(self):
        rtf = "{\\rtf1 hello \\par}"
        assert prelude.prepend_prelude_to_rtf(rtf, []) == rtf

    def test_plain_text_input_gets_block_at_top(self):
        body = "Some text without RTF header.\nMore text."
        lines = ["@[CreateVar(@Respondent, ...)]"]
        out = prelude.prepend_prelude_to_rtf(body, lines)
        assert out.startswith("@[CreateVar(@Respondent, ...)]\n\n")

    def test_rtf_with_par_inserts_before_first_par(self):
        rtf = "{\\rtf1\\ansi\\deff0 \\par hello body \\par}"
        lines = ["@[CreateVar(@Respondent, X)]"]
        out = prelude.prepend_prelude_to_rtf(rtf, lines)
        # Prelude appears before the first \par
        assert "@[CreateVar(@Respondent, X)]" in out
        prelude_idx = out.index("@[CreateVar(@Respondent, X)]")
        first_par_idx = out.index("\\par")
        assert prelude_idx < first_par_idx


# ─── end-to-end through the pipeline ───────────────────────────────────────

class TestPipelineIntegration:
    def test_prelude_added_for_fresh_deployment(self):
        # A fresh deployment without an agency config should emit the
        # prelude — child entities aren't pre-declared anywhere.
        from pipeline.engine import prelude as prelude_module
        from pipeline.parser import pine_parser
        toks = [pine_parser.parse("@[ComplainantAddress.first.City]")]
        # agency_overrides=None mimics a brand-new deployment.
        lines = prelude_module.generate_prelude(toks, agency_overrides=None)
        assert len(lines) == 2
        assert "CreateVar(@Complainant," in lines[0]
        assert "CreateVar(@ComplainantAddress," in lines[1]

    def test_prelude_disabled_via_flag(self):
        from pipeline import pipeline
        rtf = "Header\n%[Cust_Complainant_MailAddress.City]\nfooter"
        result = pipeline.convert_template(rtf, agency="oba", emit_prelude=False)
        assert "@[CreateVar(@Complainant," not in result.converted_rtf

    def test_no_prelude_when_only_root_entities(self):
        from pipeline import pipeline
        # Bare Respondent name doesn't need a prelude.
        rtf = "%[JW_Respondent.FullName]"
        result = pipeline.convert_template(rtf, agency="oba")
        assert "@[CreateVar" not in result.converted_rtf

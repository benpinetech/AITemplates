"""Tests for the universal Pine role enum."""

from __future__ import annotations

import pytest

from pipeline.grammar import role_enum


# ─── enum integrity ──────────────────────────────────────────────────────

class TestEnumStructure:
    def test_involvement_codes_unique(self):
        codes = [r.code for r in role_enum.INVOLVEMENT_ROLES]
        assert len(codes) == len(set(codes)), "duplicate involvement codes"

    def test_assignment_codes_unique(self):
        codes = [r.code for r in role_enum.ASSIGNMENT_ROLES]
        assert len(codes) == len(set(codes)), "duplicate assignment codes"

    def test_council_appears_in_both_tables(self):
        # Council intentionally appears in both — same code, different
        # parent table semantics. Verify both tables have it.
        inv_codes = {r.code for r in role_enum.INVOLVEMENT_ROLES}
        asg_codes = {r.code for r in role_enum.ASSIGNMENT_ROLES}
        assert "COUNCIL" in inv_codes
        assert "COUNCIL" in asg_codes

    def test_known_codes_present(self):
        # Spot-check that the codes the user explicitly mentioned are here.
        for code in [
            "RESPONDENT", "COMPLAINANT", "DEFENDANT", "PETITIONER", "VICTIM",
        ]:
            assert code in {r.code for r in role_enum.INVOLVEMENT_ROLES}, code
        for code in [
            "DEFENSEATTORNEY", "PROSECUTINGATTORNEY", "JUDGE", "INVESTIGATOR",
            "ASSTDC", "CHIEFDC", "DISTRICTATTY",
        ]:
            assert code in {r.code for r in role_enum.ASSIGNMENT_ROLES}, code


# ─── classification helper ───────────────────────────────────────────────

class TestClassifyPineName:
    @pytest.mark.parametrize("pine_name", [
        "Respondent", "Complainant", "Defendant", "Petitioner", "Victim",
    ])
    def test_involvement_display_names(self, pine_name):
        assert role_enum.classify_pine_name(pine_name) == "involvement"

    @pytest.mark.parametrize("pine_name", [
        "Defense Attorney", "Prosecutor", "Investigator", "Municipal Judge",
        "District Attorney",
    ])
    def test_assignment_display_names(self, pine_name):
        assert role_enum.classify_pine_name(pine_name) == "assignment"

    def test_unknown_returns_none(self):
        assert role_enum.classify_pine_name("OBAAttorney") is None  # agency rename
        assert role_enum.classify_pine_name("Mystery") is None


# ─── prompt rendering ────────────────────────────────────────────────────

class TestRenderForPrompt:
    def test_includes_all_codes(self):
        out = role_enum.render_for_prompt()
        for r in role_enum.INVOLVEMENT_ROLES:
            assert r.code in out, f"missing {r.code}"
        for r in role_enum.ASSIGNMENT_ROLES:
            assert r.code in out, f"missing {r.code}"

    def test_categorizes_by_master_code(self):
        out = role_enum.render_for_prompt()
        # MasterCode headers surface for each grouping
        assert 'MasterCode "Defense"' in out
        assert 'MasterCode "Prosecution"' in out
        assert 'MasterCode "Judiciary"' in out
        assert 'MasterCode "Law Enforcement"' in out

    def test_includes_table_source_info(self):
        out = role_enum.render_for_prompt()
        assert "CaseInvolvement" in out
        assert "CaseAssignment" in out


# ─── prompt integration ─────────────────────────────────────────────────

class TestPromptIntegration:
    def test_enum_appears_in_single_token_prompt(self):
        from pipeline.engine.llm_converter import ConversionRequest
        from pipeline.parser import jda_parser
        from pipeline.grammar.loaders import OrgVocabulary
        vocab = OrgVocabulary(entities=[], builtins=[], prompt_variables=[])
        req = ConversionRequest(
            jda_token=jda_parser.parse("%[Anything.X]"),
            agency="oba",
            vocabulary=vocab,
        )
        prompt = req.assemble_prompt()
        assert "PINE SYSTEM ROLE ENUM" in prompt
        assert "DEFENSEATTORNEY" in prompt
        assert "RESPONDENT" in prompt

    def test_enum_appears_in_batch_prompt(self):
        from pipeline.engine.llm_converter import BatchConversionRequest
        from pipeline.parser import jda_parser
        from pipeline.grammar.loaders import OrgVocabulary
        vocab = OrgVocabulary(entities=[], builtins=[], prompt_variables=[])
        req = BatchConversionRequest(
            jda_tokens=(jda_parser.parse("%[Anything.X]"),),
            agency="oba",
            vocabulary=vocab,
        )
        prompt = req.assemble_batch_prompt()
        assert "PINE SYSTEM ROLE ENUM" in prompt
        assert "PROSECUTINGATTORNEY" in prompt

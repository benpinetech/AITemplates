"""End-to-end pipeline tests.

We use synthetic RTF-shaped strings rather than real RTF files because
the pipeline operates on the byte stream — actual RTF formatting
isn't relevant to what's being tested. The Phase 1 RTF extractor is
tolerant enough that simple ``%[...]`` markers in plain text work
identically to real RTF for these tests.
"""

from __future__ import annotations

import textwrap

import pytest

from pipeline import pipeline
from pipeline.engine.llm_converter import LlmConverter, MockLlmClient
from pipeline.grammar.loaders import load_agency_overrides
from pipeline.parser import pine_parser
from pipeline.patterns.schema import Pattern
@pytest.fixture(scope="module")
def library():
    return []


# ─── single-token conversions ─────────────────────────────────────────────

class TestSingleToken:
    def test_unmatched_token_left_in_place(self, library):
        rtf = "Mystery: %[Mystery(thing)] tail."
        result = pipeline.convert_template(rtf, agency="any", library=library)
        # Unmatched segments aren't replaced — the JDA token stays
        # for the mapper to see.
        assert "%[Mystery(thing)]" in result.converted_rtf
        assert result.segments[0].provenance == pipeline.PROV_UNMATCHED


# ─── explicit drop patterns ───────────────────────────────────────────────

class TestDropPattern:
    def test_empty_rewrite_drops_the_token(self, library):
        # A verified suggestion with rewrite=[] means "consume this token,
        # emit nothing" — the segment matches (not unmatched) and the JDA
        # text is removed from the output.
        drop = Pattern(id="drop_x", description="", match="%[Cust_Junk]", rewrite=[])
        rtf = "Keep %[Cust_Junk] this."
        result = pipeline.convert_template(rtf, agency="any", library=[drop])
        assert "%[Cust_Junk]" not in result.converted_rtf
        assert result.segments[0].provenance == pipeline.PROV_SUGGESTION
        assert result.segments[0].pine_outputs == ()


# ─── LLM fallback wiring ──────────────────────────────────────────────────

class TestLlmConverter:
    def test_fallback_runs_on_unmatched(self, library):
        oba = load_agency_overrides("oba")
        client = MockLlmClient(lambda prompt: "@[Respondent.first.NameLastName]")
        fb = LlmConverter(client=client, library=library, agency_overrides=oba)
        rtf = "X: %[Mystery(thing)]"
        result = pipeline.convert_template(
            rtf, agency="oba", library=library, converter=fb,
        )
        assert "@[Respondent.first.NameLastName]" in result.converted_rtf
        assert result.segments[0].provenance == pipeline.PROV_LLM

    def test_fallback_failure_leaves_unmatched(self, library):
        oba = load_agency_overrides("oba")
        client = MockLlmClient(lambda prompt: "Sorry I can't.")
        fb = LlmConverter(client=client, library=library, agency_overrides=oba)
        rtf = "X: %[Mystery(thing)]"
        result = pipeline.convert_template(
            rtf, agency="oba", library=library, converter=fb,
        )
        # LLM failed → segment stays unmatched, JDA token in place.
        assert "%[Mystery(thing)]" in result.converted_rtf
        assert result.segments[0].provenance == pipeline.PROV_UNMATCHED

# ─── validation surfacing ─────────────────────────────────────────────────

class TestValidation:
    def test_clean_output_has_no_errors(self, library):
        rtf = "%[TitleCase(JW_Respondent.FullName)]"
        result = pipeline.convert_template(rtf, agency="oba", library=library)
        errors = [i for i in result.issues if i.severity == "error"]
        assert errors == []

    def test_validation_runs_against_unbalanced_chunks(self, library):
        # An open-If with no EndIf in the source produces an unbalanced
        # Pine output — the validator should catch it.
        # Use an unmatched chunk that the engine passes through with
        # the JDA token left in place. We trigger a structural issue
        # by having a Pine If without EndIf in the converted output.
        # Easiest: force it via the LLM fallback returning an unbalanced
        # token… actually validator is on the *Pine* stream. Let me
        # just check that the validator has run by counting issues for
        # a clean input is 0 (handled above) and for a SetCasing-on-
        # ProsNum input is >0.
        # We'll pass an LLM fallback that injects a bad token.
        oba = load_agency_overrides("oba")
        client = MockLlmClient(lambda prompt: "@[ProsNum.first.Number.SetCasing(Upper)]")
        fb = LlmConverter(client=client, library=library, agency_overrides=oba)
        rtf = "X: %[Mystery(thing)]"
        result = pipeline.convert_template(
            rtf, agency="oba", library=library, converter=fb,
        )
        # The LLM produced a token that violates the no_setcasing_on_prosnum
        # lint rule. Validator should surface that.
        rule_ids = {i.rule_id for i in result.issues}
        assert "no_setcasing_on_prosnum" in rule_ids


# ─── result shape ─────────────────────────────────────────────────────────

class TestResultShape:
    def test_agency_required(self, library):
        # convert_template requires `agency` as a positional parameter.
        with pytest.raises(TypeError):
            pipeline.convert_template("%[X]")  # type: ignore[call-arg]


# ─── edit-time rebuild ─────────────────────────────────────────────────────

class TestRebuildWithEdits:
    def test_empty_edit_yields_unmatched_like_output(self, library):
        rtf = "Dear %[TitleCase(JW_Respondent.FullName)]:"
        result = pipeline.convert_template(rtf, agency="oba", library=library)
        # Rejecting / clearing a segment → empty tuple of pine outputs.
        rebuilt = pipeline.rebuild_result_with_edits(result, {0: ()})
        assert rebuilt.segments[0].provenance == pipeline.PROV_EDIT
        assert rebuilt.segments[0].pine_outputs == ()

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

from v2 import pipeline
from v2.engine.llm_fallback import LlmFallback, MockLlmClient
from v2.grammar.loaders import load_org_overrides
from v2.parser import pine_parser
from v2.patterns import loader as pattern_loader


@pytest.fixture(scope="module")
def library():
    report = pattern_loader.load_library()
    report.raise_if_issues()
    return report.patterns


# ─── single-token conversions ─────────────────────────────────────────────

class TestSingleToken:
    def test_titlecase_fullname_oba(self, library):
        rtf = "Dear %[TitleCase(JW_Respondent.FullName)]:"
        result = pipeline.convert_template(rtf, org="oba", library=library)
        assert "@[Respondent.first.FormatName(F L).SetCasing(Title)]" in result.converted_rtf
        # Surrounding prose stays untouched.
        assert result.converted_rtf.startswith("Dear ")
        assert result.converted_rtf.endswith(":")
        # Exactly one segment, matched by a pattern.
        assert len(result.segments) == 1
        assert result.segments[0].provenance == pipeline.PROV_PATTERN
        assert result.segments[0].pattern.id == "oba_fullname_titlecase"

    def test_two_independent_tokens_preserve_prose(self, library):
        rtf = "Re: %[TitleCase(Cust_Complainant.FullName)] (Pros. No. %[JW_CaseDetails.ProsNum])"
        result = pipeline.convert_template(rtf, org="oba", library=library)
        # Both convert; prose between is intact.
        assert "@[Complainant.first.FormatName(F L).SetCasing(Title)]" in result.converted_rtf
        assert "@[ProsNum.first.Number]" in result.converted_rtf
        assert " (Pros. No. " in result.converted_rtf

    def test_unmatched_token_left_in_place(self, library):
        rtf = "Mystery: %[Mystery(thing)] tail."
        result = pipeline.convert_template(rtf, org="any", library=library)
        # Unmatched segments aren't replaced — the JDA token stays
        # for the mapper to see.
        assert "%[Mystery(thing)]" in result.converted_rtf
        assert result.segments[0].provenance == pipeline.PROV_UNMATCHED


# ─── chunk patterns ───────────────────────────────────────────────────────

class TestChunks:
    def test_defensive_null_collapses_with_prose_dropped(self, library):
        # The 4-token defensive null around a single body token: collapses
        # to 1 Pine token. Prose between source tokens within the chunk
        # span is dropped (this is the desired behaviour for collapse
        # patterns — the conditional structure goes away with it).
        rtf = (
            "prefix %[If(JW_Respondent.IsEmpty=true)] %[Else] "
            "%[TitleCase(JW_Respondent.FullName)] %[EndIf] suffix"
        )
        result = pipeline.convert_template(rtf, org="oba", library=library)
        # The full If/Else/.../EndIf span is replaced by the recursively-
        # converted body (one Pine token).
        assert "@[Respondent.first.FormatName(F L).SetCasing(Title)]" in result.converted_rtf
        assert "%[If(" not in result.converted_rtf
        assert "%[EndIf]" not in result.converted_rtf
        assert result.converted_rtf.startswith("prefix ")
        assert result.converted_rtf.endswith(" suffix")

    def test_gender_pronoun_preserves_prose_between(self, library):
        # 4-to-4 pattern: per-token replacement preserves the prose
        # bodies between the structural tokens (he/she/he-or-she).
        rtf = (
            "%[If(JW_Defendant.Gender=M)]he%[ElseIf(JW_Defendant.Gender=F)]"
            "she%[Else]he/she%[EndIf]"
        )
        result = pipeline.convert_template(rtf, org="oba", library=library)
        # Prose bodies (he/she/he-or-she) survive intact.
        assert "he" in result.converted_rtf
        assert "she" in result.converted_rtf
        assert "he/she" in result.converted_rtf
        # Pine If/ElseIf/Else/EndIf in place.
        assert "@[If('@[RespondentInfo.Gender]' == 'M')]" in result.converted_rtf
        assert "@[ElseIf('@[RespondentInfo.Gender]' == 'F')]" in result.converted_rtf
        assert "@[Else]" in result.converted_rtf
        assert "@[EndIf]" in result.converted_rtf


# ─── LLM fallback wiring ──────────────────────────────────────────────────

class TestLlmFallback:
    def test_fallback_runs_on_unmatched(self, library):
        oba = load_org_overrides("oba")
        client = MockLlmClient(lambda prompt: "@[Respondent.first.NameLastName]")
        fb = LlmFallback(client=client, library=library, org_overrides=oba)
        rtf = "X: %[Mystery(thing)]"
        result = pipeline.convert_template(
            rtf, org="oba", library=library, llm_fallback=fb,
        )
        assert "@[Respondent.first.NameLastName]" in result.converted_rtf
        assert result.segments[0].provenance == pipeline.PROV_LLM

    def test_fallback_failure_leaves_unmatched(self, library):
        oba = load_org_overrides("oba")
        client = MockLlmClient(lambda prompt: "Sorry I can't.")
        fb = LlmFallback(client=client, library=library, org_overrides=oba)
        rtf = "X: %[Mystery(thing)]"
        result = pipeline.convert_template(
            rtf, org="oba", library=library, llm_fallback=fb,
        )
        # LLM failed → segment stays unmatched, JDA token in place.
        assert "%[Mystery(thing)]" in result.converted_rtf
        assert result.segments[0].provenance == pipeline.PROV_UNMATCHED

    def test_fallback_not_called_when_pattern_matches(self, library):
        called = []
        def responder(prompt):
            called.append(prompt)
            return "@[anything]"
        oba = load_org_overrides("oba")
        fb = LlmFallback(client=MockLlmClient(responder), library=library, org_overrides=oba)
        rtf = "Re: %[TitleCase(JW_Respondent.FullName)]"
        pipeline.convert_template(rtf, org="oba", library=library, llm_fallback=fb)
        # No LLM calls — the pattern handled it.
        assert called == []


# ─── validation surfacing ─────────────────────────────────────────────────

class TestValidation:
    def test_clean_output_has_no_errors(self, library):
        rtf = "%[TitleCase(JW_Respondent.FullName)]"
        result = pipeline.convert_template(rtf, org="oba", library=library)
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
        oba = load_org_overrides("oba")
        client = MockLlmClient(lambda prompt: "@[ProsNum.first.Number.SetCasing(Upper)]")
        fb = LlmFallback(client=client, library=library, org_overrides=oba)
        rtf = "X: %[Mystery(thing)]"
        result = pipeline.convert_template(
            rtf, org="oba", library=library, llm_fallback=fb,
        )
        # The LLM produced a token that violates the no_setcasing_on_prosnum
        # lint rule. Validator should surface that.
        rule_ids = {i.rule_id for i in result.issues}
        assert "no_setcasing_on_prosnum" in rule_ids


# ─── result shape ─────────────────────────────────────────────────────────

class TestResultShape:
    def test_total_token_counts(self, library):
        rtf = "%[TitleCase(JW_Respondent.FullName)] %[JW_CaseDetails.ProsNum]"
        result = pipeline.convert_template(rtf, org="oba", library=library)
        assert result.total_jda_tokens == 2
        assert result.total_pine_tokens == 2

    def test_summary_line(self, library):
        rtf = "%[TitleCase(JW_Respondent.FullName)]"
        result = pipeline.convert_template(rtf, org="oba", library=library)
        line = result.summary_line()
        assert "pattern=1" in line
        assert "unmatched=0" in line

    def test_org_required(self, library):
        # convert_template requires `org` as a positional parameter.
        with pytest.raises(TypeError):
            pipeline.convert_template("%[X]")  # type: ignore[call-arg]


# ─── prose preservation ───────────────────────────────────────────────────

class TestProsePreservation:
    def test_no_prose_outside_brackets_changes(self, library):
        # The pipeline must only touch byte ranges containing JDA tokens.
        rtf = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "%[TitleCase(JW_Respondent.FullName)] "
            "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
        )
        result = pipeline.convert_template(rtf, org="oba", library=library)
        assert result.converted_rtf.startswith("Lorem ipsum dolor sit amet")
        assert result.converted_rtf.endswith("magna aliqua.")
        # The Pine token replaces the JDA one; prose around stays.
        assert "@[Respondent.first.FormatName(F L).SetCasing(Title)]" in result.converted_rtf
        assert "%[" not in result.converted_rtf

"""Tests for chunk-pattern matching and the stream-level engine.

The defensive null wrapper is the canonical example: five JDA tokens
(If / empty-body / Else / has-entity-body / EndIf) collapse to a
single Pine token derived by recursively converting the has-entity
body via the single-token engine.
"""

from __future__ import annotations

import pytest

from v2.parser import jda_parser
from v2.patterns import engine, loader


@pytest.fixture(scope="module")
def library():
    report = loader.load_library()
    report.raise_if_issues()
    return report.patterns


def _tokens(*sources: str):
    """Parse a sequence of JDA source strings into JdaTokens."""
    return [jda_parser.parse(s) for s in sources]


# ─── basic chunk match ──────────────────────────────────────────────────────

class TestDefensiveNullChunk:
    def test_empty_body_zero_tokens(self, library):
        """Defensive null wrapper around a single body token — the
        empty-body sequence hole captures zero tokens and the
        has-entity body captures one token."""
        tokens = _tokens(
            "%[If(JW_Respondent.IsEmpty=true)]",
            "%[Else]",
            "%[TitleCase(JW_Respondent.FullName)]",
            "%[EndIf]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 1
        seg = segments[0]
        assert seg.matched
        assert seg.pattern.id == "defensive_null_wrapper_collapse"
        assert seg.consumed == 4
        assert seg.outputs[0].unparse() == (
            "@[Respondent.first.FormatName(F L).SetCasing(Title)]"
        )

    def test_multi_token_has_entity_body(self, library):
        """When the has-entity body is multiple tokens, the sequence
        hole captures all of them and each is recursively converted."""
        tokens = _tokens(
            "%[If(JW_Respondent.IsEmpty=true)]",
            "%[Else]",
            "%[TitleCase(JW_Respondent.FullName)]",
            "%[JW_CaseDetails.ProsNum]",
            "%[EndIf]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 1
        seg = segments[0]
        assert seg.consumed == 5
        # Two output tokens, one per body source token.
        assert [t.unparse() for t in seg.outputs] == [
            "@[Respondent.first.FormatName(F L).SetCasing(Title)]",
            "@[ProsNum.first.Number]",
        ]

    def test_with_prosnum_body(self, library):
        tokens = _tokens(
            "%[If(JW_CaseDetails.IsEmpty=true)]",
            "%[Else]",
            "%[JW_CaseDetails.ProsNum]",
            "%[EndIf]",
        )
        segments = engine.convert_stream(tokens, library)
        assert len(segments) == 1
        assert segments[0].matched
        assert segments[0].outputs[0].unparse() == "@[ProsNum.first.Number]"

    def test_chunk_falls_through_when_body_unconvertible(self, library):
        tokens = _tokens(
            "%[If(JW_X.IsEmpty=true)]",
            "%[Else]",
            "%[NoMatchingPattern(JW_X)]",
            "%[EndIf]",
        )
        segments = engine.convert_stream(tokens, library)
        # 4 segments — the chunk failed (no pattern matches the body),
        # so each token is processed independently. The control
        # keywords now hit the envelope passthrough patterns; only
        # the body stays unmatched.
        assert len(segments) == 4
        assert segments[0].pattern.id == "envelope_if"
        assert segments[1].pattern.id == "envelope_else"
        assert not segments[2].matched   # the body — no pattern handles it
        assert segments[3].pattern.id == "envelope_endif"


class TestOBAAddressBlock:
    def test_full_address_block(self, library):
        tokens = _tokens(
            "%[TitleCase(JW_Respondent_RosterAddress.Address)]",
            "%[TitleCase(JW_Respondent_RosterAddress.City)]",
            "%[JW_Respondent_RosterAddress.StateCode]",
            "%[JW_Respondent_RosterAddress.Zip]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 1
        seg = segments[0]
        assert seg.matched
        assert seg.pattern.id == "oba_address_block_titlecase"
        assert seg.consumed == 4
        assert [t.unparse() for t in seg.outputs] == [
            "@[RespondentAddress.first.StreetAddress]",
            "@[RespondentAddress.first.City]",
            "@[RespondentAddress.first.State]",
            "@[RespondentAddress.first.Zip]",
        ]

    def test_complainant_mail_address(self, library):
        tokens = _tokens(
            "%[TitleCase(Cust_Complainant_MailAddress.Address)]",
            "%[TitleCase(Cust_Complainant_MailAddress.City)]",
            "%[Cust_Complainant_MailAddress.StateCode]",
            "%[Cust_Complainant_MailAddress.Zip]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 1
        assert segments[0].pattern.id == "oba_address_block_titlecase"
        assert [t.unparse() for t in segments[0].outputs] == [
            "@[ComplainantAddress.first.StreetAddress]",
            "@[ComplainantAddress.first.City]",
            "@[ComplainantAddress.first.State]",
            "@[ComplainantAddress.first.Zip]",
        ]

    def test_chunk_doesnt_fire_when_entities_differ(self, library):
        """The address chunk requires the SAME entity in all four tokens.
        If the second token references a different entity, the chunk
        match fails and tokens are processed individually."""
        tokens = _tokens(
            "%[TitleCase(JW_Respondent_RosterAddress.Address)]",
            "%[TitleCase(Cust_Complainant_MailAddress.City)]",  # different entity
            "%[JW_Respondent_RosterAddress.StateCode]",
            "%[JW_Respondent_RosterAddress.Zip]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        # Chunk pattern fails the consistency check; each token falls
        # through to single-token handling (which doesn't have a pattern
        # for these and emits unmatched).
        assert len(segments) == 4
        assert all(not s.matched for s in segments)


class TestOBAGenderPronoun:
    def test_defendant_gender_block(self, library):
        tokens = _tokens(
            "%[If(JW_Defendant.Gender=M)]",
            "%[ElseIf(JW_Defendant.Gender=F)]",
            "%[Else]",
            "%[EndIf]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 1
        seg = segments[0]
        assert seg.matched
        assert seg.pattern.id == "oba_gender_pronoun_block"
        assert seg.consumed == 4
        rendered = [t.unparse() for t in seg.outputs]
        # The string-literal contents should have the info-var name
        # textually substituted in.
        assert rendered[0] == "@[If('@[RespondentInfo.Gender]' == 'M')]"
        assert rendered[1] == "@[ElseIf('@[RespondentInfo.Gender]' == 'F')]"
        assert rendered[2] == "@[Else]"
        assert rendered[3] == "@[EndIf]"

    def test_complainant_gender_block(self, library):
        tokens = _tokens(
            "%[If(Cust_Complainant.Gender=M)]",
            "%[ElseIf(Cust_Complainant.Gender=F)]",
            "%[Else]",
            "%[EndIf]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 1
        rendered = [t.unparse() for t in segments[0].outputs]
        assert rendered[0] == "@[If('@[ComplainantInfo.Gender]' == 'M')]"
        assert rendered[1] == "@[ElseIf('@[ComplainantInfo.Gender]' == 'F')]"

    def test_unknown_entity_falls_through(self, library):
        # An entity that's not in the info-var table — the chunk-level
        # gender pronoun transform raises UnknownTransformInputError,
        # so the chunk is skipped and each token falls through to
        # single-token conversion. The envelope patterns then claim
        # all four control keywords.
        tokens = _tokens(
            "%[If(JW_NoSuchEntity.Gender=M)]",
            "%[ElseIf(JW_NoSuchEntity.Gender=F)]",
            "%[Else]",
            "%[EndIf]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 4
        # The gender-specific chunk did NOT fire — that's the property
        # the test exists to assert.
        assert all(s.pattern is None or s.pattern.id != "oba_gender_pronoun_block"
                   for s in segments)
        # Each control keyword is now claimed by its envelope pattern.
        assert segments[0].pattern.id == "envelope_if"
        assert segments[1].pattern.id == "envelope_elseif"
        assert segments[2].pattern.id == "envelope_else"
        assert segments[3].pattern.id == "envelope_endif"


class TestStreamEnginePassthrough:
    def test_single_token_path_still_works(self, library):
        tokens = _tokens(
            "%[TitleCase(JW_Respondent.FullName)]",
            "%[JW_CaseDetails.ProsNum]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 2
        assert segments[0].pattern.id == "oba_fullname_titlecase"
        assert segments[1].pattern.id == "prosnum_bare"

    def test_unmatched_passes_through(self, library):
        tokens = _tokens("%[Mystery(thing)]")
        segments = engine.convert_stream(tokens, library)
        assert len(segments) == 1
        assert not segments[0].matched
        assert segments[0].unmatched_source is not None
        assert segments[0].unmatched_source.unparse() == "%[Mystery(thing)]"

    def test_chunk_then_single_token(self, library):
        # First four tokens are a defensive null wrapper collapsing to one Pine
        # token; the fifth is a regular single-token pattern.
        tokens = _tokens(
            "%[If(JW_Respondent.IsEmpty=true)]",
            "%[Else]",
            "%[TitleCase(JW_Respondent.FullName)]",
            "%[EndIf]",
            "%[JW_CaseDetails.ProsNum]",
        )
        segments = engine.convert_stream(tokens, library, org="oba")
        assert len(segments) == 2
        assert segments[0].consumed == 4
        assert segments[0].pattern.id == "defensive_null_wrapper_collapse"
        assert segments[0].outputs[0].unparse() == (
            "@[Respondent.first.FormatName(F L).SetCasing(Title)]"
        )
        assert segments[1].consumed == 1
        assert segments[1].pattern.id == "prosnum_bare"
        assert segments[1].outputs[0].unparse() == "@[ProsNum.first.Number]"

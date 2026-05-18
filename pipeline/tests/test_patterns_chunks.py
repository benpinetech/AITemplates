"""Tests for chunk-pattern matching and the stream-level engine.

The defensive null wrapper is the canonical example: five JDA tokens
(If / empty-body / Else / has-entity-body / EndIf) collapse to a
single Pine token derived by recursively converting the has-entity
body via the single-token engine.
"""

from __future__ import annotations

import pytest

from pipeline.parser import jda_parser
from pipeline.patterns import engine, loader


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


class TestOBAGenderPronoun:
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
    def test_unmatched_passes_through(self, library):
        tokens = _tokens("%[Mystery(thing)]")
        segments = engine.convert_stream(tokens, library)
        assert len(segments) == 1
        assert not segments[0].matched
        assert segments[0].unmatched_source is not None
        assert segments[0].unmatched_source.unparse() == "%[Mystery(thing)]"

"""Tests for the control-keyword envelope patterns.

These fire when no chunk pattern claimed the control token, ensuring
``%[Else]`` / ``%[EndIf]`` / ``%[If(...)]`` / ``%[ElseIf(...)]`` /
``%[EndForeach]`` always have a deterministic Pine equivalent rather
than falling through to the LLM (and possibly to the unmatched bin).
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


def _convert(source: str, library, org: str = "any"):
    return engine.convert(jda_parser.parse(source), library, org=org)


# ─── bare structural keywords ─────────────────────────────────────────────

class TestStandalone:
    def test_else(self, library):
        result = _convert("%[Else]", library)
        assert result.matched
        assert result.pattern.id == "envelope_else"
        assert result.outputs[0].unparse() == "@[Else]"

    def test_endif(self, library):
        result = _convert("%[EndIf]", library)
        assert result.matched
        assert result.pattern.id == "envelope_endif"
        assert result.outputs[0].unparse() == "@[EndIf]"

    def test_endforeach(self, library):
        result = _convert("%[EndForeach]", library)
        assert result.matched
        assert result.pattern.id == "envelope_endforeach"
        assert result.outputs[0].unparse() == "@[EndForEach]"


# ─── If with a condition that gets re-rendered as Pine ───────────────────

class TestIfEnvelope:
    def test_isempty_condition_preserved(self, library):
        # The user's reported case: a multi-segment path inside IsEmpty
        # that the chunk pattern can't handle. Envelope should catch
        # it and produce parseable Pine.
        result = _convert(
            "%[If(Complainant.StateIDNum.IsEmpty = true)]", library,
        )
        assert result.matched
        assert result.pattern.id == "envelope_if"
        # Round-trip the condition: equality is preserved, path is
        # preserved. (Pine emits == for equality canonically.)
        out = result.outputs[0].unparse()
        assert out.startswith("@[If(")
        assert "Complainant.StateIDNum.IsEmpty" in out
        assert "true" in out

    def test_simple_path_condition(self, library):
        result = _convert("%[If(JW_X.IsActive)]", library)
        assert result.matched
        out = result.outputs[0].unparse()
        assert out == "@[If(JW_X.IsActive)]"

    def test_elseif(self, library):
        result = _convert(
            "%[ElseIf(Complainant.Foo.IsEmpty = false)]", library,
        )
        assert result.matched
        assert result.pattern.id == "envelope_elseif"
        out = result.outputs[0].unparse()
        assert out.startswith("@[ElseIf(")


# ─── envelope priority: chunk patterns still win when applicable ─────────

class TestPriority:
    def test_oba_gender_chunk_still_wins_over_envelope(self, library):
        """An If/Else/EndIf trio that matches the gender pronoun chunk
        pattern should still be consumed by the chunk — not handled
        token-by-token by the envelope patterns."""
        tokens = [
            jda_parser.parse(s) for s in (
                "%[If(JW_Defendant.Gender=M)]",
                "%[ElseIf(JW_Defendant.Gender=F)]",
                "%[Else]",
                "%[EndIf]",
            )
        ]
        segments = engine.convert_stream(tokens, library, org="oba")
        # Single segment (the chunk match), not 4 separate envelopes.
        assert len(segments) == 1
        assert segments[0].pattern.id == "oba_gender_pronoun_block"

    def test_loose_if_chain_uses_envelope(self, library):
        """A condition that doesn't match any chunk pattern (e.g. a
        plain If with multi-segment IsEmpty) goes through envelopes
        token-by-token and produces 4 Pine segments."""
        tokens = [
            jda_parser.parse(s) for s in (
                "%[If(Complainant.StateIDNum.IsEmpty = true)]",
                "%[Else]",
                "%[Complainant.LastName]",   # body — single-token, no pattern
                "%[EndIf]",
            )
        ]
        segments = engine.convert_stream(tokens, library, org="oba")
        # 4 segments; If, Else, EndIf via envelope; the LastName body
        # has no single-token pattern and stays unmatched.
        assert len(segments) == 4
        assert segments[0].pattern.id == "envelope_if"
        assert segments[1].pattern.id == "envelope_else"
        assert not segments[2].matched   # the body, no pattern
        assert segments[3].pattern.id == "envelope_endif"

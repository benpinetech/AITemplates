"""Tests for the control-keyword envelope patterns.

These fire when no chunk pattern claimed the control token, ensuring
``%[Else]`` / ``%[EndIf]`` / ``%[If(...)]`` / ``%[ElseIf(...)]`` /
``%[EndForeach]`` always have a deterministic Pine equivalent rather
than falling through to the LLM (and possibly to the unmatched bin).
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



"""End-to-end pattern engine tests.

Each test loads the actual library, runs the engine against a JDA
input, and asserts the produced Pine output. These double as
acceptance tests for the seed patterns: if you change a TOML, this
file shows what was supposed to happen.
"""

from __future__ import annotations

import pytest

from v2.parser import jda_parser, pine_parser
from v2.patterns import engine, loader


@pytest.fixture(scope="module")
def library():
    """Load the real library once for the whole test module.

    Failing on any LoadIssue here surfaces TOML/schema errors as test
    failures rather than silently dropping patterns.
    """
    report = loader.load_library()
    report.raise_if_issues()
    return report.patterns


def _convert(source: str, library, org: str = "any"):
    return engine.convert(jda_parser.parse(source), library, org=org)


# ─── ProsNum ────────────────────────────────────────────────────────────────

class TestProsNum:
    def test_uppercase_strips_setcasing(self, library):
        result = _convert("%[UpperCase(JW_CaseDetails.ProsNum)]", library)
        assert result.matched
        assert result.pattern.id == "prosnum_uppercase"
        assert len(result.outputs) == 1
        assert result.outputs[0].unparse() == "@[ProsNum.first.Number]"

    def test_bare(self, library):
        result = _convert("%[JW_CaseDetails.ProsNum]", library)
        assert result.matched
        assert result.pattern.id == "prosnum_bare"
        assert result.outputs[0].unparse() == "@[ProsNum.first.Number]"


# ─── CurrentDate / FormatDate ───────────────────────────────────────────────

class TestCurrentDate:
    def test_preset1(self, library):
        result = _convert("%[FormatDate(CurrentDate(), MMMM d, yyyy)]", library)
        assert result.matched
        assert result.pattern.id == "currentdate_format_to_preset"
        assert result.outputs[0].unparse() == "@[builtin.today.FormatDate(preset1)]"

    def test_preset5(self, library):
        result = _convert("%[FormatDate(CurrentDate(), MM/dd/yyyy)]", library)
        assert result.matched
        assert result.outputs[0].unparse() == "@[builtin.today.FormatDate(preset5)]"

    def test_unknown_format_falls_through_unchanged(self, library):
        # A bizarre format the table doesn't know — should still emit
        # something parseable, with the original format string.
        result = _convert("%[FormatDate(CurrentDate(), q)]", library)
        assert result.matched
        # Custom format passed through verbatim.
        assert result.outputs[0].unparse() == "@[builtin.today.FormatDate(q)]"


# ─── Subdocument ────────────────────────────────────────────────────────────

class TestSubdocument:
    def test_oba_template_letterhead_expands_to_two(self, library):
        result = _convert("%[Subdocument(Template\\Letterhead)]", library, org="oba")
        assert result.matched
        assert result.pattern.id == "subdocument_path_expansion"
        assert len(result.outputs) == 2
        assert result.outputs[0].unparse() == "@[SubDocument(5)]"
        assert result.outputs[1].unparse() == "@[SubDocument(3)]"

    def test_pd_letterhead_expands_to_one(self, library):
        result = _convert("%[Subdocument(Subdocs\\_Letterhead)]", library)
        assert result.matched
        assert len(result.outputs) == 1
        assert result.outputs[0].unparse() == "@[SubDocument(7)]"

    def test_unknown_path_no_match(self, library):
        # Path not in the table and no OBA fallback applies → engine
        # treats this as no-match, ready for LLM fallback.
        result = _convert("%[Subdocument(Subdocs\\Unknown)]", library)
        assert not result.matched

    def test_oba_unknown_template_path_uses_fallback(self, library):
        # Any Template\* path under OBA expands to [5, 3] even if not
        # explicitly tabulated.
        result = _convert("%[Subdocument(Template\\Anything)]", library, org="oba")
        assert result.matched
        assert [t.unparse() for t in result.outputs] == ["@[SubDocument(5)]", "@[SubDocument(3)]"]


# ─── Initials ───────────────────────────────────────────────────────────────

class TestInitials:
    def test_currentuser_lowercase_gives_cu_no_first(self, library):
        result = _convert(
            "%[LowerCase(Initials(JW_CurrentUser.FullName, false))]", library,
        )
        assert result.matched
        assert result.pattern.id == "initials_currentuser_lowercase"
        assert result.outputs[0].unparse() == "@[cu.FormatName(FILI)]"

    def test_currentuser_bare(self, library):
        result = _convert(
            "%[Initials(JW_CurrentUser.FullName, false)]", library,
        )
        assert result.matched
        assert result.outputs[0].unparse() == "@[cu.FormatName(FILI)]"

    def test_oba_attorney_uses_first(self, library):
        result = _convert(
            "%[Initials(Cust_OBAAttorney.FullName, false)]", library,
        )
        assert result.matched
        assert result.pattern.id == "initials_bare_general"
        assert result.outputs[0].unparse() == "@[OBAAttorney.first.FormatName(FILI)]"

    def test_lowercase_general(self, library):
        result = _convert(
            "%[LowerCase(Initials(Cust_OBAAttorney.FullName, false))]", library,
        )
        assert result.matched
        # General lowercase variant drops the LowerCase wrapper.
        assert result.outputs[0].unparse() == "@[OBAAttorney.first.FormatName(FILI)]"


# ─── Prompt variables ───────────────────────────────────────────────────────

class TestPromptVariables:
    def test_known_dateofletter(self, library):
        result = _convert("%[DateofLetter.DateofLetter]", library)
        assert result.matched
        assert result.pattern.id == "prompt_variable_self_dotted"
        assert result.outputs[0].unparse() == "@[DateOfLetter]"

    def test_unknown_x_x_falls_through(self, library):
        # X.X with unknown name — engine should not match the prompt-
        # variable pattern (transform raises UnknownTransformInputError)
        # and there's no other fallback today, so result.matched is False.
        result = _convert("%[Mystery.Mystery]", library)
        assert not result.matched

    def test_non_repeating_path_doesnt_fire(self, library):
        # A normal entity.field path must NOT be picked up as a prompt
        # variable.
        result = _convert("%[JW_Respondent.FullName]", library, org="oba")
        # This particular path has no specific pattern (bare FullName
        # split-form is in Phase 2.5), but the prompt-var pattern must
        # not have fired.
        if result.matched:
            assert result.pattern.id != "prompt_variable_self_dotted"


# ─── OBA full name and last name ────────────────────────────────────────────

class TestOBAFormatName:
    def test_titlecase_fullname(self, library):
        result = _convert("%[TitleCase(JW_Respondent.FullName)]", library, org="oba")
        assert result.matched
        assert result.pattern.id == "oba_fullname_titlecase"
        assert result.outputs[0].unparse() == (
            "@[Respondent.first.FormatName(F L).SetCasing(Title)]"
        )

    def test_uppercase_fullname_complainant(self, library):
        result = _convert(
            "%[UpperCase(Cust_Complainant.FullName)]", library, org="oba",
        )
        assert result.matched
        assert result.outputs[0].unparse() == (
            "@[Complainant.first.FormatName(F L).SetCasing(Upper)]"
        )

    def test_titlecase_lastname_salutation(self, library):
        result = _convert(
            "%[TitleCase(Cust_OBAAttorney.LastName)]", library, org="oba",
        )
        assert result.matched
        assert result.pattern.id == "oba_lastname_titlecase"
        assert result.outputs[0].unparse() == (
            "@[OBAAttorney.first.FormatName(L).SetCasing(Title)]"
        )

    def test_oba_pattern_inactive_outside_oba(self, library):
        # An OBA-specific pattern should NOT match when org=criminal-pd.
        result = _convert(
            "%[TitleCase(JW_Respondent.FullName)]", library, org="criminal-pd",
        )
        assert not result.matched


# ─── No-match path ──────────────────────────────────────────────────────────

class TestNoMatch:
    def test_unfamiliar_construct(self, library):
        # An expression that no seed pattern handles. The engine returns
        # an empty result so Phase 4's LLM fallback can take over.
        result = _convert("%[SomeUnknownFunction(X.Y, Z)]", library)
        assert not result.matched
        assert result.outputs == ()

"""Tests for the Pine parser.

Same shape as the JDA tests: per-feature blocks plus a parametrised
round-trip test that walks a representative set of expressions.
"""

from __future__ import annotations

import pytest

from pipeline.parser import pine_ast
from pipeline.parser.pine_parser import (
    PineParseError,
    is_single_token,
    parse,
    parse_fragment,
    split_top_level_tokens,
)


# The canonical multi-token block a converter inserts: a gender-pronoun
# if/elseif/else/endif with literal pronoun text between control tokens.
GENDER_BLOCK = (
    "@[if('@[DefName.Gender]'=='M')]his"
    "@[elseif('@[DefName.Gender]'=='F')]her"
    "@[else]his/her@[endif]"
)


def round_trip(source: str) -> pine_ast.PineToken:
    ast1 = parse(source)
    ast2 = parse(ast1.unparse())
    assert ast1.inner == ast2.inner, (
        f"round-trip failed for {source!r}\n"
        f"  ast1.inner = {ast1.inner!r}\n"
        f"  unparsed   = {ast1.unparse()!r}\n"
        f"  ast2.inner = {ast2.inner!r}"
    )
    return ast1


# ─── chains ─────────────────────────────────────────────────────────────────

class TestChain:
    def test_two_segment_chain(self):
        ast = parse("@[Respondent.first.NameLastName]")
        chain = ast.inner
        assert isinstance(chain, pine_ast.PineChain)
        assert chain.base == "Respondent"
        assert chain.segments == (
            pine_ast.PineSegment(name="first", args=None),
            pine_ast.PineSegment(name="NameLastName", args=None),
        )

    def test_method_call_in_chain(self):
        ast = parse("@[Respondent.first.FormatName(F L).SetCasing(Title)]")
        chain = ast.inner
        assert isinstance(chain, pine_ast.PineChain)
        assert chain.base == "Respondent"
        assert chain.segments[0] == pine_ast.PineSegment(name="first", args=None)
        # FormatName has its arg captured as a single format literal.
        assert chain.segments[1].name == "FormatName"
        assert chain.segments[1].args == (
            pine_ast.PineLiteral(value="F L", kind=pine_ast.LIT_FORMAT),
        )
        # SetCasing(Title) — Title is a bare ident-arg.
        assert chain.segments[2].name == "SetCasing"
        assert chain.segments[2].args == (
            pine_ast.PineChain(base="Title", segments=()),
        )

    def test_no_arg_method_call_any(self):
        ast = parse("@[DefAtty.Any()]")
        chain = ast.inner
        assert isinstance(chain, pine_ast.PineChain)
        assert chain.segments[0] == pine_ast.PineSegment(name="Any", args=())

    def test_builtin_today_format_preset(self):
        ast = parse("@[builtin.today.FormatDate(preset1)]")
        chain = ast.inner
        assert isinstance(chain, pine_ast.PineChain)
        assert chain.base == "builtin"
        assert chain.segments[-1].name == "FormatDate"
        # FormatDate's arg is captured raw — the parser doesn't know
        # whether 'preset1' is a preset or a format string, so it's
        # always a LIT_FORMAT literal.
        assert chain.segments[-1].args == (
            pine_ast.PineLiteral(value="preset1", kind=pine_ast.LIT_FORMAT),
        )


# ─── nested tokens ─────────────────────────────────────────────────────────

class TestNested:
    def test_nested_in_binary_op(self):
        ast = parse("@[If(@[DefAtty.Any()] == true)]")
        ctrl = ast.inner
        assert isinstance(ctrl, pine_ast.PineControl)
        assert ctrl.keyword == "If"
        binop = ctrl.args[0]
        assert isinstance(binop, pine_ast.PineBinaryOp)
        assert binop.op == "=="
        assert isinstance(binop.left, pine_ast.PineNested)


# ─── CreateVar ─────────────────────────────────────────────────────────────

class TestCreateVar:
    def test_create_var_simple(self):
        ast = parse("@[CreateVar(@cu, @Personnel.GetByID(@[builtin.CurrentUserPersonnelID]))]")
        call = ast.inner
        assert isinstance(call, pine_ast.PineCall)
        assert call.name == "CreateVar"
        assert call.args[0] == pine_ast.PineAtName(name="cu")
        chain = call.args[1]
        assert isinstance(chain, pine_ast.PineChain)
        assert chain.base == pine_ast.PineAtName(name="Personnel")
        assert chain.segments[-1].name == "GetByID"

    def test_create_var_with_dict_literal(self):
        ast = parse(
            '@[CreateVar(@Defendant, @CaseInvolvement.GetByQuery("CaseID":@[builtin.CaseID],"Type":"DEF"))]'
        )
        call = ast.inner
        assert isinstance(call, pine_ast.PineCall)
        assert call.name == "CreateVar"
        chain = call.args[1]
        assert isinstance(chain, pine_ast.PineChain)
        gbq = chain.segments[0]
        assert gbq.name == "GetByQuery"
        assert isinstance(gbq.args[0], pine_ast.PineDictLit)
        keys = [k for k, _ in gbq.args[0].entries]
        assert keys == ["CaseID", "Type"]


# ─── controls ──────────────────────────────────────────────────────────────

class TestControl:
    def test_else(self):
        ast = parse("@[Else]")
        assert ast.inner == pine_ast.PineControl(keyword="Else", args=())

    def test_endif(self):
        ast = parse("@[EndIf]")
        assert ast.inner == pine_ast.PineControl(keyword="EndIf", args=())

    def test_endforeach(self):
        ast = parse("@[EndForEach]")
        assert ast.inner == pine_ast.PineControl(keyword="EndForEach", args=())

    def test_if_with_string_compare(self):
        ast = parse("@[If('@[DefName.Gender]' == 'M')]")
        ctrl = ast.inner
        assert isinstance(ctrl, pine_ast.PineControl)
        binop = ctrl.args[0]
        assert isinstance(binop, pine_ast.PineBinaryOp)
        assert binop.op == "=="
        assert binop.left.kind == pine_ast.LIT_STRING  # type: ignore[attr-defined]
        assert binop.right.kind == pine_ast.LIT_STRING  # type: ignore[attr-defined]

    def test_foreach_iter(self):
        ast = parse("@[Foreach(c IN Charges)]")
        ctrl = ast.inner
        assert isinstance(ctrl, pine_ast.PineControl)
        assert ctrl.keyword == "Foreach"
        binop = ctrl.args[0]
        assert isinstance(binop, pine_ast.PineBinaryOp)
        assert binop.op == "IN"


# ─── round-trip ────────────────────────────────────────────────────────────

class TestRoundTrip:
    SAMPLES = [
        "@[Respondent.first.NameLastName]",
        "@[Respondent.first.FormatName(F L).SetCasing(Title)]",
        "@[Respondent.first.FormatName(F M L).SetCasing(Upper)]",
        "@[OBAAttorney.first.FormatName(FILI)]",
        "@[cu.FormatName(FILI)]",
        "@[builtin.today.FormatDate(preset1)]",
        "@[builtin.Today.FormatDate(MM/dd/yyyy)]",
        "@[ProsNum.first.Number]",
        "@[Else]",
        "@[EndIf]",
        "@[EndForEach]",
        "@[If(@[DefAtty.Any()] == true)]",
        "@[If('@[DefName.Gender]' == 'M')]",
        "@[Foreach(c IN Charges)]",
        "@[Charges.Any()]",
        "@[Charges.ForEach(c)]",
        "@[Charges.EndForEach]",
        "@[CreateVar(@cu, @Personnel.GetByID(@[builtin.CurrentUserPersonnelID]))]",
        '@[CreateVar(@Defendant, @CaseInvolvement.GetByQuery("CaseID":@[builtin.CaseID],"Type":"DEF"))]',
        "@[SubDocument(5)]",
        "@[GetAge(@[DefName.DateOfBirth], @[builtin.Today])]",
        "@[CP.PhoneNumber.FormatPhoneNumber()]",
    ]

    @pytest.mark.parametrize("source", SAMPLES)
    def test_round_trip(self, source):
        round_trip(source)


# ─── error handling ────────────────────────────────────────────────────────

class TestErrors:
    def test_missing_brackets(self):
        with pytest.raises(PineParseError):
            parse("Respondent.first.NameLastName")

    def test_empty_brackets(self):
        with pytest.raises(PineParseError):
            parse("@[]")

    def test_none(self):
        with pytest.raises(PineParseError):
            parse(None)  # type: ignore[arg-type]

    def test_unterminated_string(self):
        with pytest.raises(PineParseError):
            parse("@[If(X='oops)]")

    def test_unbalanced_nested(self):
        with pytest.raises(PineParseError):
            parse("@[If(@[X.Any()]")


class TestFragment:
    """Multi-token fragment parsing — one or more @[...] tokens with
    arbitrary literal text between/around them (e.g. the gender-pronoun
    block a converter inserts as a single mapping)."""

    def test_single_token_fragment(self):
        toks = parse_fragment("@[Complainant.first.NameFirst]")
        assert len(toks) == 1
        assert toks[0].unparse() == "@[Complainant.first.NameFirst]"

    def test_gender_block_parses_every_token(self):
        toks = parse_fragment(GENDER_BLOCK)
        # Four control tokens; the parser normalizes casing/spacing on
        # unparse (if→If, ==→ ' == '), so assert on the canonical forms.
        assert [t.unparse() for t in toks] == [
            "@[If('@[DefName.Gender]' == 'M')]",
            "@[ElseIf('@[DefName.Gender]' == 'F')]",
            "@[Else]",
            "@[EndIf]",
        ]

    def test_split_ignores_literal_glue(self):
        assert split_top_level_tokens("pre @[A.b] mid @[C.d] post") == [
            "@[A.b]",
            "@[C.d]",
        ]

    def test_split_keeps_nested_token_whole(self):
        assert split_top_level_tokens("@[if('@[X.Gender]'=='M')]") == [
            "@[if('@[X.Gender]'=='M')]"
        ]

    def test_split_raises_on_unbalanced(self):
        with pytest.raises(PineParseError):
            split_top_level_tokens("@[if(")

    def test_fragment_rejects_no_token(self):
        with pytest.raises(PineParseError):
            parse_fragment("just literal text, no tokens")

    def test_fragment_rejects_unbalanced(self):
        with pytest.raises(PineParseError):
            parse_fragment("@[A.b]@[if(")

    def test_fragment_rejects_bad_inner_token(self):
        with pytest.raises(PineParseError):
            parse_fragment("@[A.b]glue@[]")

    def test_is_single_token(self):
        assert is_single_token("@[A.b]") is True
        assert is_single_token("  @[A.b]  ") is True
        assert is_single_token(GENDER_BLOCK) is False
        assert is_single_token("pre @[A.b]") is False

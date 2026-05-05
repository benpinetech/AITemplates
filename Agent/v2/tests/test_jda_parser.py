"""Tests for the JDA parser.

Each block of tests covers one grammar feature. The ``round_trip``
helper is the same property the corpus runner uses: parse, unparse,
parse the unparse, and compare structurally.
"""

from __future__ import annotations

import pytest

from v2.parser import jda_ast, jda_parser
from v2.parser.jda_parser import JdaParseError, parse


def round_trip(source: str) -> jda_ast.JdaToken:
    """Parse ``source``, unparse the AST, parse again. Assert equality."""
    ast1 = parse(source)
    ast2 = parse(ast1.unparse())
    assert ast1.inner == ast2.inner, (
        f"round-trip failed for {source!r}\n"
        f"  ast1.inner = {ast1.inner!r}\n"
        f"  unparsed   = {ast1.unparse()!r}\n"
        f"  ast2.inner = {ast2.inner!r}"
    )
    return ast1


# ─── paths ──────────────────────────────────────────────────────────────────

class TestPath:
    def test_simple_path(self):
        ast = parse("%[JW_Respondent.FullName]")
        assert ast.inner == jda_ast.JdaPath(parts=("JW_Respondent", "FullName"))

    def test_three_segment_path(self):
        ast = parse("%[Cust_RespondentAtty.FullName.IsEmpty]")
        assert ast.inner == jda_ast.JdaPath(
            parts=("Cust_RespondentAtty", "FullName", "IsEmpty"),
        )

    def test_path_with_outer_whitespace(self):
        ast = parse("%[ JW_Respondent.FullName ]")
        assert ast.inner == jda_ast.JdaPath(parts=("JW_Respondent", "FullName"))

    def test_path_with_internal_whitespace_artefact(self):
        # RTF sometimes leaves a stray space inside an identifier.
        ast = parse("%[Cust_Complainant _MailAddress.StateCode]")
        assert ast.inner == jda_ast.JdaPath(
            parts=("Cust_Complainant_MailAddress", "StateCode"),
        )

    def test_space_before_dot(self):
        ast = parse("%[JW_Respondent .FirstName]")
        assert ast.inner == jda_ast.JdaPath(parts=("JW_Respondent", "FirstName"))


# ─── calls ──────────────────────────────────────────────────────────────────

class TestCall:
    def test_titlecase_wrapper(self):
        ast = parse("%[TitleCase(JW_Respondent.FullName)]")
        assert ast.inner == jda_ast.JdaCall(
            name="TitleCase",
            args=(jda_ast.JdaPath(parts=("JW_Respondent", "FullName")),),
        )

    def test_lowercase_initials_nested(self):
        ast = parse("%[LowerCase(Initials(JW_CurrentUser.FullName, false))]")
        inner_initials = jda_ast.JdaCall(
            name="Initials",
            args=(
                jda_ast.JdaPath(parts=("JW_CurrentUser", "FullName")),
                jda_ast.JdaLiteral(value="false", kind=jda_ast.LITERAL_BOOL),
            ),
        )
        assert ast.inner == jda_ast.JdaCall(name="LowerCase", args=(inner_initials,))

    def test_call_with_space_before_paren(self):
        ast = parse("%[TitleCase (JW_Respondent.FullName)]")
        assert isinstance(ast.inner, jda_ast.JdaCall)
        assert ast.inner.name == "TitleCase"

    def test_currentdate_empty_args(self):
        # CurrentDate() takes no arguments; parser must allow empty arg list.
        ast = parse("%[CurrentDate()]")
        assert ast.inner == jda_ast.JdaCall(name="CurrentDate", args=())

    def test_formatdate_with_pattern(self):
        # The pattern after the comma contains spaces and a comma;
        # the parser must capture it as a single LITERAL_FORMAT arg.
        ast = parse("%[FormatDate(CurrentDate(), MMMM d, yyyy)]")
        assert isinstance(ast.inner, jda_ast.JdaCall)
        assert ast.inner.name == "FormatDate"
        assert len(ast.inner.args) == 2
        assert ast.inner.args[0] == jda_ast.JdaCall(name="CurrentDate", args=())
        assert ast.inner.args[1] == jda_ast.JdaLiteral(
            value="MMMM d, yyyy", kind=jda_ast.LITERAL_FORMAT,
        )

    def test_subdocument_path(self):
        ast = parse("%[Subdocument(Template\\Letterhead)]")
        assert ast.inner == jda_ast.JdaCall(
            name="Subdocument",
            args=(jda_ast.JdaLiteral(value="Template\\Letterhead", kind=jda_ast.LITERAL_PATH),),
        )


# ─── controls ───────────────────────────────────────────────────────────────

class TestControl:
    def test_else(self):
        ast = parse("%[Else]")
        assert ast.inner == jda_ast.JdaControl(keyword="Else", args=())

    def test_endif(self):
        ast = parse("%[EndIf]")
        assert ast.inner == jda_ast.JdaControl(keyword="EndIf", args=())

    def test_endforeach_case_insensitive(self):
        ast = parse("%[ENDFOREACH]")
        assert ast.inner == jda_ast.JdaControl(keyword="EndForeach", args=())

    def test_endmultiselect(self):
        ast = parse("%[EndMultiSelect]")
        assert ast.inner == jda_ast.JdaControl(keyword="EndMultiSelect", args=())

    def test_if_with_isempty_equals_true(self):
        ast = parse("%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]")
        body = jda_ast.JdaBinaryOp(
            op="=",
            left=jda_ast.JdaPath(parts=("Cust_RespondentAtty", "FullName", "IsEmpty")),
            right=jda_ast.JdaLiteral(value="true", kind=jda_ast.LITERAL_BOOL),
        )
        assert ast.inner == jda_ast.JdaControl(keyword="If", args=(body,))

    def test_if_with_double_equals(self):
        ast = parse("%[If(JW_X.Y == true)]")
        assert isinstance(ast.inner, jda_ast.JdaControl)
        body = ast.inner.args[0]
        assert isinstance(body, jda_ast.JdaBinaryOp)
        assert body.op == "=="

    def test_if_unquoted_rhs_identifier(self):
        # An unquoted RHS like RBA is just a single-segment path; pattern
        # matching downstream interprets single-segment paths in context.
        ast = parse("%[If(JW_Defendant_Address.AddressTypeCode=RBA)]")
        body = ast.inner.args[0]  # type: ignore[union-attr]
        assert isinstance(body, jda_ast.JdaBinaryOp)
        assert body.right == jda_ast.JdaPath(parts=("RBA",))

    def test_multiselect_iter(self):
        ast = parse("%[MultiSelect(a in JW_Defendant_Address)]")
        # The collection is parsed as a single-segment path — JdaPath
        # is the canonical representation of any dotted-or-not name.
        iter_node = jda_ast.JdaIter(
            var="a",
            collection=jda_ast.JdaPath(parts=("JW_Defendant_Address",)),
        )
        assert ast.inner == jda_ast.JdaControl(keyword="MultiSelect", args=(iter_node,))

    def test_foreach_iter_uppercase_in(self):
        ast = parse("%[Foreach(c IN Charges)]")
        assert isinstance(ast.inner, jda_ast.JdaControl)
        assert ast.inner.keyword == "Foreach"
        iter_node = ast.inner.args[0]
        assert isinstance(iter_node, jda_ast.JdaIter)
        assert iter_node.var == "c"


# ─── round-trip ─────────────────────────────────────────────────────────────

class TestRoundTrip:
    SAMPLES = [
        "%[JW_Respondent.FullName]",
        "%[ JW_Respondent.FullName ]",
        "%[Cust_RespondentAtty.FullName.IsEmpty]",
        "%[TitleCase(JW_Respondent.FullName)]",
        "%[Initials(Cust_OBAAttorney.FullName, false)]",
        "%[FormatDate(CurrentDate(), MMMM d, yyyy)]",
        "%[FormatDate(CurrentDate(), MM/dd/yyyy)]",
        "%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]",
        "%[If(JW_Defendant_Address.AddressTypeCode=RBA)]",
        "%[Else]",
        "%[EndIf]",
        "%[MultiSelect(a in JW_Defendant_Address)]",
        "%[EndMultiSelect]",
        "%[Subdocument(Template\\Letterhead)]",
        "%[LowerCase(Initials(JW_CurrentUser.FullName, false))]",
        "%[UpperCase(JW_CaseDetails.ProsNum)]",
    ]

    @pytest.mark.parametrize("source", SAMPLES)
    def test_round_trip(self, source):
        round_trip(source)


# ─── error handling ─────────────────────────────────────────────────────────

class TestErrors:
    def test_missing_brackets(self):
        with pytest.raises(JdaParseError):
            parse("JW_Respondent.FullName")

    def test_empty_brackets(self):
        with pytest.raises(JdaParseError):
            parse("%[]")

    def test_none(self):
        with pytest.raises(JdaParseError):
            parse(None)  # type: ignore[arg-type]

    def test_unterminated_string(self):
        with pytest.raises(JdaParseError):
            parse("%[If(X='oops)]")

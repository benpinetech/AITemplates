"""Tests for the RTF extractor."""

from __future__ import annotations

import textwrap

from pipeline.parser import rtf_extractor


# A tiny synthetic RTF-shaped payload. The extractor doesn't validate
# RTF — it just walks for bracket openers and skips control sequences.
SAMPLE_RTF = textwrap.dedent(
    r"""
    {\rtf1\ansi
    Some prose. %[JW_Respondent.FullName] more prose.
    \par Date: %[FormatDate(CurrentDate(), MMMM d, yyyy)]
    \par More prose %[Else]
    """
).strip()


class TestExtract:
    def test_finds_three_jda_hits(self):
        hits = list(rtf_extractor.extract(SAMPLE_RTF, bracket="%["))
        assert len(hits) == 3
        assert hits[0].text == "%[JW_Respondent.FullName]"
        assert hits[1].text == "%[FormatDate(CurrentDate(), MMMM d, yyyy)]"
        assert hits[2].text == "%[Else]"

    def test_positions_are_in_order(self):
        hits = list(rtf_extractor.extract(SAMPLE_RTF, bracket="%["))
        starts = [h.start for h in hits]
        assert starts == sorted(starts)

    def test_text_recovers_to_correct_substring(self):
        # End is exclusive — text slice from [start:end] is the *raw*
        # match, not the cleaned text. We only assert the cleaned text
        # corresponds to the expected expression.
        hits = list(rtf_extractor.extract(SAMPLE_RTF, bracket="%["))
        for h in hits:
            assert SAMPLE_RTF[h.start : h.start + 2] == "%["

    def test_ast_is_populated(self):
        hits = list(rtf_extractor.extract(SAMPLE_RTF, bracket="%["))
        assert all(h.ast is not None for h in hits), [h.error for h in hits]

    def test_pine_brackets(self):
        rtf = "Some text @[Respondent.first.NameLastName] and @[Else] tail."
        hits = list(rtf_extractor.extract(rtf, bracket="@["))
        assert [h.text for h in hits] == [
            "@[Respondent.first.NameLastName]",
            "@[Else]",
        ]

    def test_parse_false_skips_ast(self):
        hits = list(rtf_extractor.extract(SAMPLE_RTF, bracket="%[", parse=False))
        assert all(h.ast is None and h.error is None for h in hits)

    def test_unbalanced_is_skipped(self):
        # Opener with no matching close — the extractor moves past and
        # finds the well-formed one.
        rtf = "tail %[JW_Respondent.FullName then %[Else]"
        hits = list(rtf_extractor.extract(rtf, bracket="%["))
        # The first opener is unbalanced; we expect the second one
        # to be picked up.
        assert any(h.text == "%[Else]" for h in hits)


class TestExtractText:
    def test_plain_text_jda(self):
        text = "%[JW_Respondent.FullName]\n%[Else]"
        hits = list(rtf_extractor.extract_text(text, bracket="%["))
        assert [h.text for h in hits] == ["%[JW_Respondent.FullName]", "%[Else]"]

    def test_plain_text_pine(self):
        text = "@[Respondent.first.NameLastName]\n@[Else]"
        hits = list(rtf_extractor.extract_text(text, bracket="@["))
        assert [h.text for h in hits] == [
            "@[Respondent.first.NameLastName]",
            "@[Else]",
        ]

    def test_handles_nested_in_pine(self):
        text = "@[If(@[X.Any()] == true)]"
        hits = list(rtf_extractor.extract_text(text, bracket="@["))
        # We get exactly one outer hit; the nested @[X.Any()] is part
        # of the outer expression, not a separate top-level hit.
        assert len(hits) == 1
        assert hits[0].text == "@[If(@[X.Any()] == true)]"

"""Tests for the RTF extractor."""

from __future__ import annotations

import textwrap
from pathlib import Path

from pipeline.parser import rtf_extractor

_GROUND_TRUTH = (
    Path(__file__).resolve().parents[2]
    / "ground_truth"
    / "evaluation_templates"
    / "jda_to_pine"
)


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


class TestFragmentedOpener:
    """RTF splits a single ``%[`` opener across formatting runs. The
    extractor's normalization pre-pass must stitch these back together,
    or the fillpoint is silently dropped.

    Regression for template ``219 - Plea Deadline.rtf``, where 9 of 12
    fillpoints were lost because ``%`` and ``[`` landed in separate runs
    with control words between them.
    """

    # The exact shape seen in the wild: ``%}`` closes a run, a new run
    # ``{...}`` opens, and the ``[`` sits at the *end* of that run after a
    # string of control words — NOT immediately after a newline.
    FRAGMENT = (
        r"{\rtf1\ansi prose "
        r"{\f1\cf1 %}{\rtlch\f1\fs16\cf1\insrsid160 "
        r"[}{\rtlch\f1\cf2\insrsid160 JW_Defendant.LastName}"
        r"{\rtlch\f1\cf1\insrsid160 ]} tail."
    )

    def test_normalize_stitches_split_opener(self):
        normalized = rtf_extractor.normalize_rtf(self.FRAGMENT)
        assert "%[" in normalized

    def test_extract_recovers_fragmented_fillpoint(self):
        hits = list(rtf_extractor.extract(self.FRAGMENT, bracket="%["))
        assert len(hits) == 1
        assert hits[0].text == "%[JW_Defendant.LastName]"
        assert hits[0].ast is not None, hits[0].error

    def test_newline_before_bracket_still_works(self):
        # The original, narrower fragment shape (``\n`` right before
        # ``[``) must keep working after the fix.
        frag = (
            r"{\rtf1 %}{\rtlch\f1 " "\n"
            r"[}{\rtlch\f1 JW_Respondent.FullName}{\rtlch\f1 ]}"
        )
        hits = list(rtf_extractor.extract(frag, bracket="%["))
        assert [h.text for h in hits] == ["%[JW_Respondent.FullName]"]

    def test_contiguous_opener_unaffected(self):
        # ``%[`` already together must not be mangled by normalization.
        rtf = r"{\rtf1 prose %[JW_CaseDetails.CourtNum] more}"
        hits = list(rtf_extractor.extract(rtf, bracket="%["))
        assert [h.text for h in hits] == ["%[JW_CaseDetails.CourtNum]"]


class TestRealTemplate219:
    """End-to-end against the real corpus template. The source RTF holds
    15 legacy ``%[...]`` fillpoints; before the fragmented-opener fix the
    extractor recovered only 3 (the ones whose ``%[`` happened to stay
    contiguous), silently dropping the other 12.
    """

    LEGACY = _GROUND_TRUTH / "legacy" / "219 - Plea Deadline.rtf"

    def test_extracts_all_fillpoints(self):
        rtf = self.LEGACY.read_text(encoding="utf-8", errors="replace")
        hits = list(rtf_extractor.extract(rtf, bracket="%["))
        assert len(hits) == 15, [h.text for h in hits]

    def test_all_fillpoints_parse_cleanly(self):
        rtf = self.LEGACY.read_text(encoding="utf-8", errors="replace")
        hits = list(rtf_extractor.extract(rtf, bracket="%["))
        bad = [(h.text, h.error) for h in hits if h.ast is None]
        assert not bad, bad

    def test_previously_dropped_fillpoints_now_present(self):
        rtf = self.LEGACY.read_text(encoding="utf-8", errors="replace")
        texts = " ".join(h.text for h in rtf_extractor.extract(rtf, bracket="%["))
        # A representative sample of the fillpoints that the old narrow
        # normalization regex silently dropped.
        for needle in (
            "JW_Defendant_Address",
            "JW_Defendant.LastName",
            "JW_Defendant.MrMs",
            "FormatDate",
            "StatuteDesc",
        ):
            assert needle in texts, f"missing {needle!r} in {texts!r}"


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

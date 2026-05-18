"""Tests for the IsEmpty branch-swap pre-pass.

The pre-pass swaps if/else bodies for ``If(X.<NameField>.IsEmpty=true)``
conditionals so that, after the OBA polarity-flip pattern translates
the condition to ``If(@[Y.Any()]==true)``, the bodies end up attached
to the semantically-correct branches.
"""

from __future__ import annotations

import pytest

from pipeline.parser import branch_swap, jda_parser
from pipeline.patterns import engine, loader


@pytest.fixture(scope="module")
def library():
    report = loader.load_library()
    report.raise_if_issues()
    return report.patterns


# ─── pre-pass mechanics ───────────────────────────────────────────────────

class TestSwapMechanics:
    def test_fullname_isempty_swaps_bodies(self):
        src = (
            "%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]"
            "[empty body]"
            "%[Else]"
            "[has-name body]"
            "%[EndIf]"
        )
        out = branch_swap.swap_inverted_branches(src)
        assert out == (
            "%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]"
            "[has-name body]"
            "%[Else]"
            "[empty body]"
            "%[EndIf]"
        )

    def test_lastname_also_swaps(self):
        src = "%[If(JW_Respondent.LastName.IsEmpty = true)]A%[Else]B%[EndIf]"
        out = branch_swap.swap_inverted_branches(src)
        assert out == "%[If(JW_Respondent.LastName.IsEmpty = true)]B%[Else]A%[EndIf]"

    def test_isnullorempty_also_swaps(self):
        src = "%[If(Cust_RespondentAtty.FullName.IsNullOrEmpty = true)]A%[Else]B%[EndIf]"
        out = branch_swap.swap_inverted_branches(src)
        assert out == "%[If(Cust_RespondentAtty.FullName.IsNullOrEmpty = true)]B%[Else]A%[EndIf]"

    def test_bare_isempty_no_eq_true_swaps(self):
        # JDA also has a form without `= true` — same polarity flip applies.
        src = "%[If(JW_Atty_Def_Active.FullName.IsEmpty)]A%[Else]B%[EndIf]"
        out = branch_swap.swap_inverted_branches(src)
        assert out == "%[If(JW_Atty_Def_Active.FullName.IsEmpty)]B%[Else]A%[EndIf]"

    def test_bare_isnullorempty_swaps(self):
        src = "%[If(JW_Atty_Pros_Active.LastName.IsNullOrEmpty)]A%[Else]B%[EndIf]"
        out = branch_swap.swap_inverted_branches(src)
        assert out == "%[If(JW_Atty_Pros_Active.LastName.IsNullOrEmpty)]B%[Else]A%[EndIf]"

    def test_no_swap_for_unrelated_isempty(self):
        # StateIDNum.IsEmpty translates via envelope_if (no polarity
        # flip), so its branches must NOT be swapped — otherwise the
        # output would be semantically inverted.
        src = "%[If(Cust_Complainant.StateIDNum.IsEmpty = true)]A%[Else]B%[EndIf]"
        assert branch_swap.swap_inverted_branches(src) == src

    def test_no_swap_for_address_isempty(self):
        src = "%[If(Cust_Complainant_MailAddress.Address.IsEmpty = true)]A%[Else]B%[EndIf]"
        assert branch_swap.swap_inverted_branches(src) == src

    def test_no_swap_when_no_else(self):
        # No-Else blocks have nothing to swap.
        src = "%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]A%[EndIf]"
        assert branch_swap.swap_inverted_branches(src) == src

    def test_idempotent_on_empty(self):
        assert branch_swap.swap_inverted_branches("") == ""

    def test_idempotent_on_no_tokens(self):
        src = "Just plain text with no JDA tokens at all."
        assert branch_swap.swap_inverted_branches(src) == src

    def test_handles_nested_if(self):
        # Outer FullName.IsEmpty wraps an inner Gender check. The
        # outer should swap; the inner is not a polarity-flip
        # candidate so it stays put. The inner If/Else/EndIf live
        # *inside* the swapped body — but the bytes within that body
        # move as a unit, so the inner conditional's structure is
        # preserved.
        src = (
            "%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]"
            "OUTER-EMPTY"
            "%[Else]"
            "%[If(JW_Respondent.Gender=M)]M%[Else]F%[EndIf]"
            "%[EndIf]"
        )
        out = branch_swap.swap_inverted_branches(src)
        assert out == (
            "%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]"
            "%[If(JW_Respondent.Gender=M)]M%[Else]F%[EndIf]"
            "%[Else]"
            "OUTER-EMPTY"
            "%[EndIf]"
        )


# ─── per-token pattern translation ────────────────────────────────────────

class TestIfPattern:
    def _convert(self, src, library):
        return engine.convert(jda_parser.parse(src), library, org="oba")

    def test_unrelated_field_falls_through_to_envelope(self, library):
        # StateIDNum.IsEmpty doesn't have a polarity-flip pattern;
        # envelope_if preserves it as-is.
        r = self._convert("%[If(Cust_Complainant.StateIDNum.IsEmpty = true)]", library)
        assert r.matched
        assert r.pattern.id == "envelope_if"

    def test_address_isempty_falls_through_to_envelope(self, library):
        r = self._convert(
            "%[If(Cust_Complainant_MailAddress.Address.IsEmpty = true)]",
            library,
        )
        assert r.matched
        assert r.pattern.id == "envelope_if"

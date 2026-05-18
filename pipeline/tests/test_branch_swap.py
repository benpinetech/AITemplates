"""Tests for the IsEmpty branch-swap pre-pass.

The pre-pass swaps if/else bodies for ``If(X.<NameField>.IsEmpty=true)``
conditionals so that, after the OBA polarity-flip pattern translates
the condition to ``If(@[Y.Any()]==true)``, the bodies end up attached
to the semantically-correct branches.
"""

from __future__ import annotations

from pipeline.parser import branch_swap


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
        src = "%[If(JW_Atty_Def_Active.FullName.IsEmpty)]A%[Else]B%[EndIf]"
        out = branch_swap.swap_inverted_branches(src)
        assert out == "%[If(JW_Atty_Def_Active.FullName.IsEmpty)]B%[Else]A%[EndIf]"

    def test_bare_isnullorempty_swaps(self):
        src = "%[If(JW_Atty_Pros_Active.LastName.IsNullOrEmpty)]A%[Else]B%[EndIf]"
        out = branch_swap.swap_inverted_branches(src)
        assert out == "%[If(JW_Atty_Pros_Active.LastName.IsNullOrEmpty)]B%[Else]A%[EndIf]"

    def test_no_swap_for_unrelated_isempty(self):
        src = "%[If(Cust_Complainant.StateIDNum.IsEmpty = true)]A%[Else]B%[EndIf]"
        assert branch_swap.swap_inverted_branches(src) == src

    def test_no_swap_for_address_isempty(self):
        src = "%[If(Cust_Complainant_MailAddress.Address.IsEmpty = true)]A%[Else]B%[EndIf]"
        assert branch_swap.swap_inverted_branches(src) == src

    def test_no_swap_when_no_else(self):
        src = "%[If(Cust_RespondentAtty.FullName.IsEmpty = true)]A%[EndIf]"
        assert branch_swap.swap_inverted_branches(src) == src

    def test_idempotent_on_empty(self):
        assert branch_swap.swap_inverted_branches("") == ""

    def test_idempotent_on_no_tokens(self):
        src = "Just plain text with no JDA tokens at all."
        assert branch_swap.swap_inverted_branches(src) == src

    def test_handles_nested_if(self):
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

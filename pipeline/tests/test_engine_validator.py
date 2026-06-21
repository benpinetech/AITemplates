"""Tests for the Pine validator."""

from __future__ import annotations

import pytest

from pipeline.engine.validator import (
    Validator,
    ValidationIssue,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    errors_only,
    warnings_only,
)
from pipeline.grammar.loaders import load_lint_rules, load_agency_overrides
from pipeline.parser import pine_parser


@pytest.fixture(scope="module")
def validator_oba():
    return Validator(
        lint_rules=load_lint_rules(),
        agency=load_agency_overrides("oba"),
    )


def _toks(*sources):
    return [pine_parser.parse(s) for s in sources]


# ─── lint rules ────────────────────────────────────────────────────────────

class TestLintRules:
    def test_setcasing_on_prosnum_is_error(self, validator_oba):
        toks = _toks("@[ProsNum.first.Number.SetCasing(Upper)]")
        issues = validator_oba.validate_stream(toks)
        errs = errors_only(issues)
        assert any(i.rule_id == "no_setcasing_on_prosnum" for i in errs)

    def test_clean_prosnum_passes(self, validator_oba):
        toks = _toks("@[ProsNum.first.Number]")
        issues = validator_oba.validate_stream(toks)
        # No errors against the clean form.
        assert errors_only(issues) == []

    def test_subdocument_template_form_is_error(self, validator_oba):
        toks = _toks("@[SubDocument(template)]")
        issues = validator_oba.validate_stream(toks)
        assert any(i.rule_id == "no_subdocument_template_form" for i in errors_only(issues))

    def test_legacy_prefix_in_pine_is_error(self, validator_oba):
        toks = _toks("@[JW_Respondent.first.NameLastName]")
        issues = validator_oba.validate_stream(toks)
        assert any(i.rule_id == "no_legacy_entity_prefix_in_pine" for i in errors_only(issues))


# ─── vocabulary check ─────────────────────────────────────────────────────

class TestVocabulary:
    def test_known_oba_entity_passes(self, validator_oba):
        toks = _toks("@[Respondent.first.NameLastName]")
        warnings = warnings_only(validator_oba.validate_stream(toks))
        assert not any(w.rule_id == "unknown_entity" for w in warnings)

    def test_unknown_capitalised_base_emits_warning(self, validator_oba):
        # 'TotallyMadeUpEntity' is capitalised and not in OBA vocab.
        toks = _toks("@[TotallyMadeUpEntity.first.Name]")
        warnings = warnings_only(validator_oba.validate_stream(toks))
        assert any(w.rule_id == "unknown_entity" for w in warnings)

    def test_lowercase_base_is_quiet(self, validator_oba):
        # Lowercase chain bases (loop vars, templates) do not trigger.
        toks = _toks("@[c.SystemStatuteOffense]")
        warnings = warnings_only(validator_oba.validate_stream(toks))
        assert not any(w.rule_id == "unknown_entity" for w in warnings)

    def test_builtin_passes(self, validator_oba):
        toks = _toks("@[builtin.today.FormatDate(preset1)]")
        warnings = warnings_only(validator_oba.validate_stream(toks))
        assert not any(w.rule_id == "unknown_entity" for w in warnings)

    def test_foreach_loop_var_in_scope(self, validator_oba):
        # Charges.ForEach(c) introduces 'c' as a loop variable;
        # @[c.SystemStatuteOffense] should be quiet inside that block.
        toks = _toks(
            "@[Charges.ForEach(c)]",
            "@[c.SystemStatuteOffense]",
            "@[Charges.EndForEach]",
        )
        issues = validator_oba.validate_stream(toks)
        # No structural errors, no unknown_entity warnings.
        assert errors_only(issues) == []
        assert not any(w.rule_id == "unknown_entity" for w in warnings_only(issues))


# ─── structural balance ───────────────────────────────────────────────────

class TestStructural:
    def test_balanced_if_endif(self, validator_oba):
        toks = _toks(
            "@[If(@[Respondent.Any()] == true)]",
            "@[Respondent.first.NameLastName]",
            "@[EndIf]",
        )
        errs = errors_only(validator_oba.validate_stream(toks))
        # No structural errors.
        assert not any(e.rule_id == "unbalanced_control" for e in errs)

    def test_unclosed_if_is_error(self, validator_oba):
        toks = _toks(
            "@[If(@[Respondent.Any()] == true)]",
            "@[Respondent.first.NameLastName]",
            # missing EndIf
        )
        errs = errors_only(validator_oba.validate_stream(toks))
        assert any(e.rule_id == "unbalanced_control" for e in errs)

    def test_endif_without_if_is_error(self, validator_oba):
        toks = _toks("@[EndIf]")
        errs = errors_only(validator_oba.validate_stream(toks))
        assert any(e.rule_id == "unbalanced_control" for e in errs)

    def test_balanced_method_form_foreach(self, validator_oba):
        toks = _toks(
            "@[Charges.ForEach(c)]",
            "@[c.SystemStatuteOffense]",
            "@[Charges.EndForEach]",
        )
        errs = errors_only(validator_oba.validate_stream(toks))
        assert not any(e.rule_id == "unbalanced_control" for e in errs)


# ─── single-token API ─────────────────────────────────────────────────────

class TestSingleToken:
    def test_validate_one(self, validator_oba):
        tok = pine_parser.parse("@[Respondent.first.NameLastName]")
        issues = validator_oba.validate_token(tok, index=0)
        assert errors_only(issues) == []

    def test_validate_one_catches_lint(self, validator_oba):
        tok = pine_parser.parse("@[ProsNum.first.Number.SetCasing(Upper)]")
        issues = validator_oba.validate_token(tok)
        assert any(i.rule_id == "no_setcasing_on_prosnum"
                   for i in errors_only(issues))


# ─── default construction ─────────────────────────────────────────────────

class TestDefaults:
    def test_no_agency_skips_vocabulary(self):
        # Validator with no agency should still run lint + structural,
        # but skip vocabulary checks (no allow-list to compare against).
        v = Validator(lint_rules=load_lint_rules(), agency=None)
        toks = _toks("@[TotallyMadeUpEntity.first.Name]")
        issues = v.validate_stream(toks)
        assert not any(i.rule_id == "unknown_entity" for i in issues)

    def test_no_lint_uses_default(self):
        # Construct without explicit lint_rules; default loads from disk.
        v = Validator(agency=load_agency_overrides("oba"))
        toks = _toks("@[ProsNum.first.Number.SetCasing(Upper)]")
        # Default lint rules still flag the SetCasing issue.
        assert any(i.rule_id == "no_setcasing_on_prosnum"
                   for i in errors_only(v.validate_stream(toks)))

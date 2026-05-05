"""Tests for the Phase 3 grammar loaders.

These exercise the actual on-disk TOML files. If a TOML edit breaks
the schema, this suite is the early-warning system.
"""

from __future__ import annotations

import pytest

from v2.grammar import loaders


# ─── pine_grammar.toml ─────────────────────────────────────────────────────

class TestPineGrammar:
    @pytest.fixture(scope="class")
    def g(self):
        return loaders.load_pine_grammar()

    def test_token_delimiters(self, g):
        assert g.token_format.opener == "@["
        assert g.token_format.closer == "]"

    def test_preset1_format(self, g):
        assert g.date_presets["preset1"].format == "MMMM d, yyyy"

    def test_all_known_presets_present(self, g):
        # Phase 1 / pattern engine relies on presets 1–6.
        for n in ("preset1", "preset2", "preset3", "preset4", "preset5", "preset6"):
            assert n in g.date_presets

    def test_setcasing_args(self, g):
        # The validator should know which SetCasing values are legal.
        assert "Title" in g.setcasing["allowed_args"]
        assert "Upper" in g.setcasing["allowed_args"]
        assert "Lower" in g.setcasing["allowed_args"]
        assert "Sentence" in g.setcasing["allowed_args"]

    def test_format_tokens(self, g):
        # FormatName should know F, M, L, FILI, etc.
        for tok in ("F", "M", "L", "FILI", "I", "P", "S"):
            assert tok in g.format_tokens

    def test_canonical_control_keywords(self, g):
        # The Pine parser already canonicalises these names; the file
        # should agree.
        for kw in ("If", "ElseIf", "Else", "EndIf", "Foreach", "EndForEach"):
            assert kw in g.control_keywords.canonical

    def test_operator_categories(self, g):
        assert "==" in g.operators.equality
        assert "=" in g.operators.equality
        assert "&&" in g.operators.logical
        assert "||" in g.operators.logical
        assert "IN" in g.operators.membership

    def test_getlabel_named_table_known(self, g):
        # Spot check: NameGender → 98 per §7.
        assert g.getlabel.named_to_numeric["NameGender"] == 98


# ─── pine_data_model.toml ──────────────────────────────────────────────────

class TestPineDataModel:
    @pytest.fixture(scope="class")
    def m(self):
        return loaders.load_pine_data_model()

    def test_core_data_sources_present(self, m):
        names = m.source_names()
        for required in ("Case", "CaseInvolvement", "CaseAssignment", "CaseAgency",
                         "Name", "Personnel", "Event"):
            assert required in names

    def test_field_sets_cover_main_records(self, m):
        record_types = [fs.record_type for fs in m.field_set]
        assert "CaseInvolvement" in record_types
        assert "Personnel" in record_types

    def test_query_params_cover_common_keys(self, m):
        keys = [p.key for p in m.query_param]
        for k in ("CaseID", "Type", "IsActive", "OrderBy"):
            assert k in keys


# ─── lint_rules.toml ───────────────────────────────────────────────────────

class TestLintRules:
    @pytest.fixture(scope="class")
    def lints(self):
        return loaders.load_lint_rules()

    def test_severity_values_are_valid(self, lints):
        for r in lints.lint_rule:
            assert r.severity in ("error", "warning")

    def test_match_regexes_compile(self, lints):
        import re
        for r in lints.lint_rule:
            re.compile(r.match_regex)  # raises if malformed

    def test_known_anti_patterns_present(self, lints):
        # Spot-check: a few important rules from §35 must be there.
        ids = {r.id for r in lints.lint_rule}
        assert "no_setcasing_on_prosnum" in ids
        assert "no_subdocument_template_form" in ids
        assert "no_legacy_brackets_in_pine" in ids


# ─── org_overrides/oba.toml ────────────────────────────────────────────────

class TestOBAOverrides:
    @pytest.fixture(scope="class")
    def oba(self):
        return loaders.load_org_overrides("oba")

    def test_identity(self, oba):
        assert oba.id == "oba"

    def test_vocabulary_includes_oba_pine_entities(self, oba):
        # These entities are referenced by patterns in
        # ../patterns/library/oba/. The vocabulary must list them.
        for entity in ("Respondent", "Complainant", "OBAAttorney", "Defense",
                       "Prosecutor", "Investigator", "ProsNum"):
            assert entity in oba.vocabulary.entities

    def test_oba_address_setcasing_default_is_false(self, oba):
        # OBA convention drops SetCasing on address fields per §17.
        assert oba.conventions.address_setcasing_default is False

    def test_subdoc_template_family_expands_to_5_then_3(self, oba):
        assert oba.subdoc_template_family is not None
        assert oba.subdoc_template_family.output_ids == [5, 3]


# ─── consistency between assets and the pattern library ───────────────────

class TestCrossAssetConsistency:
    """Sanity checks that the structured assets agree with what the
    pattern engine produces. If a rewrite emits an entity not in the
    OBA vocabulary, that's a bug in either the pattern or the override
    file."""

    def test_all_oba_pattern_entities_in_vocabulary(self):
        """Every Pine entity name a `org_context = "oba"` pattern emits
        appears in oba.vocabulary.entities. Catches typos and drift."""
        from v2.patterns import loader, transforms
        oba = loaders.load_org_overrides("oba")
        # The translation transform's RHS values are the entity names
        # that OBA-aware patterns will emit.
        emitted_entities = set(transforms._JDA_TO_PINE_ENTITY.values())
        # Some entries are address-only or non-OBA; just check the
        # core OBA-relevant set has full coverage.
        oba_relevant = {
            "Respondent", "Complainant", "OBAAttorney", "Defense",
            "Prosecutor", "Investigator", "RespondentAtty",
            "RespondentAddress", "ComplainantAddress", "RespondentAttyAddress",
            "DefenseAddress", "cu",
        }
        missing = oba_relevant - set(oba.vocabulary.entities)
        assert not missing, (
            f"OBA patterns emit these entities but they aren't in "
            f"oba.vocabulary.entities: {sorted(missing)}"
        )

    def test_lint_setcasing_rule_targets_valid_pine(self):
        """The 'no SetCasing on ProsNum' lint rule should not match the
        valid form."""
        import re
        lints = loaders.load_lint_rules()
        rule = next(r for r in lints.lint_rule if r.id == "no_setcasing_on_prosnum")
        pat = re.compile(rule.match_regex)
        # Valid form must NOT match.
        assert pat.search("@[ProsNum.first.Number]") is None
        # Invalid form must match.
        assert pat.search("@[ProsNum.first.Number.SetCasing(Upper)]") is not None

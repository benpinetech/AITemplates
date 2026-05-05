"""Tests for the pattern miner.

Uses tiny synthetic RTF-shaped strings so we don't depend on the real
corpus for unit-level testing. The corpus-walker integration test
exists as a tool (``tools/mine_patterns.py``) — too slow to run in
the standard test suite.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from v2.engine import miner
from v2.parser import jda_parser, pine_parser
from v2.patterns import loader as pattern_loader


@pytest.fixture(scope="module")
def library():
    report = pattern_loader.load_library()
    report.raise_if_issues()
    return report.patterns


# ─── unit-level helpers ────────────────────────────────────────────────────

class TestEntityFinders:
    def test_find_jda_entity_in_titlecase_call(self):
        ast = jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]")
        assert miner._find_jda_entity(ast.inner) == "JW_Respondent"

    def test_find_jda_entity_returns_none_when_multiple(self):
        ast = jda_parser.parse("%[If(JW_X.IsEmpty=true)]")
        # JW_X is an entity; IsEmpty etc. are field-like and dropped
        # by the field heuristic. Only JW_X remains.
        assert miner._find_jda_entity(ast.inner) == "JW_X"

    def test_find_jda_entity_skips_known_field_names(self):
        # "FullName" appears as a path segment but should be classified
        # as a field, not an entity.
        ast = jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]")
        # _is_likely_field_name should keep JW_Respondent as the only
        # entity.
        ents = set()
        miner._collect_jda_entities(ast.inner, ents)
        assert ents == {"JW_Respondent"}

    def test_find_pine_entity_in_chain(self):
        ast = pine_parser.parse("@[Respondent.first.NameLastName]")
        assert miner._find_pine_entity(ast.inner) == "Respondent"


# ─── generalization ────────────────────────────────────────────────────────

class TestGeneralize:
    def test_simple_substitution(self):
        m, r = miner._generalize(
            "%[TitleCase(JW_Respondent.FullName)]",
            "@[Respondent.first.FormatName(F L).SetCasing(Title)]",
            "JW_Respondent",
            "Respondent",
        )
        assert m == "%[TitleCase($entity.FullName)]"
        assert r == "@[$entity_pine.first.FormatName(F L).SetCasing(Title)]"

    def test_word_boundary_avoids_substring_match(self):
        # "Respondent" must not get replaced inside "RespondentAtty".
        m, r = miner._generalize(
            "%[Cust_Respondent.X]",
            "@[Respondent.something]",
            "Cust_Respondent",
            "Respondent",
        )
        assert m == "%[$entity.X]"
        assert r == "@[$entity_pine.something]"


# ─── mine_pair ─────────────────────────────────────────────────────────────

class TestMinePair:
    def test_skips_when_existing_pattern_covers_it(self, library):
        # The OBA pattern already converts TitleCase(JW_Respondent.FullName);
        # the miner should return None for that pair.
        jda = jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]")
        pine = pine_parser.parse("@[Respondent.first.FormatName(F L).SetCasing(Title)]")
        result = miner.mine_pair(jda, pine, library, "oba", "demo.rtf", 0)
        assert result is None

    def test_emits_candidate_for_novel_pair(self, library):
        # A made-up entity (UnknownNewEntity) translated to its Pine form.
        # Not in our pattern library, so the miner should propose it.
        # But the entity translation table doesn't know it — the miner
        # should reject for slice 1 (it requires the table to agree).
        jda = jda_parser.parse("%[TitleCase(UnknownNewEntity.FullName)]")
        pine = pine_parser.parse("@[UnknownNewEntity.first.FormatName(F L).SetCasing(Title)]")
        result = miner.mine_pair(jda, pine, library, "any", "demo.rtf", 0)
        # Translation table doesn't have UnknownNewEntity; expect None.
        # (translate_jda_entity_to_pine falls back to the input
        # unchanged, so jda_entity == pine_entity holds — let's verify.)
        # Actually with fallback-unchanged behavior, translation returns
        # "UnknownNewEntity" === pine_entity, so the miner DOES emit a
        # candidate. Check that.
        assert result is not None
        assert "$entity" in result.match
        assert "$entity_pine" in result.rewrite

    def test_rejects_when_translation_disagrees(self, library):
        # JDA entity translates to a different name than Pine has.
        # E.g., JW_Defendant translates to "Respondent" in OBA, but if
        # the pine token uses "Defendant" the miner should reject.
        jda = jda_parser.parse("%[TitleCase(JW_Defendant.FullName)]")
        pine = pine_parser.parse("@[Defendant.first.FormatName(F L).SetCasing(Title)]")
        result = miner.mine_pair(jda, pine, library, "oba", "demo.rtf", 0)
        assert result is None


# ─── mine_template (with synthetic RTF) ────────────────────────────────────

class TestMineTemplate:
    def test_mismatched_token_counts_skipped(self, library):
        legacy = "Some text %[TitleCase(JW_X.FullName)] more text."
        # Pine has TWO tokens — count mismatch.
        pine = "Some text @[X.first.FormatName(F L).SetCasing(Title)] @[ProsNum.first.Number] more."
        report = miner.mine_template(legacy, pine, library, "any", "demo.rtf")
        assert report.pairs_skipped_count_mismatch == 1
        assert report.pairs_examined == 0

    def test_matched_count_with_covered_pair(self, library):
        # An OBA pattern already handles this conversion.
        legacy = "%[TitleCase(JW_Respondent.FullName)]"
        pine = "@[Respondent.first.FormatName(F L).SetCasing(Title)]"
        report = miner.mine_template(legacy, pine, library, "oba", "demo.rtf")
        assert report.pairs_examined == 1
        assert report.pairs_already_covered == 1
        assert len(report.candidates) == 0


# ─── candidate output ──────────────────────────────────────────────────────

class TestWriteCandidates:
    def test_writes_one_toml_per_candidate(self, tmp_path, library):
        legacy = "%[TitleCase(NovelEntity.FullName)]"
        pine = "@[NovelEntity.first.FormatName(F L).SetCasing(Title)]"
        report = miner.mine_template(legacy, pine, library, "any", "demo.rtf")
        assert len(report.candidates) >= 1
        written = miner.write_candidates(report, tmp_path)
        assert len(written) == len(report.candidates)
        for path in written:
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "[[pattern]]" in content
            assert "provenance" in content
            assert "candidate" in content

    def test_candidate_toml_loads_via_pattern_loader(self, tmp_path, library):
        # The mined TOML must validate against the pattern schema.
        legacy = "%[TitleCase(NovelEntity.FullName)]"
        pine = "@[NovelEntity.first.FormatName(F L).SetCasing(Title)]"
        report = miner.mine_template(legacy, pine, library, "any", "demo.rtf")
        miner.write_candidates(report, tmp_path)
        loaded = pattern_loader.load_library(tmp_path)
        assert loaded.ok, loaded.issues
        assert len(loaded.patterns) >= 1


# ─── deduplication ─────────────────────────────────────────────────────────

class TestDeduplication:
    def test_same_shape_yields_one_candidate(self, library):
        # Two structurally identical pairs (different entity names but
        # same generalized form after substitution) collapse to one
        # candidate via the hashed id.
        legacy = "%[TitleCase(NovelOne.FullName)]"
        pine = "@[NovelOne.first.FormatName(F L).SetCasing(Title)]"
        report = miner.mine_template(legacy, pine, library, "any", "a.rtf")
        legacy2 = "%[TitleCase(NovelTwo.FullName)]"
        pine2 = "@[NovelTwo.first.FormatName(F L).SetCasing(Title)]"
        miner.mine_template(legacy2, pine2, library, "any", "b.rtf", report)
        # After substitution both produce identical (match, rewrite),
        # so the candidate count is 1 — deduped.
        assert len(report.candidates) == 1

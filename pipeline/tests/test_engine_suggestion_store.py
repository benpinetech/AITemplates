"""Tests for the LLM-suggestion store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.engine import suggestion_store
from pipeline.parser import pine_parser


# ─── basic accept / reject ─────────────────────────────────────────────────

class TestAccept:
    def test_writes_toml_with_expected_id(self, tmp_path):
        path = suggestion_store.accept_suggestion(
            "%[TitleCase(JW_Mystery.FullName)]",
            "@[Mystery.first.FormatName(F L).SetCasing(Title)]",
            org="oba",
            root=tmp_path,
        )
        assert path.exists()
        # Default scope is "global" → verified/oba/global/<id>.toml.
        assert path.parent.name == "global"
        assert path.parent.parent.name == "oba"
        assert path.parent.parent.parent.name == "verified"
        assert path.suffix == ".toml"
        # File name is the stable hash-based id.
        assert path.stem.startswith("verified_")

    def test_idempotent(self, tmp_path):
        a = suggestion_store.accept_suggestion(
            "%[A]", "@[B]", org="oba", root=tmp_path,
        )
        b = suggestion_store.accept_suggestion(
            "%[A]", "@[B]", org="oba", root=tmp_path,
        )
        assert a == b   # same path, just overwritten

    def test_different_pairs_get_different_ids(self, tmp_path):
        a = suggestion_store.accept_suggestion(
            "%[A]", "@[B]", org="oba", root=tmp_path,
        )
        b = suggestion_store.accept_suggestion(
            "%[A]", "@[C]", org="oba", root=tmp_path,
        )
        assert a != b

    def test_rejects_invalid_org_slug(self, tmp_path):
        with pytest.raises(ValueError):
            suggestion_store.accept_suggestion(
                "%[A]", "@[B]", org="../escape", root=tmp_path,
            )


class TestReject:
    def test_appends_jsonl_record(self, tmp_path):
        suggestion_store.reject_suggestion(
            "%[Bad]", "@[wrong]", org="oba",
            reason="LLM hallucinated entity",
            source_template="demo.rtf",
            root=tmp_path,
        )
        log = tmp_path / "rejected.log"
        assert log.exists()
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(records) == 1
        assert records[0]["jda"] == "%[Bad]"
        assert records[0]["pine"] == "@[wrong]"
        assert records[0]["org"] == "oba"
        assert records[0]["reason"] == "LLM hallucinated entity"

    def test_multiple_rejects_append(self, tmp_path):
        suggestion_store.reject_suggestion("%[A]", "@[a]", org="oba", root=tmp_path)
        suggestion_store.reject_suggestion("%[B]", "@[b]", org="oba", root=tmp_path)
        log = tmp_path / "rejected.log"
        records = log.read_text().splitlines()
        assert len(records) == 2


class TestIsAccepted:
    def test_round_trip(self, tmp_path):
        assert not suggestion_store.is_suggestion_accepted(
            "%[X]", "@[y]", "oba", root=tmp_path,
        )
        suggestion_store.accept_suggestion("%[X]", "@[y]", "oba", root=tmp_path)
        assert suggestion_store.is_suggestion_accepted(
            "%[X]", "@[y]", "oba", root=tmp_path,
        )


# ─── load round-trip ───────────────────────────────────────────────────────

class TestLoad:
    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert suggestion_store.load_verified_for_org("oba", root=tmp_path) == []

    def test_loaded_suggestion_has_expected_match_and_rewrite(self, tmp_path):
        # Accept a suggestion, load it back, and confirm the Pattern fields
        # have the exact strings needed for the pipeline's suggestion lookup.
        suggestion_store.accept_suggestion(
            "%[TitleCase(SomeNovelEntity.FullName)]",
            "@[SomeNovelEntity.first.FormatName(F L).SetCasing(Title)]",
            org="oba",
            root=tmp_path,
        )
        verified = suggestion_store.load_verified_for_org("oba", root=tmp_path)
        assert len(verified) == 1
        p = verified[0]
        assert p.match == "%[TitleCase(SomeNovelEntity.FullName)]"
        assert p.rewrite == "@[SomeNovelEntity.first.FormatName(F L).SetCasing(Title)]"


# ─── special characters ────────────────────────────────────────────────────

class TestSpecialCharacters:
    def test_subdocument_path_with_backslash(self, tmp_path):
        # Subdocument paths contain backslashes — verify TOML quoting
        # round-trips them.
        path = suggestion_store.accept_suggestion(
            "%[Subdocument(Template\\NovelPath)]",
            "@[SubDocument(99)]",
            org="oba",
            root=tmp_path,
        )
        verified = suggestion_store.load_verified_for_org("oba", root=tmp_path)
        assert len(verified) == 1
        # The match string in the loaded pattern is the literal source.
        assert verified[0].match == "%[Subdocument(Template\\NovelPath)]"

    def test_apostrophe_in_pine_via_string_literal(self, tmp_path):
        # Pine string literals use single quotes; the suggestion
        # writer must use a triple-quoted TOML string for content
        # containing apostrophes.
        suggestion_store.accept_suggestion(
            "%[X.Gender]",
            "@[If('@[X.Gender]' == 'M')]",
            org="oba",
            root=tmp_path,
        )
        verified = suggestion_store.load_verified_for_org("oba", root=tmp_path)
        assert len(verified) == 1
        assert verified[0].rewrite == "@[If('@[X.Gender]' == 'M')]"

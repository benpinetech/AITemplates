"""Tests for batch mode (folder → confirm mappings → folder).

All offline — no LLM. Collect runs with ``converter=None`` so unmatched
tokens surface for manual mapping; apply is deterministic by design.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.engine import suggestion_store
from pipeline.tools import batch

_GROUND_TRUTH_LEGACY = (
    Path(__file__).resolve().parents[2]
    / "ground_truth"
    / "evaluation_templates"
    / "jda_to_pine"
    / "legacy"
)


@pytest.fixture
def input_dir(tmp_path: Path) -> Path:
    """A small batch folder built from real corpus templates (copied, so
    ground_truth stays read-only)."""
    src = tmp_path / "in"
    src.mkdir()
    for name in ("219 - Plea Deadline.rtf", "215 - Letterhead.rtf"):
        gt = _GROUND_TRUTH_LEGACY / name
        if gt.exists():
            (src / name).write_text(gt.read_text(encoding="utf-8", errors="replace"))
    return src


class TestCollect:
    def test_dedupes_tokens_across_files(self, input_dir):
        bundle = batch.collect_mappings(input_dir, "oba", converter=None)
        assert bundle["schema"] == "jda-pine-batch-collect/v1"
        jdas = [m["jda"] for m in bundle["mappings"]]
        # Unique — dedup collapsed every repeated occurrence.
        assert len(jdas) == len(set(jdas))
        # 219 alone has 15 fillpoints but only 13 distinct (FullName ×2).
        # Deduping must make unique_tokens < total_tokens.
        assert bundle["stats"]["unique_tokens"] < bundle["stats"]["total_tokens"]

    def test_repeated_token_counts_occurrences_and_files(self, input_dir):
        bundle = batch.collect_mappings(input_dir, "oba", converter=None)
        by_jda = {m["jda"]: m for m in bundle["mappings"]}
        # FullName appears twice in 219 — one deduped row, count >= 2.
        full = by_jda.get("%[JW_Defendant.FullName]")
        assert full is not None
        assert full["count"] >= 2
        assert "219 - Plea Deadline.rtf" in full["files"]

    def test_no_llm_leaves_tokens_unmatched(self, input_dir):
        bundle = batch.collect_mappings(input_dir, "oba", converter=None)
        # With no verified suggestions and no LLM, everything is unmatched.
        assert bundle["stats"]["llm"] == 0
        assert bundle["stats"]["unmatched"] == bundle["stats"]["unique_tokens"]
        assert all(m["provenance"] == "unmatched" for m in bundle["mappings"])

    def test_verified_suggestion_is_used_over_unmatched(self, input_dir, tmp_path):
        root = tmp_path / "suggestions"
        suggestion_store.accept_suggestion(
            "%[JW_CaseDetails.CourtNum]",
            "@[CourtNum.first.Number]",
            "oba",
            root=root,
        )
        bundle = batch.collect_mappings(
            input_dir, "oba", converter=None, suggestions_root=root
        )
        by_jda = {m["jda"]: m for m in bundle["mappings"]}
        row = by_jda["%[JW_CaseDetails.CourtNum]"]
        assert row["provenance"] == "suggestion"
        assert row["pine_tokens"] == ["@[CourtNum.first.Number]"]
        assert bundle["stats"]["suggestion"] >= 1

    def test_mappings_sorted_by_impact(self, input_dir):
        bundle = batch.collect_mappings(input_dir, "oba", converter=None)
        counts = [m["count"] for m in bundle["mappings"]]
        assert counts == sorted(counts, reverse=True)


class TestApply:
    def test_writes_outputs_and_matches_confirmed(self, input_dir, tmp_path):
        out = tmp_path / "out"
        mappings = [
            {
                "jda": "%[JW_CaseDetails.CourtNum]",
                "pine_tokens": ["@[CourtNum.first.Number]"],
            }
        ]
        bundle = batch.apply_mappings(input_dir, out, "oba", mappings)
        assert bundle["schema"] == "jda-pine-batch-apply/v1"
        # Every input file produced an output file.
        assert bundle["stats"]["written"] == bundle["stats"]["files"]
        for name in ("219 - Plea Deadline.rtf",):
            assert (out / name).exists()
        # The confirmed token converted; its Pine text is in the output.
        converted = (out / "219 - Plea Deadline.rtf").read_text(encoding="utf-8")
        assert "@[CourtNum.first.Number]" in converted
        assert bundle["stats"]["matched"] >= 1

    def test_unmatched_tokens_reported_per_file(self, input_dir, tmp_path):
        out = tmp_path / "out"
        bundle = batch.apply_mappings(input_dir, out, "oba", mappings=[])
        row = next(r for r in bundle["files"] if r["name"] == "219 - Plea Deadline.rtf")
        # No mappings → the file's fillpoints are all unmatched and listed.
        assert row["matched"] == 0
        assert row["unmatched"] > 0
        assert "%[JW_CaseDetails.CourtNum]" in row["unmatched_tokens"]

    def test_empty_pine_row_is_skipped_not_dropped(self, input_dir, tmp_path):
        out = tmp_path / "out"
        # A row the human left blank must NOT match (leave it unmatched),
        # rather than convert the token to nothing.
        mappings = [{"jda": "%[JW_CaseDetails.CourtNum]", "pine_tokens": []}]
        bundle = batch.apply_mappings(input_dir, out, "oba", mappings)
        row = next(r for r in bundle["files"] if r["name"] == "219 - Plea Deadline.rtf")
        assert "%[JW_CaseDetails.CourtNum]" in row["unmatched_tokens"]

    def test_persist_writes_agency_suggestions(self, input_dir, tmp_path):
        out = tmp_path / "out"
        root = tmp_path / "suggestions"
        mappings = [
            {
                "jda": "%[JW_CaseDetails.CourtNum]",
                "pine_tokens": ["@[CourtNum.first.Number]"],
            }
        ]
        bundle = batch.apply_mappings(
            input_dir, out, "oba", mappings, persist=True, suggestions_root=root
        )
        assert bundle["stats"]["persisted"] == 1
        # The persisted mapping is now loadable for the agency.
        loaded = suggestion_store.load_verified_for_agency("oba", root=root)
        assert any(
            p.match == "%[JW_CaseDetails.CourtNum]" for p in loaded
        ), [p.match for p in loaded]

    def test_drop_row_removes_token_from_output(self, input_dir, tmp_path):
        out = tmp_path / "out"
        mappings = [{"jda": "%[JW_CaseDetails.CourtNum]", "pine_tokens": [], "drop": True}]
        bundle = batch.apply_mappings(input_dir, out, "oba", mappings)
        converted = (out / "219 - Plea Deadline.rtf").read_text(encoding="utf-8")
        # The token is gone (converted to nothing) — and it is NOT reported
        # as unmatched, because dropping it was a deliberate decision.
        assert "JW_CaseDetails.CourtNum" not in converted
        row = next(r for r in bundle["files"] if r["name"] == "219 - Plea Deadline.rtf")
        assert "%[JW_CaseDetails.CourtNum]" not in row["unmatched_tokens"]
        assert row["matched"] >= 1

    def test_drop_persists_as_empty_suggestion(self, input_dir, tmp_path):
        out = tmp_path / "out"
        root = tmp_path / "suggestions"
        mappings = [{"jda": "%[JW_CaseDetails.CourtNum]", "pine_tokens": [], "drop": True}]
        batch.apply_mappings(
            input_dir, out, "oba", mappings, persist=True, suggestions_root=root
        )
        loaded = suggestion_store.load_verified_for_agency("oba", root=root)
        drop = next(p for p in loaded if p.match == "%[JW_CaseDetails.CourtNum]")
        assert (drop.rewrite_tokens() or []) == []  # a drop pattern

    def test_collect_marks_persisted_drop_as_drop(self, input_dir, tmp_path):
        root = tmp_path / "suggestions"
        suggestion_store.accept_suggestion(
            "%[JW_CaseDetails.CourtNum]", [], "oba", root=root
        )
        bundle = batch.collect_mappings(
            input_dir, "oba", converter=None, suggestions_root=root
        )
        row = next(m for m in bundle["mappings"] if m["jda"] == "%[JW_CaseDetails.CourtNum]")
        assert row["drop"] is True
        assert row["provenance"] == "suggestion"

    def test_progress_callback_fires(self, input_dir, tmp_path):
        collect_events = []
        batch.collect_mappings(
            input_dir, "oba", converter=None, on_progress=collect_events.append
        )
        assert any(e["phase"] == "scan" for e in collect_events)

        apply_events = []
        batch.apply_mappings(
            input_dir, tmp_path / "out", "oba", mappings=[],
            on_progress=apply_events.append,
        )
        assert any(e["phase"] == "apply" for e in apply_events)
        # Progress events carry a monotone done/total for a determinate bar.
        scans = [e for e in apply_events if e["phase"] == "apply"]
        assert all(e["total"] == len(scans) for e in scans)

    def test_apply_does_not_persist_by_default(self, input_dir, tmp_path):
        out = tmp_path / "out"
        root = tmp_path / "suggestions"
        mappings = [
            {
                "jda": "%[JW_CaseDetails.CourtNum]",
                "pine_tokens": ["@[CourtNum.first.Number]"],
            }
        ]
        bundle = batch.apply_mappings(
            input_dir, out, "oba", mappings, suggestions_root=root
        )
        assert bundle["stats"]["persisted"] == 0
        assert suggestion_store.load_verified_for_agency("oba", root=root) == []

"""Tests for the pattern loader and schema validation.

These tests use a tmp_path-built mini library so we can exercise both
success and failure cases (bad TOML, missing transforms, undeclared
holes, duplicate ids) without disturbing the real library.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from v2.patterns import loader


def _write(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return p


class TestLoaderHappy:
    def test_empty_library(self, tmp_path):
        report = loader.load_library(tmp_path)
        assert report.ok
        assert report.patterns == []

    def test_one_pattern(self, tmp_path):
        _write(
            tmp_path,
            "p.toml",
            """
            [[pattern]]
            id = "demo_titlecase"
            description = "demo"
            match = "%[TitleCase($entity.FullName)]"
            rewrite = "@[$entity_pine.first.FormatName(F L).SetCasing(Title)]"

            [pattern.holes.entity]
            kind = "path-segment"

            [pattern.holes.entity_pine]
            derive_from = "entity"
            transform = "translate_jda_entity_to_pine"
            """,
        )
        report = loader.load_library(tmp_path)
        assert report.ok, report.issues
        assert len(report.patterns) == 1
        assert report.patterns[0].id == "demo_titlecase"


class TestLoaderIssues:
    def test_duplicate_ids(self, tmp_path):
        _write(tmp_path, "a.toml", """
            [[pattern]]
            id = "dup"
            description = "first"
            match = "%[X]"
            rewrite = "@[X]"
        """)
        _write(tmp_path, "b.toml", """
            [[pattern]]
            id = "dup"
            description = "second"
            match = "%[Y]"
            rewrite = "@[Y]"
        """)
        report = loader.load_library(tmp_path)
        assert not report.ok
        assert any("duplicate pattern id" in i.message for i in report.issues)

    def test_undeclared_hole(self, tmp_path):
        _write(tmp_path, "a.toml", """
            [[pattern]]
            id = "missing_decl"
            description = "uses $foo without declaring it"
            match = "%[TitleCase($foo.FullName)]"
            rewrite = "@[$foo.first.NameLastName]"
        """)
        report = loader.load_library(tmp_path)
        assert not report.ok
        assert any("not declared" in i.message for i in report.issues)

    def test_unknown_transform(self, tmp_path):
        _write(tmp_path, "a.toml", """
            [[pattern]]
            id = "bad_transform"
            description = "..."
            match = "%[X.$y]"
            rewrite = "@[$z]"

            [pattern.holes.y]
            kind = "path-segment"

            [pattern.holes.z]
            derive_from = "y"
            transform = "this_is_not_real"
        """)
        report = loader.load_library(tmp_path)
        assert not report.ok
        assert any("unknown transform" in i.message for i in report.issues)

    def test_unknown_rewrite_function(self, tmp_path):
        _write(tmp_path, "a.toml", """
            [[pattern]]
            id = "bad_fn"
            description = "..."
            match = "%[Subdocument($p)]"
            rewrite_function = "no_such_fn"

            [pattern.holes.p]
            kind = "subdoc-path"
        """)
        report = loader.load_library(tmp_path)
        assert not report.ok
        assert any("unknown rewrite_function" in i.message for i in report.issues)

    def test_bad_toml(self, tmp_path):
        _write(tmp_path, "a.toml", "this is = not = valid TOML at all")
        report = loader.load_library(tmp_path)
        assert not report.ok
        assert any("TOML parse error" in i.message for i in report.issues)


class TestPatternsForOrg:
    def test_filters_and_priority_order(self):
        from v2.patterns.schema import Pattern
        a = Pattern(id="a", description="", match="%[A]", rewrite="@[A]",
                   org_context="any", priority=50)
        b = Pattern(id="b", description="", match="%[B]", rewrite="@[B]",
                   org_context="oba", priority=200)
        c = Pattern(id="c", description="", match="%[C]", rewrite="@[C]",
                   org_context="criminal-pd", priority=100)

        out = loader.patterns_for_org([a, b, c], "oba")
        assert [p.id for p in out] == ["b", "a"]   # b first (priority 200), then a (any/50)


class TestRealLibrary:
    """Smoke test against the real seed library — every TOML must load
    without issues.
    """

    def test_real_library_loads_clean(self):
        report = loader.load_library()
        assert report.ok, "Real library has issues:\n" + "\n".join(
            f"  {i.file}: {i.pattern_id}: {i.message}" for i in report.issues
        )
        assert len(report.patterns) > 0

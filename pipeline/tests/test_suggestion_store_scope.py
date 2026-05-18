"""Scope-aware behaviour for the verified-suggestion store.

Covers:

  - file layout matches the chosen scope (global / by_template / by_audience)
  - load_verified_for_org filters by template_name / audience correctly
  - scope mismatch excludes a suggestion
  - same (jda, pine) at different scopes do not collide
  - scoped overrides get a higher priority than global suggestions, so
    a converter's "this pattern is wrong here" edit beats a seed pattern
  - legacy flat-layout files written by older versions still load
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.engine import suggestion_store as ss
from pipeline.patterns.loader import patterns_for_org


JDA = "%[TitleCase(JW_Mystery.FullName)]"
PINE = "@[Mystery.first.FormatName(F L).SetCasing(Title)]"


# ─── file layout per scope ────────────────────────────────────────────────────


class TestScopeFileLayout:
    def test_global_scope_writes_to_global_dir(self, tmp_path):
        path = ss.accept_suggestion(JDA, PINE, org="oba", root=tmp_path)
        assert path.parent.name == "global"
        assert path.parent.parent.name == "oba"

    def test_template_scope_writes_to_by_template(self, tmp_path):
        path = ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Letter to C.rtf"),
            root=tmp_path,
        )
        assert path.parent.name == "Letter to C.rtf"
        assert path.parent.parent.name == "by_template"

    def test_audience_scope_writes_to_by_audience(self, tmp_path):
        path = ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_AUDIENCE, "complainant"),
            root=tmp_path,
        )
        assert path.parent.name == "complainant"
        assert path.parent.parent.name == "by_audience"

    def test_template_scope_rejects_path_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            ss.accept_suggestion(
                JDA, PINE, org="oba",
                scope=(ss.SCOPE_TEMPLATE, "../escape"),
                root=tmp_path,
            )

    def test_unknown_scope_kind_raises(self, tmp_path):
        with pytest.raises(ValueError):
            ss.accept_suggestion(
                JDA, PINE, org="oba",
                scope=("nope", "x"),
                root=tmp_path,
            )


# ─── load filtering ───────────────────────────────────────────────────────────


class TestLoadFilters:
    def test_global_loads_without_template_or_audience(self, tmp_path):
        ss.accept_suggestion(JDA, PINE, org="oba", root=tmp_path)
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].match == JDA

    def test_template_scope_loads_only_when_template_matches(self, tmp_path):
        ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Letter to C.rtf"),
            root=tmp_path,
        )
        # Without the matching template_name: invisible.
        assert ss.load_verified_for_org("oba", root=tmp_path) == []
        # With a different template_name: still invisible.
        assert ss.load_verified_for_org(
            "oba", root=tmp_path, template_name="Other.rtf",
        ) == []
        # With the matching template_name: loaded.
        loaded = ss.load_verified_for_org(
            "oba", root=tmp_path, template_name="Letter to C.rtf",
        )
        assert len(loaded) == 1

    def test_audience_scope_loads_only_when_audience_matches(self, tmp_path):
        ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_AUDIENCE, "complainant"),
            root=tmp_path,
        )
        assert ss.load_verified_for_org("oba", root=tmp_path) == []
        assert ss.load_verified_for_org(
            "oba", root=tmp_path, audience="respondent",
        ) == []
        loaded = ss.load_verified_for_org(
            "oba", root=tmp_path, audience="complainant",
        )
        assert len(loaded) == 1

    def test_global_plus_scoped_load_together(self, tmp_path):
        # One global, one template-scoped, one audience-scoped.
        ss.accept_suggestion("%[A]", "@[a]", org="oba", root=tmp_path)
        ss.accept_suggestion(
            "%[B]", "@[b]", org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Doc.rtf"),
            root=tmp_path,
        )
        ss.accept_suggestion(
            "%[C]", "@[c]", org="oba",
            scope=(ss.SCOPE_AUDIENCE, "complainant"),
            root=tmp_path,
        )
        # No filters → only the global one is visible.
        assert {p.match for p in ss.load_verified_for_org("oba", root=tmp_path)} == {"%[A]"}
        # Matching template only → global + template.
        loaded = ss.load_verified_for_org(
            "oba", root=tmp_path, template_name="Doc.rtf",
        )
        assert {p.match for p in loaded} == {"%[A]", "%[B]"}
        # Matching audience only → global + audience.
        loaded = ss.load_verified_for_org(
            "oba", root=tmp_path, audience="complainant",
        )
        assert {p.match for p in loaded} == {"%[A]", "%[C]"}
        # Both → all three.
        loaded = ss.load_verified_for_org(
            "oba", root=tmp_path,
            template_name="Doc.rtf", audience="complainant",
        )
        assert {p.match for p in loaded} == {"%[A]", "%[B]", "%[C]"}


# ─── id / file collision avoidance ────────────────────────────────────────────


class TestScopeCollision:
    def test_same_pair_different_scopes_get_different_files(self, tmp_path):
        a = ss.accept_suggestion(JDA, PINE, org="oba", root=tmp_path)
        b = ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Doc.rtf"),
            root=tmp_path,
        )
        c = ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_AUDIENCE, "complainant"),
            root=tmp_path,
        )
        # Three distinct files; the global accept hasn't shadowed the
        # template or audience accepts.
        assert {a, b, c} == {a, b, c}
        assert len({a, b, c}) == 3

    def test_idempotent_within_scope(self, tmp_path):
        a = ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Doc.rtf"),
            root=tmp_path,
        )
        b = ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Doc.rtf"),
            root=tmp_path,
        )
        assert a == b


# ─── priority: scoped beats global beats seed ────────────────────────────────


class TestScopePriority:
    def test_scoped_override_outranks_global_and_seed(self, tmp_path):
        # A "seed" pattern at default priority 100.
        from pipeline.patterns.schema import Pattern
        seed = Pattern(
            id="seed", description="seed",
            org_context="oba", priority=100,
            match=JDA, rewrite="@[seed.output]",
        )
        ss.accept_suggestion(JDA, "@[global.output]", org="oba", root=tmp_path)
        ss.accept_suggestion(
            JDA, "@[scoped.output]", org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Doc.rtf"),
            root=tmp_path,
        )
        loaded = ss.load_verified_for_org(
            "oba", root=tmp_path, template_name="Doc.rtf",
        )
        combined = patterns_for_org([seed] + loaded, "oba")
        # patterns_for_org sorts by priority desc — the scoped override
        # should come first, then global, then seed.
        rewrites = [p.rewrite for p in combined]
        assert rewrites == ["@[scoped.output]", "@[global.output]", "@[seed.output]"]


# ─── is_suggestion_accepted respects scope ────────────────────────────────────


class TestIsAcceptedScope:
    def test_accept_at_template_scope_not_seen_at_global(self, tmp_path):
        ss.accept_suggestion(
            JDA, PINE, org="oba",
            scope=(ss.SCOPE_TEMPLATE, "Doc.rtf"),
            root=tmp_path,
        )
        assert not ss.is_suggestion_accepted(JDA, PINE, "oba", root=tmp_path)
        assert ss.is_suggestion_accepted(
            JDA, PINE, "oba",
            scope=(ss.SCOPE_TEMPLATE, "Doc.rtf"),
            root=tmp_path,
        )


# ─── legacy flat-layout backward-compat ──────────────────────────────────────


class TestLegacyLayout:
    def test_legacy_flat_toml_still_loads(self, tmp_path):
        # Hand-write a TOML at the old, pre-scope location so a user
        # who upgrades doesn't lose their saved overrides.
        legacy_dir = tmp_path / "verified" / "oba"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "verified_legacy.toml").write_text(
            "[[pattern]]\n"
            "id = 'verified_legacy'\n"
            "description = 'legacy flat'\n"
            "provenance = 'llm-generated'\n"
            "verification = 'verified'\n"
            "org_context = 'oba'\n"
            "priority = 150\n"
            f"match = '{JDA}'\n"
            f"rewrite = '{PINE}'\n",
            encoding="utf-8",
        )
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].id == "verified_legacy"

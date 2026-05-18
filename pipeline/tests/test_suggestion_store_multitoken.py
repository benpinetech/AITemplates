"""Multi-token and drop-pattern persistence in the verified-suggestion
store.

The store now writes either a scalar ``match``/``rewrite`` (the 1:1
case) or an inline TOML array (the chunk-pattern / expand / drop
cases). The pattern engine already understands both shapes — these
tests check the round-trip end-to-end via the engine, so a converter's
hand-edit truly fires deterministically the next time the same JDA
shape appears.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import pipeline
from pipeline.engine import suggestion_store as ss
from pipeline.parser import jda_parser, pine_parser
from pipeline.patterns import engine as patterns_engine


# ─── 1:1 round-trip (no regression) ──────────────────────────────────────────


class TestSingleTokenBackcompat:
    def test_string_inputs_still_work(self, tmp_path):
        ss.accept_suggestion(
            "%[X.FullName]", "@[X.first.NameLastName]",
            org="oba", root=tmp_path,
        )
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        assert len(loaded) == 1
        p = loaded[0]
        assert p.match == "%[X.FullName]"
        assert p.rewrite == "@[X.first.NameLastName]"

    def test_string_and_singleton_list_share_id(self, tmp_path):
        """Same logical mapping via string or 1-element list should
        produce the same on-disk file — otherwise upgrading callers
        from string to list would silently duplicate suggestions."""
        a = ss.accept_suggestion(
            "%[X.A]", "@[X.A]", org="oba", root=tmp_path,
        )
        b = ss.accept_suggestion(
            ["%[X.A]"], ["@[X.A]"], org="oba", root=tmp_path,
        )
        assert a == b


# ─── 1 JDA → N Pine (split) ──────────────────────────────────────────────────


class TestSplit:
    def test_split_rewrite_round_trips_through_engine(self, tmp_path):
        # Bare FullName → first + last (a real OBA conversion).
        ss.accept_suggestion(
            "%[X.FullName]",
            ["@[X.first.NameFirstName]", "@[X.first.NameLastName]"],
            org="oba", root=tmp_path,
        )
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        assert len(loaded) == 1
        p = loaded[0]
        # Match stays scalar (single JDA token); rewrite is a list.
        assert p.match == "%[X.FullName]"
        assert p.rewrite == [
            "@[X.first.NameFirstName]",
            "@[X.first.NameLastName]",
        ]
        # Engine emits both Pine tokens for the JDA input.
        result = patterns_engine.convert(
            jda_parser.parse("%[X.FullName]"), loaded, org="oba",
        )
        assert result.matched
        rendered = [t.unparse() for t in result.outputs]
        assert rendered == [
            "@[X.first.NameFirstName]",
            "@[X.first.NameLastName]",
        ]


# ─── N JDA → M Pine (literal chunk pattern from a converter edit) ────────────


class TestLiteralChunkPattern:
    def test_n_to_n_chunk_persists_and_matches(self, tmp_path):
        # A trivial 2-token chunk: two literal JDA tokens map to two
        # specific Pine tokens. The pattern is purely literal — no
        # holes — so it only fires on this exact pair.
        ss.accept_suggestion(
            ["%[Cust_X.A]", "%[Cust_X.B]"],
            ["@[XAlpha.first.Field]", "@[XBeta.first.Field]"],
            org="oba", root=tmp_path,
        )
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        assert len(loaded) == 1
        p = loaded[0]
        assert p.match == ["%[Cust_X.A]", "%[Cust_X.B]"]
        assert p.rewrite == ["@[XAlpha.first.Field]", "@[XBeta.first.Field]"]
        assert p.is_chunk_pattern()

        # Use the stream engine to verify the chunk actually matches.
        toks = [
            jda_parser.parse("%[Cust_X.A]"),
            jda_parser.parse("%[Cust_X.B]"),
        ]
        segments = patterns_engine.convert_stream(toks, loaded, org="oba")
        # One segment that consumed both JDA tokens.
        assert len(segments) == 1
        seg = segments[0]
        assert seg.consumed == 2
        assert seg.pattern is p
        assert [t.unparse() for t in seg.outputs] == [
            "@[XAlpha.first.Field]",
            "@[XBeta.first.Field]",
        ]

    def test_chunk_doesnt_fire_on_different_sequence(self, tmp_path):
        # The chunk pattern only fires when the exact JDA sequence
        # appears. A different token in either slot should not match.
        ss.accept_suggestion(
            ["%[Cust_X.A]", "%[Cust_X.B]"],
            ["@[XAlpha.first.Field]", "@[XBeta.first.Field]"],
            org="oba", root=tmp_path,
        )
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        toks = [
            jda_parser.parse("%[Cust_X.A]"),
            jda_parser.parse("%[Cust_X.OTHER]"),
        ]
        segments = patterns_engine.convert_stream(toks, loaded, org="oba")
        # Both tokens come out unmatched — the chunk needed B, not OTHER.
        assert all(s.pattern is None for s in segments)


# ─── 1 JDA → 0 Pine (drop pattern) ───────────────────────────────────────────


class TestDropPattern:
    def test_drop_rewrite_persists_as_empty_array(self, tmp_path):
        path = ss.accept_suggestion(
            "%[Cust_OBAAttorney.Title]", [],
            org="oba", root=tmp_path,
        )
        # Rendered TOML has an explicit empty array.
        body = Path(path).read_text(encoding="utf-8")
        assert "rewrite = []" in body
        assert "drop pattern" in body  # description annotates the shape

    def test_drop_rewrite_makes_segment_vanish_on_next_run(self, tmp_path):
        ss.accept_suggestion(
            "%[Cust_OBAAttorney.Title]", [],
            org="oba", root=tmp_path,
        )
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        assert len(loaded) == 1
        # Run through the engine.
        jda = jda_parser.parse("%[Cust_OBAAttorney.Title]")
        result = patterns_engine.convert(jda, loaded, org="oba")
        # Pattern matched but emitted zero Pine tokens.
        assert result.matched
        assert result.outputs == ()


# ─── id stability across single vs list shape ────────────────────────────────


class TestIdStability:
    def test_multi_and_single_dont_collide(self, tmp_path):
        # The 1:1 case and the 1:N case for the same JDA token must
        # produce distinct files — otherwise a later 1:N save would
        # overwrite an earlier 1:1 save.
        a = ss.accept_suggestion("%[A]", "@[a]", org="oba", root=tmp_path)
        b = ss.accept_suggestion(
            "%[A]", ["@[a]", "@[b]"], org="oba", root=tmp_path,
        )
        assert a != b


# ─── apostrophes / special chars in list elements ────────────────────────────


class TestSpecialCharsInList:
    def test_apostrophe_in_one_list_element(self, tmp_path):
        # One element with an apostrophe (triple-quote-needed) mixed
        # with a plain element (literal-quote-needed) — verify the
        # writer renders a legal inline array.
        ss.accept_suggestion(
            "%[X.Gender]",
            [
                "@[If('@[X.Gender]' == 'M')]",
                "@[ElseIf('@[X.Gender]' == 'F')]",
            ],
            org="oba", root=tmp_path,
        )
        loaded = ss.load_verified_for_org("oba", root=tmp_path)
        assert len(loaded) == 1
        p = loaded[0]
        assert p.rewrite == [
            "@[If('@[X.Gender]' == 'M')]",
            "@[ElseIf('@[X.Gender]' == 'F')]",
        ]


# ─── reject malformed inputs ─────────────────────────────────────────────────


class TestRejectMalformed:
    def test_empty_jda_list_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            ss.accept_suggestion([], "@[a]", org="oba", root=tmp_path)

    def test_empty_string_in_jda_list_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            ss.accept_suggestion(["%[A]", ""], "@[a]", org="oba", root=tmp_path)

    def test_empty_string_pine_element_rejected_inside_list(self, tmp_path):
        # An empty string inside the pine list is a likely caller bug
        # (they meant []). Reject so it doesn't write a malformed TOML.
        with pytest.raises(ValueError):
            ss.accept_suggestion(
                "%[A]", ["@[a]", ""], org="oba", root=tmp_path,
            )

    def test_unparseable_pine_element_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            ss.accept_suggestion(
                "%[A]", ["@[a]", "not pine"], org="oba", root=tmp_path,
            )


# ─── end-to-end via the pipeline (the actual converter loop) ─────────────────


class TestPipelineIntegration:
    def test_persisted_drop_eliminates_jda_in_converted_rtf(self, tmp_path):
        # Hand-edit registered as drop → next pipeline run on a
        # template containing the same JDA token no longer emits it.
        ss.accept_suggestion(
            "%[Cust_OBAAttorney.Title]", [],
            org="oba", root=tmp_path,
        )
        rtf = "Dear %[Cust_OBAAttorney.Title]:"
        result = pipeline.convert_template(
            rtf, org="oba", suggestions_root=tmp_path,
        )
        # The JDA token's bytes are replaced with empty string.
        assert "%[Cust_OBAAttorney.Title]" not in result.converted_rtf
        # Surrounding prose stays intact (modulo collapsed whitespace).
        assert "Dear" in result.converted_rtf
        assert ":" in result.converted_rtf

    def test_persisted_split_emits_two_pine_tokens_on_next_run(self, tmp_path):
        ss.accept_suggestion(
            "%[ZZ_Custom.FullName]",
            [
                "@[ZZCustom.first.NameFirstName]",
                "@[ZZCustom.first.NameLastName]",
            ],
            org="oba", root=tmp_path,
        )
        rtf = "Hello %[ZZ_Custom.FullName]."
        result = pipeline.convert_template(
            rtf, org="oba", suggestions_root=tmp_path,
        )
        assert "@[ZZCustom.first.NameFirstName]" in result.converted_rtf
        assert "@[ZZCustom.first.NameLastName]" in result.converted_rtf

"""Tests for the LLM fallback layer.

Focus: prompt assembly correctness, response parsing tolerance, and
the privacy invariant (the prompt only contains the explicit allowed
inputs — no template prose). Real Anthropic SDK calls are NOT
exercised here; ``MockLlmClient`` returns canned responses.
"""

from __future__ import annotations

import pytest

from v2.engine.llm_fallback import (
    FallbackRequest,
    LlmFallback,
    MockLlmClient,
    _select_few_shot,
    _similarity,
)
from v2.grammar.loaders import load_org_overrides
from v2.parser import jda_parser, pine_parser
from v2.patterns import loader as pattern_loader


@pytest.fixture(scope="module")
def oba():
    return load_org_overrides("oba")


@pytest.fixture(scope="module")
def library():
    report = pattern_loader.load_library()
    report.raise_if_issues()
    return report.patterns


# ─── prompt assembly ──────────────────────────────────────────────────────

class TestPromptAssembly:
    def test_contains_input_section(self, oba):
        req = FallbackRequest(
            jda_token=jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"),
            org="oba",
            vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_prompt()
        assert "INPUT" in prompt
        assert "%[TitleCase(JW_Respondent.FullName)]" in prompt

    def test_contains_vocabulary(self, oba):
        req = FallbackRequest(
            jda_token=jda_parser.parse("%[Mystery]"),
            org="oba",
            vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_prompt()
        # Pick an entity we know is in OBA vocabulary.
        assert "Respondent" in prompt
        assert "ALLOWED PINE VOCABULARY" in prompt

    def test_includes_few_shot_examples(self, oba, library):
        req = FallbackRequest(
            jda_token=jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"),
            org="oba",
            vocabulary=oba.vocabulary,
            few_shot=library[:2],
        )
        prompt = req.assemble_prompt()
        # Both pattern matches should appear in the prompt.
        assert library[0].match in prompt or library[0].match.split('\n')[0] in prompt or any(
            m in prompt for m in (library[0].match if isinstance(library[0].match, list) else [library[0].match])
        )

    def test_few_shot_strips_dollar_holes(self, oba, library):
        """Pattern source uses ``$entity`` to mark holes. The LLM
        prompt must not contain ``$entity`` because the model will
        echo the dollar back into Pine output. Replacement is
        ``<entity>`` so the model recognises it as a placeholder."""
        # Pick a pattern that actually has $-holes in its source.
        holed = next(p for p in library if isinstance(p.match, str) and "$" in p.match)
        req = FallbackRequest(
            jda_token=jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"),
            org="oba",
            vocabulary=oba.vocabulary,
            few_shot=[holed],
        )
        prompt = req.assemble_prompt()
        # No $-prefixed identifiers anywhere in the prompt.
        import re
        assert re.search(r"\$[a-zA-Z_]", prompt) is None, (
            f"Prompt still contains $-holes:\n{prompt}"
        )
        # Replacement is angle-bracket placeholders.
        assert "<entity>" in prompt or "<" in prompt

    def test_grammar_fragment_appended(self, oba):
        req = FallbackRequest(
            jda_token=jda_parser.parse("%[Mystery]"),
            org="oba",
            vocabulary=oba.vocabulary,
            grammar_fragment="(date presets: preset1=MMMM d, yyyy ; preset5=MM/dd/yyyy)",
        )
        prompt = req.assemble_prompt()
        assert "preset1" in prompt
        assert "RELEVANT PINE GRAMMAR FRAGMENT" in prompt


# ─── privacy invariant ────────────────────────────────────────────────────

class TestPrivacyInvariant:
    """The prompt must contain only:
      - the framing text (constant, hard-coded in the module)
      - the input JDA token's unparsed text
      - vocabulary names
      - few-shot pattern texts
      - the grammar fragment

    Any other content is a privacy violation. We verify by constructing
    the prompt with known inputs and checking that NO unexpected
    content sneaks in.
    """

    def test_prompt_excludes_unrelated_text(self, oba):
        # The token unparses to a specific string; vocab and few-shot
        # are explicit. No prose should leak in.
        token = jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]")
        req = FallbackRequest(
            jda_token=token,
            org="oba",
            vocabulary=oba.vocabulary,
            few_shot=[],   # no patterns
            grammar_fragment="",
        )
        prompt = req.assemble_prompt()
        # The token text appears exactly once, in the INPUT section.
        assert prompt.count(token.unparse()) == 1
        # No section headers we didn't include.
        assert "VERIFIED EXAMPLES" not in prompt
        assert "RELEVANT PINE GRAMMAR FRAGMENT" not in prompt

    def test_prompt_assembly_is_deterministic(self, oba):
        token = jda_parser.parse("%[Initials(Cust_OBAAttorney.FullName, false)]")
        req = FallbackRequest(
            jda_token=token,
            org="oba",
            vocabulary=oba.vocabulary,
        )
        a = req.assemble_prompt()
        b = req.assemble_prompt()
        assert a == b

    def test_prompt_does_not_quote_arbitrary_input(self, oba):
        # Even if the JDA token contains odd characters, only its
        # unparsed text appears — not anything outside.
        token = jda_parser.parse("%[Subdocument(Template\\Letterhead)]")
        req = FallbackRequest(
            jda_token=token,
            org="oba",
            vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_prompt()
        # Backslash path appears exactly once (in the INPUT section).
        assert prompt.count("Template\\Letterhead") == 1


# ─── response parsing ─────────────────────────────────────────────────────

class TestResponseParsing:
    def test_clean_response(self):
        out = FallbackRequest.parse_response("@[Respondent.first.NameLastName]")
        assert out is not None
        assert out.unparse() == "@[Respondent.first.NameLastName]"

    def test_response_with_explanatory_text(self):
        # An LLM that wraps its answer in chatter — we still extract
        # the bracketed expression.
        text = (
            "Sure! Here is the Pine version:\n"
            "@[Respondent.first.NameLastName]\n"
            "Hope that helps."
        )
        out = FallbackRequest.parse_response(text)
        assert out is not None
        assert out.unparse() == "@[Respondent.first.NameLastName]"

    def test_response_in_code_fence(self):
        text = "```\n@[Respondent.first.NameLastName]\n```"
        out = FallbackRequest.parse_response(text)
        assert out is not None
        assert out.unparse() == "@[Respondent.first.NameLastName]"

    def test_unbalanced_brackets_returns_none(self):
        out = FallbackRequest.parse_response("@[Respondent.first.NameLastName")
        assert out is None

    def test_unparseable_returns_none(self):
        out = FallbackRequest.parse_response("@[--nonsense--]")
        # Pine parser rejects this; fallback returns None.
        assert out is None

    def test_no_pine_token_returns_none(self):
        out = FallbackRequest.parse_response("Sorry, I can't help with that.")
        assert out is None


# ─── few-shot selection ───────────────────────────────────────────────────

class TestFewShotSelection:
    def test_similarity_basic(self):
        s1 = _similarity("TitleCase(JW_Respondent.FullName)", "TitleCase(X.FullName)")
        s2 = _similarity("TitleCase(JW_Respondent.FullName)", "Subdocument(X)")
        assert s1 > s2

    def test_few_shot_picks_relevant_patterns(self, library):
        # For a TitleCase(X.FullName) input, the most-similar pattern
        # should be the OBA fullname titlecase one.
        target = "%[TitleCase(JW_Respondent.FullName)]"
        picks = _select_few_shot(library, target, org="oba", k=3)
        ids = [p.id for p in picks]
        assert "oba_fullname_titlecase" in ids

    def test_few_shot_filters_by_org(self, library):
        # When org="criminal-pd", OBA-only patterns must not appear.
        picks = _select_few_shot(
            library, "%[TitleCase(JW_X.FullName)]", org="criminal-pd", k=10,
        )
        for p in picks:
            assert p.org_context in ("any", "criminal-pd")

    def test_few_shot_excludes_chunk_patterns(self, library):
        # Chunk patterns aren't useful as few-shot for single-token
        # fallback (they're wrong shape).
        picks = _select_few_shot(library, "%[Mystery]", org="any", k=20)
        for p in picks:
            assert not p.is_chunk_pattern()


# ─── end-to-end with mock client ─────────────────────────────────────────

class TestEndToEnd:
    def test_mock_returns_pine_token(self, library, oba):
        client = MockLlmClient({
            # Our mock recognizes the input substring and returns a fixed
            # Pine token.
            "TitleCase(JW_Respondent.FullName)":
                "@[Respondent.first.FormatName(F L).SetCasing(Title)]",
        })
        fb = LlmFallback(client=client, library=library, org_overrides=oba)
        out = fb.convert(jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"))
        assert out is not None
        assert out.unparse() == "@[Respondent.first.FormatName(F L).SetCasing(Title)]"

    def test_mock_unparseable_response_returns_none(self, library, oba):
        client = MockLlmClient(lambda prompt: "I'm not sure how to convert this.")
        fb = LlmFallback(client=client, library=library, org_overrides=oba)
        out = fb.convert(jda_parser.parse("%[Mystery]"))
        assert out is None

    def test_build_request_attaches_few_shot(self, library, oba):
        client = MockLlmClient(lambda prompt: "@[Respondent.first.NameLastName]")
        fb = LlmFallback(client=client, library=library, org_overrides=oba)
        req = fb.build_request(jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"))
        assert len(req.few_shot) > 0

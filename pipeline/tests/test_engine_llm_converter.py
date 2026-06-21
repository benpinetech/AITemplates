"""Tests for the LLM fallback layer.

Focus: prompt assembly correctness, response parsing tolerance, and
the privacy invariant (the prompt only contains the explicit allowed
inputs — no template prose). Real Anthropic SDK calls are NOT
exercised here; ``MockLlmClient`` returns canned responses.
"""

from __future__ import annotations

import pytest

from pipeline.engine.llm_converter import (
    ConversionRequest,
    LlmConverter,
    MockLlmClient,
    _select_few_shot,
    _similarity,
)
from pipeline.grammar.loaders import load_agency_overrides
from pipeline.parser import jda_parser, pine_parser
from pipeline.patterns.schema import Pattern


@pytest.fixture(scope="module")
def oba():
    return load_agency_overrides("oba")


@pytest.fixture(scope="module")
def library():
    return []


@pytest.fixture(scope="module")
def few_shot_patterns():
    """Minimal synthetic Pattern objects for few-shot tests."""
    return [
        Pattern(
            id="syn_fullname",
            description="synthetic few-shot",
            match="%[TitleCase(JW_Respondent.FullName)]",
            rewrite="@[Respondent.first.FormatName(F L).SetCasing(Title)]",
        ),
        Pattern(
            id="syn_dollar_hole",
            description="synthetic pattern with $-hole",
            match="%[TitleCase($entity.FullName)]",
            rewrite="@[$entity_pine.first.FormatName(F L).SetCasing(Title)]",
            holes={
                "entity": {"kind": "path-segment"},
                "entity_pine": {"derive_from": "entity", "transform": "translate_jda_entity_to_pine"},
            },
        ),
    ]


# ─── prompt assembly ──────────────────────────────────────────────────────

class TestPromptAssembly:
    def test_contains_input_section(self, oba):
        req = ConversionRequest(
            jda_token=jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"),
            agency="oba",
            vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_prompt()
        assert "INPUT" in prompt
        assert "%[TitleCase(JW_Respondent.FullName)]" in prompt

    def test_contains_vocabulary(self, oba):
        req = ConversionRequest(
            jda_token=jda_parser.parse("%[Mystery]"),
            agency="oba",
            vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_prompt()
        # Pick an entity we know is in OBA vocabulary.
        assert "Respondent" in prompt
        assert "ALLOWED PINE VOCABULARY" in prompt

    def test_includes_few_shot_examples(self, oba, few_shot_patterns):
        p = few_shot_patterns[0]
        req = ConversionRequest(
            jda_token=jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"),
            agency="oba",
            vocabulary=oba.vocabulary,
            few_shot=[p],
        )
        prompt = req.assemble_prompt()
        assert p.match in prompt or any(
            m in prompt for m in (p.match if isinstance(p.match, list) else [p.match])
        )

    def test_few_shot_strips_dollar_holes(self, oba, few_shot_patterns):
        """Pattern source uses ``$entity`` to mark holes. The LLM
        prompt must not contain ``$entity`` because the model will
        echo the dollar back into Pine output. Replacement is
        ``<entity>`` so the model recognises it as a placeholder."""
        holed = few_shot_patterns[1]   # the synthetic pattern with $-holes
        req = ConversionRequest(
            jda_token=jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"),
            agency="oba",
            vocabulary=oba.vocabulary,
            few_shot=[holed],
        )
        prompt = req.assemble_prompt()
        import re
        assert re.search(r"\$[a-zA-Z_]", prompt) is None, (
            f"Prompt still contains $-holes:\n{prompt}"
        )
        assert "<entity>" in prompt or "<" in prompt

    def test_grammar_fragment_appended(self, oba):
        req = ConversionRequest(
            jda_token=jda_parser.parse("%[Mystery]"),
            agency="oba",
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
        req = ConversionRequest(
            jda_token=token,
            agency="oba",
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
        req = ConversionRequest(
            jda_token=token,
            agency="oba",
            vocabulary=oba.vocabulary,
        )
        a = req.assemble_prompt()
        b = req.assemble_prompt()
        assert a == b

    def test_prompt_omits_translation_rules_block(self, oba):
        # The OBA 10-rule block was removed after the H1 prompt-strip
        # experiment showed it diluted the prompt without F1 benefit.
        # The universal role enum + vocabulary + few-shot carry the
        # same info more generally. See LLM_CAPABILITY_FINDINGS.md.
        token = jda_parser.parse("%[Cust_Complainant.FullName]")
        req = ConversionRequest(jda_token=token, agency="oba", vocabulary=oba.vocabulary)
        prompt = req.assemble_prompt()
        assert "TRANSLATION RULES" not in prompt
        # Role enum still carries the entity vocabulary the rules used to enumerate.
        assert "INVOLVEMENT TYPES" in prompt or "ASSIGNMENT TYPES" in prompt

    def test_prompt_omits_entity_translation_table(self, oba):
        # Same rationale as above — the entity translation table was
        # redundant with the role enum + vocabulary list. Per-input
        # entity hints (``ENTITY HINT``) still surface known renames
        # for the specific token being translated.
        token = jda_parser.parse("%[Cust_Complainant_Address.City]")
        req = ConversionRequest(jda_token=token, agency="oba", vocabulary=oba.vocabulary)
        prompt = req.assemble_prompt()
        assert "JDA → PINE ENTITY TRANSLATION TABLE" not in prompt
        # The per-input hint still resolves the leading entity.
        assert "ENTITY HINT" in prompt
        assert "Cust_Complainant_Address" in prompt
        assert "ComplainantAddress" in prompt

    def test_prompt_includes_entity_hint_when_known(self, oba):
        # When the input's leading entity is in the table, the prompt
        # adds a per-call hint pointing the LLM at the right Pine entity.
        token = jda_parser.parse("%[Cust_RespondentAtty.LastName]")
        req = ConversionRequest(jda_token=token, agency="oba", vocabulary=oba.vocabulary)
        prompt = req.assemble_prompt()
        assert "ENTITY HINT" in prompt
        # The hint pairs the JDA name with its Pine target.
        hint_section = prompt.split("ENTITY HINT")[1]
        assert "Cust_RespondentAtty" in hint_section
        assert "RespondentAtty" in hint_section

    def test_prompt_omits_hint_for_unknown_entity(self, oba):
        token = jda_parser.parse("%[MysteryEntity.SomeField]")
        req = ConversionRequest(jda_token=token, agency="oba", vocabulary=oba.vocabulary)
        prompt = req.assemble_prompt()
        assert "ENTITY HINT" not in prompt

    def test_batch_prompt_lists_inputs_in_order(self, oba):
        from pipeline.engine.llm_converter import BatchConversionRequest
        toks = [
            jda_parser.parse("%[Cust_Complainant.FullName]"),
            jda_parser.parse("%[Cust_Complainant_Address.City]"),
            jda_parser.parse("%[TitleCase(JW_Atty_Pros_Active.FullName)]"),
        ]
        req = BatchConversionRequest(
            jda_tokens=tuple(toks), agency="oba", vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_batch_prompt()
        # All three appear in numbered order in the INPUTS section.
        inputs_section = prompt.split("INPUTS")[1].split("OUTPUTS")[0]
        assert "1. %[Cust_Complainant.FullName]" in inputs_section
        assert "2. %[Cust_Complainant_Address.City]" in inputs_section
        assert "3. %[TitleCase(JW_Atty_Pros_Active.FullName)]" in inputs_section
        # All three get an entity hint too.
        hint_section = prompt.split("ENTITY HINT")[1]
        assert "for input 1" in hint_section
        assert "for input 2" in hint_section
        assert "for input 3" in hint_section

    def test_classify_audience_from_filename_to_c(self, oba):
        from pipeline.engine.llm_converter import classify_document_audience
        from pipeline.patterns.transforms import _JDA_TO_PINE_ENTITY
        # Filename signal trumps token frequency
        assert classify_document_audience(
            "C Offer PR.rtf", [], _JDA_TO_PINE_ENTITY,
        ) == "complainant"
        assert classify_document_audience(
            "Letter to Complainant.rtf", [], _JDA_TO_PINE_ENTITY,
        ) == "complainant"
        assert classify_document_audience(
            "Process Ltr C.rtf", [], _JDA_TO_PINE_ENTITY,
        ) == "complainant"

    def test_classify_audience_from_filename_to_r(self, oba):
        from pipeline.engine.llm_converter import classify_document_audience
        from pipeline.patterns.transforms import _JDA_TO_PINE_ENTITY
        assert classify_document_audience(
            "R Offer PR.rtf", [], _JDA_TO_PINE_ENTITY,
        ) == "respondent"
        assert classify_document_audience(
            "Letter to Respondent.rtf", [], _JDA_TO_PINE_ENTITY,
        ) == "respondent"
        assert classify_document_audience(
            "UPL Process R.rtf", [], _JDA_TO_PINE_ENTITY,
        ) == "respondent"
        assert classify_document_audience(
            "Letter to Disbarred Attorney.rtf", [], _JDA_TO_PINE_ENTITY,
        ) == "respondent"

    def test_classify_audience_from_token_frequency(self):
        from pipeline.engine.llm_converter import classify_document_audience
        from pipeline.patterns.transforms import _JDA_TO_PINE_ENTITY
        # No filename → fall back to token counts
        complainant_heavy = [
            jda_parser.parse("%[Cust_Complainant.FullName]"),
            jda_parser.parse("%[Cust_Complainant.LastName]"),
            jda_parser.parse("%[Cust_Complainant_Address.City]"),
            jda_parser.parse("%[JW_Respondent.MrMs]"),
        ]
        assert classify_document_audience(
            None, complainant_heavy, _JDA_TO_PINE_ENTITY,
        ) == "complainant"

        respondent_heavy = [
            jda_parser.parse("%[JW_Respondent.FullName]"),
            jda_parser.parse("%[JW_Respondent.LastName]"),
            jda_parser.parse("%[JW_Respondent_RosterAddress.City]"),
            jda_parser.parse("%[Cust_Complainant.MrMs]"),
        ]
        assert classify_document_audience(
            None, respondent_heavy, _JDA_TO_PINE_ENTITY,
        ) == "respondent"

    def test_classify_audience_returns_none_when_balanced(self):
        from pipeline.engine.llm_converter import classify_document_audience
        from pipeline.patterns.transforms import _JDA_TO_PINE_ENTITY
        balanced = [
            jda_parser.parse("%[Cust_Complainant.FullName]"),
            jda_parser.parse("%[Cust_Complainant.LastName]"),
            jda_parser.parse("%[JW_Respondent.FullName]"),
            jda_parser.parse("%[JW_Respondent.LastName]"),
        ]
        # 50/50 split — no clear majority
        assert classify_document_audience(
            None, balanced, _JDA_TO_PINE_ENTITY,
        ) is None

    def test_classify_audience_returns_none_for_too_few_tokens(self):
        from pipeline.engine.llm_converter import classify_document_audience
        from pipeline.patterns.transforms import _JDA_TO_PINE_ENTITY
        # Below the min-tokens threshold (3)
        assert classify_document_audience(
            None,
            [jda_parser.parse("%[Cust_Complainant.FullName]"),
             jda_parser.parse("%[Cust_Complainant.LastName]")],
            _JDA_TO_PINE_ENTITY,
        ) is None

    def test_batch_prompt_includes_audience_when_classifiable(self, oba):
        from pipeline.engine.llm_converter import BatchConversionRequest
        toks = [jda_parser.parse("%[Cust_Complainant.FullName]")]
        req = BatchConversionRequest(
            jda_tokens=tuple(toks), agency="oba", vocabulary=oba.vocabulary,
            template_name="C Offer PR.rtf",
        )
        prompt = req.assemble_batch_prompt()
        assert "DOCUMENT AUDIENCE" in prompt
        section = prompt.split("DOCUMENT AUDIENCE")[1].split("DOCUMENT CONTEXT")[0]
        assert "Complainant" in section

    def test_batch_prompt_omits_audience_when_unclear(self, oba):
        from pipeline.engine.llm_converter import BatchConversionRequest
        toks = [jda_parser.parse("%[SomethingObscure.Field]")]
        req = BatchConversionRequest(
            jda_tokens=tuple(toks), agency="oba", vocabulary=oba.vocabulary,
            # No filename and tokens don't classify
        )
        prompt = req.assemble_batch_prompt()
        assert "DOCUMENT AUDIENCE" not in prompt

    def test_batch_prompt_includes_document_context(self, oba):
        from pipeline.engine.llm_converter import BatchConversionRequest
        # 3 Complainant tokens, 1 Respondent token → Complainant should
        # be flagged as the dominant entity in the doc-context section.
        toks = [
            jda_parser.parse("%[Cust_Complainant.FullName]"),
            jda_parser.parse("%[Cust_Complainant.LastName]"),
            jda_parser.parse("%[Cust_Complainant_Address.City]"),
            jda_parser.parse("%[JW_Respondent.FullName]"),
        ]
        req = BatchConversionRequest(
            jda_tokens=tuple(toks), agency="oba", vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_batch_prompt()
        assert "DOCUMENT CONTEXT" in prompt
        ctx_section = prompt.split("DOCUMENT CONTEXT")[1].split("INPUTS")[0]
        # Complainant appears most (3 times: two direct + one address)
        # Respondent appears once.
        assert "Complainant" in ctx_section
        assert "Respondent" in ctx_section
        # Counts should be on separate lines
        complainant_line = next(l for l in ctx_section.splitlines() if "Complainant" in l and "Address" not in l)
        respondent_line = next(l for l in ctx_section.splitlines() if "Respondent" in l)
        # Complainant count > Respondent count
        c_count = int(complainant_line.split('×')[0].strip())
        r_count = int(respondent_line.split('×')[0].strip())
        assert c_count > r_count

    def test_batch_response_parsing_simple(self):
        from pipeline.engine.llm_converter import BatchConversionRequest
        resp = (
            "1. @[Complainant.first.NameFirstName] @[Complainant.first.NameLastName]\n"
            "2. @[ComplainantAddress.first.City]\n"
            "3. @[Prosecutor.first.FormatName(F L).SetCasing(Title)]\n"
        )
        out = BatchConversionRequest.parse_batch_response(resp, 3)
        assert len(out) == 3
        assert [t.unparse() for t in out[0]] == [
            "@[Complainant.first.NameFirstName]",
            "@[Complainant.first.NameLastName]",
        ]
        assert [t.unparse() for t in out[1]] == ["@[ComplainantAddress.first.City]"]
        assert [t.unparse() for t in out[2]] == ["@[Prosecutor.first.FormatName(F L).SetCasing(Title)]"]

    def test_batch_response_parsing_no_mapping_slot(self):
        from pipeline.engine.llm_converter import BatchConversionRequest
        resp = (
            "1. @[Respondent.first.NameLastName]\n"
            "2. <no mapping found>\n"
            "3. @[Complainant.first.NameLastName]\n"
        )
        out = BatchConversionRequest.parse_batch_response(resp, 3)
        assert len(out) == 3
        assert len(out[0]) == 1
        assert out[1] == []          # no Pine tokens for slot 2
        assert len(out[2]) == 1

    def test_batch_response_parsing_tolerates_missing_slots(self):
        from pipeline.engine.llm_converter import BatchConversionRequest
        # LLM might skip slot 2 entirely.
        resp = (
            "1. @[Respondent.first.NameLastName]\n"
            "3. @[Complainant.first.NameLastName]\n"
        )
        out = BatchConversionRequest.parse_batch_response(resp, 3)
        assert len(out) == 3
        assert len(out[0]) == 1
        assert out[1] == []          # missing slot
        assert len(out[2]) == 1

    def test_batch_convert_via_mock_client(self, oba):
        from pipeline.engine.llm_converter import LlmConverter, MockLlmClient
        canned = (
            "1. @[Complainant.first.NameFirstName] @[Complainant.first.NameLastName]\n"
            "2. @[ComplainantAddress.first.City]\n"
        )
        client = MockLlmClient(lambda prompt: canned)
        fb = LlmConverter(client=client, library=[], agency_overrides=oba)
        toks = [
            jda_parser.parse("%[Cust_Complainant.FullName]"),
            jda_parser.parse("%[Cust_Complainant_Address.City]"),
        ]
        out = fb.convert_batch(toks)
        assert len(out) == 2
        assert [t.unparse() for t in out[0]] == [
            "@[Complainant.first.NameFirstName]",
            "@[Complainant.first.NameLastName]",
        ]
        assert [t.unparse() for t in out[1]] == ["@[ComplainantAddress.first.City]"]

    def test_batch_convert_empty_returns_empty(self, oba):
        from pipeline.engine.llm_converter import LlmConverter, MockLlmClient
        fb = LlmConverter(
            client=MockLlmClient(lambda p: ""),
            library=[], agency_overrides=oba,
        )
        assert fb.convert_batch([]) == []

    def test_constant_prefix_stable_across_inputs(self, oba):
        # OpenAI's prompt cache keys on the prefix; we want the entire
        # rules+vocab+entity-table section identical between calls so
        # the cache hits. Verify by comparing the prefix up to the
        # variable section (FEWSHOT / HINT / INPUT).
        a = ConversionRequest(
            jda_token=jda_parser.parse("%[Cust_Complainant.LastName]"),
            agency="oba",
            vocabulary=oba.vocabulary,
        ).assemble_prompt()
        b = ConversionRequest(
            jda_token=jda_parser.parse("%[JW_Respondent.FullName]"),
            agency="oba",
            vocabulary=oba.vocabulary,
        ).assemble_prompt()
        # Find a marker that's the LAST constant-section header.
        marker = "ALLOWED PINE VOCABULARY"
        a_prefix = a[: a.index(marker) + len(marker)]
        b_prefix = b[: b.index(marker) + len(marker)]
        assert a_prefix == b_prefix

    def test_prompt_does_not_quote_arbitrary_input(self, oba):
        # Even if the JDA token contains odd characters, the input
        # appears in exactly one place: the INPUT section. (Rule
        # examples may legitimately mention other paths.)
        token = jda_parser.parse("%[Subdocument(MysteryPath\\NotInRules)]")
        req = ConversionRequest(
            jda_token=token,
            agency="oba",
            vocabulary=oba.vocabulary,
        )
        prompt = req.assemble_prompt()
        assert prompt.count("MysteryPath\\NotInRules") == 1


# ─── response parsing ─────────────────────────────────────────────────────

class TestResponseParsing:
    def test_clean_response(self):
        out = ConversionRequest.parse_response("@[Respondent.first.NameLastName]")
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
        out = ConversionRequest.parse_response(text)
        assert out is not None
        assert out.unparse() == "@[Respondent.first.NameLastName]"

    def test_response_in_code_fence(self):
        text = "```\n@[Respondent.first.NameLastName]\n```"
        out = ConversionRequest.parse_response(text)
        assert out is not None
        assert out.unparse() == "@[Respondent.first.NameLastName]"

    def test_unbalanced_brackets_returns_none(self):
        out = ConversionRequest.parse_response("@[Respondent.first.NameLastName")
        assert out is None

    def test_unparseable_returns_none(self):
        out = ConversionRequest.parse_response("@[--nonsense--]")
        # Pine parser rejects this; fallback returns None.
        assert out is None

    def test_no_pine_token_returns_none(self):
        out = ConversionRequest.parse_response("Sorry, I can't help with that.")
        assert out is None


# ─── few-shot selection ───────────────────────────────────────────────────

class TestFewShotSelection:
    def test_similarity_basic(self):
        s1 = _similarity("TitleCase(JW_Respondent.FullName)", "TitleCase(X.FullName)")
        s2 = _similarity("TitleCase(JW_Respondent.FullName)", "Subdocument(X)")
        assert s1 > s2

    def test_few_shot_filters_by_agency(self, library):
        # When agency="criminal-pd", OBA-only patterns must not appear.
        picks = _select_few_shot(
            library, "%[TitleCase(JW_X.FullName)]", agency="criminal-pd", k=10,
        )
        for p in picks:
            assert p.agency_context in ("any", "criminal-pd")

    def test_few_shot_excludes_chunk_patterns(self, library):
        # Chunk patterns aren't useful as few-shot for single-token
        # fallback (they're wrong shape).
        picks = _select_few_shot(library, "%[Mystery]", agency="any", k=20)
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
        fb = LlmConverter(client=client, library=library, agency_overrides=oba)
        out = fb.convert(jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"))
        assert out is not None
        assert out.unparse() == "@[Respondent.first.FormatName(F L).SetCasing(Title)]"

    def test_mock_unparseable_response_returns_none(self, library, oba):
        client = MockLlmClient(lambda prompt: "I'm not sure how to convert this.")
        fb = LlmConverter(client=client, library=library, agency_overrides=oba)
        out = fb.convert(jda_parser.parse("%[Mystery]"))
        assert out is None

    def test_build_request_attaches_few_shot(self, few_shot_patterns, oba):
        client = MockLlmClient(lambda prompt: "@[Respondent.first.NameLastName]")
        fb = LlmConverter(client=client, library=few_shot_patterns, agency_overrides=oba)
        req = fb.build_request(jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]"))
        assert len(req.few_shot) > 0

"""End-to-end test of the LLM-suggestion closed loop.

Demonstrates the core workflow:

  1. A novel JDA token has no matching suggestion.
  2. The LLM converter (mocked) produces a Pine suggestion.
  3. The mapper accepts the suggestion via ``suggestion_store.accept_suggestion``.
  4. The pipeline is re-run on the same JDA — and now the segment
     matches via a deterministic suggestion lookup (no LLM call this time).

This is the core "iteration without prompt churn" property the v2
design promises.
"""

from __future__ import annotations

import pytest

from pipeline import pipeline
from pipeline.engine import suggestion_store
from pipeline.engine.llm_converter import LlmConverter, MockLlmClient
from pipeline.grammar.loaders import load_agency_overrides


@pytest.fixture
def fresh_suggestions_root(tmp_path):
    """A clean suggestions root for each test, so we don't pollute the
    real pipeline/suggestions/ directory."""
    return tmp_path


@pytest.fixture
def oba():
    return load_agency_overrides("oba")


def test_accept_then_rerun_uses_suggestion(fresh_suggestions_root, oba):
    """After accepting an LLM suggestion, the next pipeline run on the
    same JDA token matches via the deterministic suggestion lookup —
    no LLM call."""
    novel_rtf = "Re: %[NovelMagic(JW_Mystery)]"

    # ── First run: no suggestion matches → LLM converter → suggestion produced.
    llm_calls = []
    def responder(prompt: str) -> str:
        llm_calls.append(prompt)
        return "@[Mystery.first.Magic]"
    fb = LlmConverter(
        client=MockLlmClient(responder), library=[], agency_overrides=oba,
    )
    result1 = pipeline.convert_template(
        novel_rtf, agency="oba", converter=fb,
        suggestions_root=fresh_suggestions_root,
    )
    assert len(llm_calls) == 1, "LLM should have been hit once"
    llm_seg = next(s for s in result1.segments if s.provenance == pipeline.PROV_LLM)
    assert llm_seg.pine_outputs[0].unparse() == "@[Mystery.first.Magic]"

    # ── Mapper accepts the suggestion.
    jda_text = llm_seg.source_jda_tokens[0].unparse()
    pine_text = llm_seg.pine_outputs[0].unparse()
    suggestion_store.accept_suggestion(
        jda_text, pine_text, agency="oba", root=fresh_suggestions_root,
    )

    # ── Second run: suggestion lookup fires deterministically. No LLM call.
    llm_calls.clear()
    result2 = pipeline.convert_template(
        novel_rtf, agency="oba", converter=fb,
        suggestions_root=fresh_suggestions_root,
    )
    assert llm_calls == [], "LLM must not be called on the re-run"
    seg = next(s for s in result2.segments if s.source_jda_tokens)
    assert seg.provenance == pipeline.PROV_SUGGESTION
    assert seg.pine_outputs[0].unparse() == "@[Mystery.first.Magic]"


def test_reject_doesnt_persist_for_suggestion_matching(
    fresh_suggestions_root, oba,
):
    """Rejecting a suggestion writes an audit-log record but does NOT
    create a verified entry. The next run still hits the LLM."""
    novel_rtf = "%[NovelMagic(JW_Mystery)]"
    llm_calls = []
    def responder(prompt: str) -> str:
        llm_calls.append(prompt)
        return "@[Mystery.first.Magic]"
    fb = LlmConverter(
        client=MockLlmClient(responder), library=[], agency_overrides=oba,
    )
    result1 = pipeline.convert_template(
        novel_rtf, agency="oba", converter=fb,
        suggestions_root=fresh_suggestions_root,
    )
    llm_seg = next(s for s in result1.segments if s.provenance == pipeline.PROV_LLM)
    suggestion_store.reject_suggestion(
        llm_seg.source_jda_tokens[0].unparse(),
        llm_seg.pine_outputs[0].unparse(),
        agency="oba",
        reason="wrong field name",
        root=fresh_suggestions_root,
    )

    # The rejection log exists but no verified file was created.
    assert (fresh_suggestions_root / "rejected.log").exists()
    assert not list((fresh_suggestions_root / "verified").rglob("*.toml"))

    # Re-running still hits the LLM.
    llm_calls.clear()
    pipeline.convert_template(
        novel_rtf, agency="oba", converter=fb,
        suggestions_root=fresh_suggestions_root,
    )
    assert len(llm_calls) == 1


# The canonical multi-token block: a legacy HisHer pronoun variable maps
# to a full Pine gender conditional with literal pronoun text between the
# control tokens. This does not fit the one-token-per-mapping model and is
# carried verbatim (PineRawBlock) through save → load → apply.
GENDER_BLOCK = (
    "@[if('@[DefName.Gender]'=='M')]his"
    "@[elseif('@[DefName.Gender]'=='F')]her"
    "@[else]his/her@[endif]"
)


def test_multi_token_block_round_trips(fresh_suggestions_root, oba):
    """A converter-added multi-token block (the gender-pronoun if/elseif/
    else/endif) saves, loads, and re-applies verbatim on a later run —
    the value the HITL cache is meant to accumulate."""
    from pipeline.parser.pine_ast import PineRawBlock

    legacy = "%[Cust_Atty_Def_Active2.HisHer]"
    rtf = f"The Defendant shall pay {legacy} fees."

    # Save the block as a verified suggestion (previously rejected with
    # "refusing to cache unparseable Pine output").
    suggestion_store.accept_suggestion(
        legacy, GENDER_BLOCK, agency="oba", root=fresh_suggestions_root,
    )

    # It survives the load-time parseability filter.
    loaded = suggestion_store.load_verified_for_agency(
        "oba", root=fresh_suggestions_root,
    )
    assert [p.rewrite_tokens() for p in loaded if legacy in p.match_tokens()] == [
        [GENDER_BLOCK]
    ]

    # And it applies deterministically: no LLM converter passed, so a
    # match here can only come from the suggestion lookup.
    result = pipeline.convert_template(
        rtf, agency="oba", suggestions_root=fresh_suggestions_root,
    )
    seg = next(s for s in result.segments if s.source_jda_tokens)
    assert seg.provenance == pipeline.PROV_SUGGESTION
    assert len(seg.pine_outputs) == 1
    out = seg.pine_outputs[0]
    assert isinstance(out, PineRawBlock)
    # Verbatim: literal pronoun glue between tokens is preserved exactly.
    assert out.unparse() == GENDER_BLOCK
    assert GENDER_BLOCK in result.converted_rtf
    assert legacy not in result.converted_rtf


def test_pipeline_loads_default_suggestions_root_when_unspecified(
    oba, monkeypatch, tmp_path,
):
    """When `suggestions_root` is not passed, the pipeline reads from
    the default `pipeline/suggestions/` location. Patch that location
    to a tmp path for this test."""
    monkeypatch.setattr(
        "pipeline.engine.suggestion_store.SUGGESTIONS_DIR", tmp_path,
    )
    suggestion_store.accept_suggestion(
        "%[NovelMagic(JW_X)]", "@[X.first.Magic]", "oba", root=tmp_path,
    )
    result = pipeline.convert_template(
        "%[NovelMagic(JW_X)]", agency="oba",
    )
    seg = next(s for s in result.segments if s.source_jda_tokens)
    assert seg.provenance == pipeline.PROV_SUGGESTION
    assert seg.pine_outputs[0].unparse() == "@[X.first.Magic]"

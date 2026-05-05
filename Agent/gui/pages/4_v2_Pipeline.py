"""Streamlit page for the v2 chunk-based pipeline.

Runs ``v2.pipeline.convert_template`` on an uploaded or pasted JDA
RTF and displays:

  - source vs converted RTF (rendered side-by-side)
  - per-segment provenance (which pattern matched, or LLM, or unmatched)
  - validation issues
  - a stats summary

LLM fallback is **off by default** to avoid API calls. Toggle it on
in the sidebar — needs ``ANTHROPIC_API_KEY`` in the environment.
"""

import html
import os
import sys
from pathlib import Path

import streamlit as st


# ── path setup ────────────────────────────────────────────────────────────────
GUI_DIR = Path(__file__).resolve().parent.parent
AGENT_DIR = GUI_DIR.parent
sys.path.insert(0, str(GUI_DIR))
sys.path.insert(0, str(AGENT_DIR.parent))   # so `from v2... import ...` works

from rtf_render import init_renderer, render_rtf, highlight_legacy, highlight_pine
from v2 import pipeline
from v2.engine import suggestion_store
from v2.engine.llm_fallback import AnthropicLlmClient, LlmFallback
from v2.grammar.loaders import load_org_overrides
from v2.patterns import loader as pattern_loader


st.set_page_config(page_title="v2 Pipeline", layout="wide")
st.title("v2 Pipeline")
st.caption(
    "Chunk-based pattern engine + optional LLM fallback. "
    "All output here comes from the **v2** pipeline — see the "
    "Eval Dashboard and Template Runner pages for the v1 outputs."
)

if "renderer_checked" not in st.session_state:
    init_renderer("http://localhost:5000")
    st.session_state.renderer_checked = True


# ── helpers ───────────────────────────────────────────────────────────────────
def render_panel(rtf_content: str, token_type: str, label: str):
    st.markdown(f"**{label}**")
    if not rtf_content:
        st.caption("No content available.")
        return
    rendered, method = render_rtf(rtf_content)
    st.caption(f"rendered via {method}")
    if method == "dotnet":
        st.components.v1.html(rendered, height=500, scrolling=True)
    else:
        highlighted = (
            highlight_legacy(rendered)
            if token_type == "legacy" else highlight_pine(rendered)
        )
        st.markdown(
            f'<div style="font-family:monospace;white-space:pre-wrap;font-size:11px;'
            f'max-height:500px;overflow-y:auto;border:1px solid #ddd;padding:8px">'
            f"{highlighted}</div>",
            unsafe_allow_html=True,
        )
    with st.expander("Raw RTF"):
        st.code(rtf_content[:4000], language=None)


def provenance_color(prov: str) -> str:
    return {
        pipeline.PROV_PATTERN: "#1f883d",     # green
        pipeline.PROV_LLM:     "#9a6700",      # amber
        pipeline.PROV_UNMATCHED: "#d1242f",   # red
    }.get(prov, "#666")


def _load_library():
    """Load the active pattern library fresh on every call. We don't
    cache this because accepting a suggestion writes a new TOML and
    the next run needs to see it."""
    return pattern_loader.load_library().patterns


# ── sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("v2 conversion")
    st.caption("Upload a JDA RTF or paste text below, pick an org, run.")

    uploaded = st.file_uploader("JDA RTF", type=["rtf"])
    pasted = st.text_area(
        "…or paste source text",
        height=120,
        placeholder=(
            "%[TitleCase(JW_Respondent.FullName)]\n"
            "%[Subdocument(Template\\Letterhead)]\n"
            "..."
        ),
    )

    org = st.selectbox(
        "Org context",
        options=["oba", "any"],
        index=0,
        help=(
            "Determines which patterns apply and which Pine vocabulary "
            "the validator uses. Required by Phase 5."
        ),
    )

    has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    use_llm = st.checkbox(
        f"Enable LLM fallback{'' if has_anthropic_key else ' (no API key set)'}",
        value=False,
        disabled=not has_anthropic_key,
        help=(
            "When checked, unmatched segments are sent to the Anthropic "
            "API. The prompt contains only AST + vocabulary + few-shot "
            "patterns + grammar — never prose."
        ),
    )

    run_btn = st.button("Run v2 conversion", type="primary", use_container_width=True)


# ── run ───────────────────────────────────────────────────────────────────────
if run_btn:
    if uploaded is not None:
        source = uploaded.read().decode("utf-8", errors="replace")
        st.session_state.v2_source_name = uploaded.name
    elif pasted.strip():
        source = pasted
        st.session_state.v2_source_name = "<pasted>"
    else:
        st.warning("Upload an RTF or paste source text first.")
        st.stop()

    library = _load_library()
    org_overrides = load_org_overrides(org) if org != "any" else None

    fb = None
    if use_llm and has_anthropic_key:
        try:
            fb = LlmFallback(
                client=AnthropicLlmClient(),
                library=library,
                org_overrides=org_overrides,
            )
        except RuntimeError as e:
            st.error(f"LLM client failed to start: {e}")
            st.stop()

    with st.spinner("Converting…"):
        result = pipeline.convert_template(
            source, org=org,
            library=library,
            org_overrides=org_overrides,
            llm_fallback=fb,
        )

    st.session_state.v2_source = source
    st.session_state.v2_result = result
    # New run → wipe any per-segment Accept/Reject decisions from the
    # previous run so the buttons are live again.
    st.session_state.v2_decisions = {}


# ── display ───────────────────────────────────────────────────────────────────
result: pipeline.ConversionResult | None = st.session_state.get("v2_result")
source: str | None = st.session_state.get("v2_source")

if not result or not source:
    st.info("Pick an RTF and click **Run v2 conversion** to see results.")
    st.stop()

# Summary row.
prov = result.by_provenance
errors = [i for i in result.issues if i.severity == "error"]
warnings = [i for i in result.issues if i.severity == "warning"]
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("JDA tokens", result.total_jda_tokens)
c2.metric("Pine tokens", result.total_pine_tokens)
c3.metric("Pattern segs", prov[pipeline.PROV_PATTERN])
c4.metric("LLM segs", prov[pipeline.PROV_LLM])
c5.metric("Unmatched", prov[pipeline.PROV_UNMATCHED])
c6.metric("Issues", f"{len(errors)} err / {len(warnings)} warn")

# Source vs converted, side-by-side.
st.markdown("### v2 RTF output")
left, right = st.columns(2, gap="large")
with left:
    render_panel(source, "legacy", "Legacy (input)  —  v2 source")
with right:
    render_panel(result.converted_rtf, "pine", "Converted (v2 pipeline)")

# Segments table — provenance per chunk.
st.markdown("### Segments and provenance")
if not result.segments:
    st.caption("No bracketed expressions were extracted from the source.")
else:
    rows_html = []
    for i, seg in enumerate(result.segments):
        color = provenance_color(seg.provenance)
        prov_label = seg.provenance
        if seg.pattern is not None:
            prov_label = f"pattern: {seg.pattern.id}"
        jda_text = " | ".join(t.unparse() for t in seg.source_jda_tokens)
        pine_text = " | ".join(t.unparse() for t in seg.pine_outputs) or "—"
        issue_count = len(seg.issues)
        issue_label = (
            f' <span style="color:#d1242f;font-size:11px">({issue_count} issue)</span>'
            if issue_count else ""
        )
        rows_html.append(
            f'<tr>'
            f'<td style="padding:4px 8px;font-size:11px;color:#666">#{i}</td>'
            f'<td style="padding:4px 8px;font-size:11px"><span style="color:{color};'
            f'font-weight:600">{html.escape(prov_label)}</span>{issue_label}</td>'
            f'<td style="padding:4px 8px;font-family:monospace;font-size:11px">'
            f'{html.escape(jda_text)}</td>'
            f'<td style="padding:4px 8px;font-family:monospace;font-size:11px">'
            f'{html.escape(pine_text)}</td>'
            f'</tr>'
        )
    table_html = (
        '<table style="width:100%;border-collapse:collapse">'
        '<thead><tr style="background:#0d1117;color:#e6edf3">'
        '<th style="padding:6px 8px;text-align:left;font-size:11px">#</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px">provenance</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px">JDA source</th>'
        '<th style="padding:6px 8px;text-align:left;font-size:11px">Pine output</th>'
        '</tr></thead>'
        '<tbody>' + "".join(rows_html) + '</tbody></table>'
    )
    st.markdown(table_html, unsafe_allow_html=True)

# LLM-suggestion review.
llm_segments = [
    (i, seg) for i, seg in enumerate(result.segments)
    if seg.provenance == pipeline.PROV_LLM
]
if llm_segments:
    st.markdown("### LLM suggestions — accept or reject")
    st.caption(
        "Each card below is a Pine mapping the LLM proposed. **Accept** writes "
        "an exact-match pattern to `Agent/v2/suggestions/verified/<org>/`, so "
        "the next run on this token converts deterministically with no LLM hit. "
        "**Reject** logs an audit record under `rejected.log` (no pattern is "
        "added)."
    )

    decisions = st.session_state.setdefault("v2_decisions", {})
    org_for_save = result.org

    for i, seg in llm_segments:
        seg_key = f"seg_{id(result)}_{i}"   # stable for this result instance
        decision = decisions.get(seg_key)

        with st.container(border=True):
            col_a, col_b = st.columns(2, gap="medium")
            with col_a:
                st.markdown("**JDA source**")
                for tok in seg.source_jda_tokens:
                    st.code(tok.unparse(), language=None)
            with col_b:
                st.markdown("**LLM Pine suggestion**")
                for tok in seg.pine_outputs:
                    st.code(tok.unparse(), language=None)
                if seg.issues:
                    st.warning(
                        f"Validator flagged {len(seg.issues)} issue(s) on this "
                        "suggestion — review carefully before accepting."
                    )

            if decision == "accepted":
                st.success("✓ Accepted — this mapping is now a verified pattern.")
            elif decision == "rejected":
                st.info("✗ Rejected — logged to rejected.log; no pattern saved.")
            else:
                btn_a, btn_r, _ = st.columns([1, 1, 4])
                if btn_a.button("Accept", key=f"accept_{i}", type="primary"):
                    for src_tok, pine_tok in zip(seg.source_jda_tokens, seg.pine_outputs):
                        suggestion_store.accept_suggestion(
                            src_tok.unparse(),
                            pine_tok.unparse(),
                            org=org_for_save,
                            source_template=st.session_state.get("v2_source_name"),
                            source_segment_index=i,
                        )
                    decisions[seg_key] = "accepted"
                    st.rerun()
                if btn_r.button("Reject", key=f"reject_{i}"):
                    for src_tok, pine_tok in zip(seg.source_jda_tokens, seg.pine_outputs):
                        suggestion_store.reject_suggestion(
                            src_tok.unparse(),
                            pine_tok.unparse(),
                            org=org_for_save,
                            source_template=st.session_state.get("v2_source_name"),
                            source_segment_index=i,
                        )
                    decisions[seg_key] = "rejected"
                    st.rerun()

    # Hint to re-run after acceptance.
    if any(decisions.get(f"seg_{id(result)}_{i}") == "accepted" for i, _ in llm_segments):
        st.info(
            "Re-run the conversion (sidebar **Run v2 conversion** button) to see "
            "the accepted suggestions match deterministically — no LLM call this time."
        )

# Issues panel.
st.markdown("### Validation issues")
if not result.issues:
    st.success("No issues raised by the validator.")
else:
    if errors:
        st.markdown(f"**Errors ({len(errors)})**")
        for e in errors:
            idx = "" if e.token_index is None else f" @token #{e.token_index}"
            st.error(f"`[{e.rule_id}]{idx}` {e.message}\n\n{e.token_text or ''}")
    if warnings:
        with st.expander(f"Warnings ({len(warnings)})"):
            for w in warnings:
                idx = "" if w.token_index is None else f" @token #{w.token_index}"
                st.warning(f"`[{w.rule_id}]{idx}` {w.message}\n\n{w.token_text or ''}")

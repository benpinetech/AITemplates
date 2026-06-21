"""Streamlit page for the v2 chunk-based pipeline.

Runs ``v2.pipeline.convert_template`` on an uploaded or pasted JDA
RTF and displays:

  - source vs converted RTF (rendered side-by-side)
  - per-segment provenance (which pattern matched, or LLM, or unmatched)
  - validation issues
  - a stats summary

LLM fallback is **on by default** when ``OPENAI_API_KEY`` is set —
gpt-5.5 + RAG-tool access makes patterns+LLM the best config (macro
F1 ≈ 0.674 on the OBA corpus, vs 0.670 patterns-only). Disable the
toggle for fast deterministic runs.
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
# v2 lives at ``Agent/v2/`` — put ``Agent/`` on the path so ``from pipeline…``
# resolves. (Putting the repo root on the path was the old bug — it
# made Python look for ``repo_root/v2/`` which doesn't exist.)
sys.path.insert(0, str(AGENT_DIR))

from rtf_render import init_renderer, render_rtf, highlight_legacy, highlight_pine
from pipeline import pipeline
from pipeline.engine import suggestion_store
from pipeline.engine.llm_fallback import LlmFallback, OpenAILlmClient
from pipeline.grammar.loaders import load_agency_overrides
from pipeline.parser import pine_parser
from pipeline.patterns import loader as pattern_loader

# Pick up OPENAI_API_KEY from the repo-root .env (same file v1 uses)
# so the LLM toggle works without manually exporting in the shell.
try:
    from dotenv import load_dotenv
    load_dotenv(AGENT_DIR.parent / ".env")
except ImportError:
    pass


st.set_page_config(page_title="v2 Pipeline", layout="wide")
st.title("v2 Pipeline")
st.caption(
    "Chunk-based pattern engine + LLM fallback (gpt-5.5 with RAG over "
    "the Pine syntax reference). Patterns + LLM hits **macro F1 ≈ "
    "0.674** on the OBA corpus and is the recommended config when "
    "`OPENAI_API_KEY` is set. Patterns-only stays available for fast "
    "iteration."
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
        pipeline.PROV_PATTERN: "#1f883d",      # green
        pipeline.PROV_LLM:     "#9a6700",      # amber
        pipeline.PROV_UNMATCHED: "#d1242f",   # red
        pipeline.PROV_EDIT:    "#7848c1",      # purple
    }.get(prov, "#666")


def parse_pine_text(text: str):
    """Parse a converter-edited Pine string into a tuple of PineTokens.

    Returns ``(tokens, error)``. On success ``error`` is None. The
    parser is lenient: blank input yields an empty tuple (valid — the
    converter explicitly cleared the segment). Multiple ``@[...]``
    tokens separated by whitespace are accepted.
    """
    text = (text or "").strip()
    if not text:
        return (), None
    tokens = []
    i = 0
    while i < len(text):
        start = text.find("@[", i)
        if start < 0:
            # Trailing prose after the last token is fine; bail.
            if tokens:
                # If we already parsed tokens and there's leftover that
                # isn't whitespace, surface it.
                if text[i:].strip():
                    return None, (
                        f"unexpected text after Pine tokens: {text[i:].strip()!r}"
                    )
            else:
                return None, f"expected @[...] but got: {text[i:].strip()!r}"
            break
        # Skip any non-whitespace prefix between i and start.
        if text[i:start].strip():
            return None, f"unexpected text before @[: {text[i:start].strip()!r}"
        depth = 1
        j = start + 2
        while j < len(text) and depth > 0:
            c = text[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            j += 1
        if depth != 0:
            return None, "unbalanced brackets in Pine expression"
        candidate = text[start:j]
        try:
            tokens.append(pine_parser.parse(candidate))
        except pine_parser.PineParseError as e:
            return None, f"Pine parse error in {candidate!r}: {e}"
        i = j
    return tuple(tokens), None


def _load_library():
    """Load the active pattern library fresh on every call. We don't
    cache this because accepting a suggestion writes a new TOML and
    the next run needs to see it."""
    return pattern_loader.load_library().patterns


# ── sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("v2 conversion")
    st.caption("Upload a JDA RTF or paste text below, pick an agency, run.")

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

    agency = st.selectbox(
        "Org context",
        options=["oba", "any"],
        index=0,
        help=(
            "Determines which patterns apply and which Pine vocabulary "
            "the validator uses. Required by Phase 5."
        ),
    )

    has_openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    use_llm = st.checkbox(
        f"Enable LLM fallback{'' if has_openai_key else ' — no OPENAI_API_KEY set'}",
        value=has_openai_key,
        disabled=not has_openai_key,
        help=(
            "Recommended ON. Uses gpt-5.5 by default — see model picker "
            "below to choose a faster/cheaper option or a custom model. "
            "Privacy: prompt contains only AST + vocabulary + few-shot "
            "patterns + grammar — never prose."
        ),
    )

    # Model picker — only the models the 16-template eval validated as
    # usable for this task. Excluded: gpt-5.4-nano (F1 = 0.154, collapsed),
    # o3 (F1 = 0.42–0.45 with empty-output failures on long templates).
    # See Agent/LLM_CAPABILITY_FINDINGS.md for the model spread results.
    _MODEL_OPTIONS = {
        "gpt-5.5  —  recommended (best F1, default)": "gpt-5.5",
        "gpt-5.4-mini  —  ~8× faster, small F1 cost": "gpt-5.4-mini",
        "Custom…": None,
    }
    _env_model = os.environ.get("OPENAI_MODEL", "").strip()
    if _env_model and _env_model not in _MODEL_OPTIONS.values():
        # Surface the env-set model as a labeled option so the user knows
        # what's in effect when OPENAI_MODEL is set externally.
        _MODEL_OPTIONS = {
            f"{_env_model}  —  from OPENAI_MODEL env": _env_model,
            **_MODEL_OPTIONS,
        }

    model_label = st.selectbox(
        "LLM model",
        options=list(_MODEL_OPTIONS.keys()),
        index=0,
        disabled=not (use_llm and has_openai_key),
        help=(
            "Validated on the 16-template eval. gpt-5.5 is the strongest "
            "available OpenAI model for this task. gpt-5.4-mini trades "
            "F1 for speed (0.44 vs 0.55, ~8× faster). nano and reasoning "
            "models (o3) were tested and excluded — they regress on this "
            "shape of task."
        ),
    )
    selected_model = _MODEL_OPTIONS[model_label]
    if selected_model is None:
        # Custom model — let the user type one in.
        selected_model = st.text_input(
            "Custom model id",
            value="",
            placeholder="e.g. gpt-5.4, gpt-5.5-2026-04-23",
            disabled=not (use_llm and has_openai_key),
            help=(
                "Any OpenAI chat-completion model id accessible to your "
                "API key. Note: reasoning models (o*) are not recommended "
                "— they overthink this task and frequently return empty."
            ),
        ).strip() or None

    run_btn = st.button("Run v2 conversion", type="primary", width="stretch")


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
    agency_overrides = load_agency_overrides(agency) if agency != "any" else None

    fb = None
    if use_llm and has_openai_key:
        try:
            # ``selected_model`` comes from the sidebar dropdown. When the
            # user picks the default option it's "gpt-5.5"; "Custom…" can
            # leave it empty (falls back to OpenAILlmClient's default).
            fb = LlmFallback(
                client=OpenAILlmClient(model=selected_model or None),
                library=library,
                agency_overrides=agency_overrides,
            )
        except RuntimeError as e:
            st.error(f"LLM client failed to start: {e}")
            st.stop()

    template_name = st.session_state.get("v2_source_name")
    with st.spinner("Converting…"):
        result = pipeline.convert_template(
            source, agency=agency,
            library=library,
            agency_overrides=agency_overrides,
            llm_fallback=fb,
            template_name=template_name if template_name and template_name != "<pasted>" else None,
        )

    st.session_state.v2_source = source
    st.session_state.v2_result = result
    # Keep the unedited result around so Reset can restore an
    # individual segment to its pre-edit Pine output. Rebuilds always
    # start from this baseline, so removing an edit cleanly reverts.
    st.session_state.v2_original_result = result
    # New run → wipe any per-segment Accept/Reject decisions AND any
    # pending in-memory edits from the previous run so the UI resets.
    st.session_state.v2_decisions = {}
    st.session_state.v2_edits = {}


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

# ── Segments: edit every segment, optionally persist as a scoped override ──
st.markdown("### Segments")
if not result.segments:
    st.caption("No bracketed expressions were extracted from the source.")
else:
    # Audience + template_name drive the persist-scope picker. The
    # pipeline already classified — surface what it decided so the
    # converter knows what "this audience" means before persisting.
    audience = result.audience
    template_name = result.template_name
    summary_bits = [f"audience: **{audience or '(unclassified)'}**"]
    if template_name:
        summary_bits.append(f"template: **{template_name}**")
    st.caption(
        "Every segment is editable. **Save edit** updates this run only. "
        "**Save edit + persist** writes a verified suggestion scoped to one of: "
        "this template, this audience, or globally. Scoped overrides take "
        "priority over seed patterns on the next run. — "
        + " · ".join(summary_bits)
    )

    agency_for_save = result.agency
    edits = st.session_state.setdefault("v2_edits", {})

    for i, seg in enumerate(result.segments):
        color = provenance_color(seg.provenance)
        prov_label = seg.provenance
        if seg.pattern is not None:
            prov_label = f"pattern: {seg.pattern.id}"
        jda_text = " ".join(t.unparse() for t in seg.source_jda_tokens) or "(none)"
        current_pine = " ".join(t.unparse() for t in seg.pine_outputs)

        with st.container(border=True):
            # Header row: provenance + segment index + issue counter.
            header_md = (
                f'<div style="font-size:12px">'
                f'<span style="color:#666">segment #{i}</span> &nbsp;·&nbsp; '
                f'<span style="color:{color};font-weight:600">'
                f'{html.escape(prov_label)}</span>'
            )
            if seg.issues:
                header_md += (
                    f' &nbsp;·&nbsp; <span style="color:#d1242f">'
                    f'{len(seg.issues)} validation issue(s)</span>'
                )
            header_md += '</div>'
            st.markdown(header_md, unsafe_allow_html=True)

            col_jda, col_pine = st.columns([1, 1], gap="medium")
            with col_jda:
                st.markdown("**JDA source**")
                st.code(jda_text, language=None)

            with col_pine:
                st.markdown("**Pine output (edit freely)**")
                edit_key = f"edit_text_{id(result)}_{i}"
                edited_text = st.text_area(
                    "pine output",
                    value=edits.get(i, current_pine),
                    key=edit_key,
                    label_visibility="collapsed",
                    height=80,
                    placeholder=(
                        "@[Entity.first.Field] — leave blank to clear this "
                        "segment's output"
                    ),
                )

            # Show any validator issues for this segment.
            if seg.issues:
                for issue in seg.issues:
                    sev = (issue.severity or "warning").lower()
                    msg = f"`[{issue.rule_id}]` {issue.message}"
                    (st.error if sev == "error" else st.warning)(msg)

            # Action row.
            scope_default = (
                "this template" if template_name else
                "this audience" if audience else
                "global"
            )
            scope_choice = st.radio(
                "persist scope",
                options=["this template", "this audience", "global"],
                index=["this template", "this audience", "global"].index(scope_default),
                key=f"scope_{id(result)}_{i}",
                horizontal=True,
                help=(
                    "Where to write the verified suggestion when you click "
                    "**Save edit + persist**. *this template* limits it to "
                    "documents with this exact filename. *this audience* "
                    "limits it to documents the classifier labels with this "
                    "audience. *global* re-applies on every template "
                    "(known cache-poisoning hazard — use sparingly)."
                ),
            )

            btn_save, btn_save_persist, btn_reset, _ = st.columns([1, 1.4, 1, 3])
            save_clicked = btn_save.button(
                "Save edit", key=f"save_{id(result)}_{i}",
                help="Apply the edited Pine to this run's output. Nothing is written to disk.",
            )
            persist_clicked = btn_save_persist.button(
                "Save edit + persist",
                key=f"persist_{id(result)}_{i}",
                type="primary",
                help=(
                    "Apply the edit AND write a verified suggestion under the "
                    "chosen scope so it re-applies on future runs."
                ),
            )
            reset_clicked = btn_reset.button(
                "Reset", key=f"reset_{id(result)}_{i}",
                help="Discard this edit and restore the original segment output.",
            )

            if reset_clicked:
                edits.pop(i, None)
                # Drop the widget's session value so the next rerun
                # repopulates from the (now-restored) original output.
                st.session_state.pop(edit_key, None)
                st.rerun()

            if save_clicked or persist_clicked:
                parsed, err = parse_pine_text(edited_text)
                if err is not None:
                    st.error(f"can't apply edit: {err}")
                else:
                    edits[i] = edited_text
                    # Persist if requested. Scope picker → scope tuple.
                    if persist_clicked:
                        if scope_choice == "this template" and not template_name:
                            st.warning(
                                "No template filename is set for this run — "
                                "persisting at 'this audience' or 'global' "
                                "instead. (Upload an RTF rather than pasting "
                                "to get a template name.)"
                            )
                            scope_choice = "this audience" if audience else "global"
                        if scope_choice == "this audience" and not audience:
                            st.warning(
                                "Audience classifier returned no result — "
                                "persisting at 'global' instead."
                            )
                            scope_choice = "global"

                        scope = {
                            "this template": (
                                suggestion_store.SCOPE_TEMPLATE,
                                template_name or "",
                            ),
                            "this audience": (
                                suggestion_store.SCOPE_AUDIENCE,
                                audience or "",
                            ),
                            "global": (suggestion_store.SCOPE_GLOBAL, ""),
                        }[scope_choice]

                        # Persist the whole segment as one mapping —
                        # match list = source JDA tokens, rewrite list
                        # = converter-edited Pine tokens. 1:1, 1:N,
                        # N:M, and N:0 (drop) all collapse to a single
                        # accept_suggestion call.
                        jda_list = [t.unparse() for t in seg.source_jda_tokens]
                        pine_list = [t.unparse() for t in parsed]
                        try:
                            suggestion_store.accept_suggestion(
                                jda_list, pine_list,
                                agency=agency_for_save,
                                scope=scope,
                                source_template=template_name,
                                source_segment_index=i,
                                note=f"hand-edit on {seg.provenance}",
                            )
                            shape = f"{len(jda_list)}→{len(pine_list)}"
                            kind_note = " (drop)" if not pine_list else ""
                            st.success(
                                f"Edit saved and persisted at scope "
                                f"`{scope[0]}:{scope[1] or '*'}` — "
                                f"shape {shape}{kind_note}."
                            )
                        except Exception as e:  # noqa: BLE001
                            st.error(f"persist failed: {e}")
                    st.rerun()

    # Apply all pending edits → rebuild the converted RTF on the right.
    # ALWAYS rebuild from the original (unedited) result so removing
    # an edit cleanly reverts that segment.
    original = st.session_state.get("v2_original_result", result)
    if edits:
        new_edits = {}
        for i, raw in edits.items():
            parsed, err = parse_pine_text(raw)
            if err is None:
                new_edits[i] = parsed
        if new_edits:
            new_result = pipeline.rebuild_result_with_edits(original, new_edits)
            st.session_state.v2_result = new_result
    else:
        # No edits pending — make sure the working result is the original.
        st.session_state.v2_result = original

if st.session_state.get("v2_edits"):
    if st.button("Clear all edits", key="clear_edits"):
        st.session_state.v2_edits = {}
        st.rerun()

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

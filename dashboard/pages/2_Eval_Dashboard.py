import sys
import html
import json
import os
import re
import signal
import subprocess
from collections import Counter
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# ── path setup ───────────────────────────────────────────────────────────────
GUI_DIR   = Path(__file__).resolve().parent.parent
AGENT_DIR = GUI_DIR.parent
EVAL_RUNS_DIR  = AGENT_DIR / "eval_runs"
GROUND_TRUTH_DIR = AGENT_DIR / "ground_truth"

DEFAULT_LEGACY_DIR = str(GROUND_TRUTH_DIR / "evaluation_templates" / "jda_to_pine" / "legacy")
DEFAULT_PINE_DIR   = str(GROUND_TRUTH_DIR / "evaluation_templates" / "jda_to_pine" / "pine")

sys.path.insert(0, str(GUI_DIR))
from rtf_render import init_renderer, render_rtf, highlight_legacy, highlight_pine, highlight_ground_truth

try:
    from dotenv import load_dotenv
    load_dotenv(AGENT_DIR.parent / ".env")
except ImportError:
    pass

st.set_page_config(page_title="Eval Dashboard", layout="wide")
st.title("Evaluation Dashboard")
st.caption("v2 chunk-based pipeline — runs and metrics from eval_v2.py.")

# ── handle pending stop request ───────────────────────────────────────────────
if st.session_state.get("stop_requested") and st.session_state.get("eval_proc_pid"):
    try:
        os.kill(st.session_state.eval_proc_pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    st.session_state.pop("stop_requested", None)
    st.session_state.pop("eval_proc_pid", None)
    st.session_state.eval_running = False
    st.warning("Run stopped early. Templates completed before stop are saved in the run history.")

# ── init renderer once ────────────────────────────────────────────────────────
if "renderer_checked" not in st.session_state:
    init_renderer("http://localhost:5000")
    st.session_state.renderer_checked = True


# ── helpers ───────────────────────────────────────────────────────────────────
def load_runs() -> list[dict]:
    runs = []
    if EVAL_RUNS_DIR.exists():
        for f in sorted(EVAL_RUNS_DIR.glob("*.json"), reverse=True):
            try:
                with open(f) as fh:
                    runs.append(json.load(fh))
            except Exception:
                pass
    return runs


def f1(recall: float, precision: float) -> float:
    return (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def token_box(label: str, tokens: list[str], color: str = "#e6edf3"):
    rows = "".join(
        f'<div style="padding:2px 0;color:{color};font-size:11px;font-family:monospace">'
        f'{html.escape(t)}</div>'
        for t in tokens
    ) or '<div style="color:#666;font-size:11px;font-family:monospace;padding:2px 0">—</div>'
    st.markdown(f"**{label}**")
    st.html(
        f'<div style="height:180px;overflow-y:scroll;border:1px solid #30363d;'
        f'border-radius:4px;padding:6px 8px;background:#0d1117">'
        f'{rows}</div>'
    )


def pct(v: float) -> str:
    return f"{v:.1%}"


def color_f1(v: float) -> str:
    if v >= 0.85:
        return "background-color:#c6efce;color:#276221"
    if v >= 0.65:
        return "background-color:#ffeb9c;color:#9c5700"
    return "background-color:#ffc7ce;color:#9c0006"


def render_template_panel(rtf_content: str, token_type: str, label: str):
    st.markdown(f"**{label}**")
    if not rtf_content:
        st.caption("No content saved for this run.")
        return
    rendered, method = render_rtf(rtf_content)
    st.caption(f"rendered via {method}")
    if method == "dotnet":
        st.components.v1.html(rendered, height=450, scrolling=True)
    else:
        if token_type == "legacy":
            highlighted = highlight_legacy(rendered)
        elif token_type == "ground_truth":
            highlighted = highlight_ground_truth(rendered)
        else:
            highlighted = highlight_pine(rendered)
        st.markdown(
            f'<div style="font-family:monospace;white-space:pre-wrap;font-size:11px;'
            f'max-height:450px;overflow-y:auto;border:1px solid #ddd;padding:8px">'
            f"{highlighted}</div>",
            unsafe_allow_html=True,
        )
    with st.expander("Raw RTF"):
        st.code(rtf_content[:4000], language=None)


# ── sidebar: run evaluation ───────────────────────────────────────────────────
with st.sidebar:
    st.header("Run Evaluation")
    eval_mode = st.radio("Mode", ["Batch"], horizontal=True)

    legacy_dir_input = st.text_input("Legacy dir", value=DEFAULT_LEGACY_DIR)
    pine_dir_input   = st.text_input("Pine dir",   value=DEFAULT_PINE_DIR)
    run_label        = st.text_input("Label (optional)", placeholder="e.g. v2 cold")

    v2_org = st.selectbox(
        "Org context",
        options=["oba", "any"],
        index=0,
        help="Picks which patterns + vocabulary apply.",
    )
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    st.caption(
        "**Recommended: patterns + LLM.** With gpt-5.5, patterns + LLM hits macro F1 ≈ 0.674 "
        "(best on this corpus). Patterns-only is faster (9 s vs ~24 min) and still gets F1 ≈ 0.670, "
        "so use it for iteration / debugging and the LLM run for production output."
        + ("" if has_key else
           " &nbsp; *Set `OPENAI_API_KEY` in `.env` to enable the LLM toggle.*")
    )
    v2_use_llm = st.checkbox(
        "Enable LLM fallback",
        value=has_key,
        disabled=not has_key,
        help="Hits the OpenAI API in one batched call per template. Adds ~5s per template.",
    )
    v2_auto_accept = st.checkbox(
        "Auto-accept LLM suggestions (writes to disk)",
        value=False,
        disabled=not v2_use_llm,
        help="Persists every LLM-produced (jda, pine) pair into suggestions/verified/<org>/. "
             "Past testing showed context-blind cached suggestions hurt 181 templates and helped "
             "only 7. Use only when you're prepared to audit and prune the cache.",
    )
    v2_reverse = st.checkbox(
        "Reverse template order",
        value=False,
        help="Diagnose whether precision drops are caused by template ordering or content.",
    )

    # ── template picker ───────────────────────────────────────────────────────
    _lp = Path(legacy_dir_input)
    _pp = Path(pine_dir_input)
    matched_pairs = sorted(
        f.name for f in _lp.glob("*.rtf") if (_pp / f.name).exists()
    ) if _lp.exists() and _pp.exists() else []

    if matched_pairs:
        st.caption(f"{len(matched_pairs)} template pairs found")
        c1, c2 = st.columns(2)
        if c1.button("All", width="stretch"):
            for n in matched_pairs:
                st.session_state[f"tmpl_{n}"] = True
        if c2.button("None", width="stretch"):
            for n in matched_pairs:
                st.session_state[f"tmpl_{n}"] = False

        with st.container(height=280):
            for name in matched_pairs:
                st.checkbox(
                    name.removesuffix(".rtf"),
                    value=st.session_state.get(f"tmpl_{name}", True),
                    key=f"tmpl_{name}",
                )

        selected = [n for n in matched_pairs if st.session_state.get(f"tmpl_{n}", True)]
    else:
        selected = []
        if legacy_dir_input and pine_dir_input:
            st.caption("No matched pairs found.")

    run_btn = st.button(
        "Run Batch",
        type="primary",
        width="stretch",
        disabled=not selected or bool(st.session_state.get("eval_running")),
    )

    if run_btn:
        st.session_state.eval_running            = True
        st.session_state.eval_legacy_dir         = legacy_dir_input
        st.session_state.eval_pine_dir           = pine_dir_input
        st.session_state.eval_label              = run_label
        st.session_state.eval_total_templates    = len(selected)
        st.session_state.eval_selected_templates = selected
        st.session_state.eval_v2_org             = v2_org
        st.session_state.eval_v2_use_llm         = v2_use_llm
        st.session_state.eval_v2_auto_accept     = v2_auto_accept
        st.session_state.eval_v2_reverse         = v2_reverse

    if st.session_state.get("eval_running"):
        if st.button("Stop Run", type="secondary", width="stretch"):
            st.session_state.stop_requested = True
            st.rerun()

# ── live eval log ─────────────────────────────────────────────────────────────
if st.session_state.get("eval_running"):
    total_templates = st.session_state.get("eval_total_templates", "?")
    combined_area = st.empty()

    cmd = [
        sys.executable, "-u",
        str(AGENT_DIR / "v2" / "tools" / "eval_v2.py"),
        "--legacy-dir", st.session_state.eval_legacy_dir,
        "--pine-dir",   st.session_state.eval_pine_dir,
        "--org",        st.session_state.get("eval_v2_org", "oba"),
    ]
    if st.session_state.get("eval_v2_use_llm"):
        cmd.append("--use-llm")
    if st.session_state.get("eval_v2_auto_accept"):
        cmd.append("--auto-accept")
    if st.session_state.get("eval_v2_reverse"):
        cmd.append("--reverse")
    if st.session_state.get("eval_label"):
        cmd += ["--label", st.session_state.eval_label]
    if st.session_state.get("eval_selected_templates"):
        cmd += ["--templates"] + st.session_state.eval_selected_templates

    env = {**os.environ, "PYTHONPATH": str(AGENT_DIR)}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(AGENT_DIR),
        env=env,
    )
    st.session_state.eval_proc_pid = proc.pid

    def _render(lines: list[str], done: int, total, current: str, status: str):
        escaped = html.escape("\n".join(lines))
        status_escaped = html.escape(status)
        progress_escaped = html.escape(current)
        combined_area.html(
            f'<div style="border:1px solid #30363d;border-radius:6px;overflow:hidden;font-family:monospace">'
            f'<div style="height:360px;overflow-y:scroll;display:flex;flex-direction:column-reverse;'
            f'background:#0d1117">'
            f'<div style="font-size:11px;padding:10px;color:#e6edf3;white-space:pre-wrap;word-wrap:break-word">'
            f'{escaped}</div>'
            f'</div>'
            f'<div style="border-top:1px solid #30363d;padding:7px 12px;background:#0d1117;color:#e6edf3;font-size:13px">'
            f'<span style="color:#58a6ff;font-weight:bold">{done} / {total}</span>'
            f'{"&nbsp;&nbsp;" + progress_escaped if progress_escaped else ""}'
            f'</div>'
            f'<div style="border-top:1px solid #30363d;padding:7px 12px;background:#0d1117;'
            f'color:#3fb950;font-size:14px">&#9654; {status_escaped}</div>'
            f'</div>'
        )

    def _is_noise(line: str) -> bool:
        s = line.strip()
        return s.startswith(("from ", "import ")) and not s.startswith(("from __", "import __"))

    eval_lines: list[str] = []
    status_msg = ""
    current_template = ""
    completed = 0
    in_report = False

    for line in proc.stdout:
        clean = line.rstrip()
        if _is_noise(clean):
            continue

        stripped = clean.strip()
        is_separator = bool(stripped) and all(c in "=#" for c in stripped)

        if is_separator:
            in_report = True
            eval_lines.append(clean)
        elif in_report:
            if not stripped:
                eval_lines.append(clean)
            elif clean.startswith(" "):
                eval_lines.append(clean)
                if "  Recall:" in clean:
                    completed += 1
            else:
                in_report = False
                status_msg = clean
        elif clean:
            status_msg = clean
            if clean.startswith("Evaluating "):
                current_template = clean[len("Evaluating "):]

        _render(eval_lines, completed, total_templates, current_template, status_msg)

    proc.wait()
    st.session_state.eval_running = False
    st.session_state.pop("eval_proc_pid", None)

    if proc.returncode == 0:
        st.success("Batch evaluation complete. Scroll down to see results.")
    else:
        st.error(f"Evaluation failed (exit code {proc.returncode}).")
    st.rerun()

# ── load runs ─────────────────────────────────────────────────────────────────
runs = load_runs()

if not runs:
    st.info("No evaluation runs yet. Use the sidebar to run your first evaluation.")
    st.stop()

# ── overview chart + runs table ───────────────────────────────────────────────
st.subheader("All Runs")

chart_df = pd.DataFrame([
    {
        "Run": r.get("label") or r["run_id"],
        "Run ID": r["run_id"],
        "Timestamp": r["timestamp"][:19].replace("T", " "),
        "Status": r.get("status", "complete"),
        "Templates": r.get("summary", {}).get("total_templates", 0),
        "Skipped (broken)": len(r.get("skipped_broken", []) or []),
        "Micro F1": round(r.get("summary", {}).get("micro_f1", 0.0), 4),
        "Micro Recall": round(r.get("summary", {}).get("micro_recall", 0.0), 4),
        "Micro Precision": round(r.get("summary", {}).get("micro_precision", 0.0), 4),
        "Macro F1": round(r.get("summary", {}).get("macro_f1", 0.0), 4),
        "Macro Recall": round(r.get("summary", {}).get("macro_recall", 0.0), 4),
        "Macro Precision": round(r.get("summary", {}).get("macro_precision", 0.0), 4),
    }
    for r in runs
]).sort_values("Timestamp")

st.markdown("**Macro F1 trend**")
macro_long_df = chart_df.melt(
    id_vars=["Run", "Run ID", "Timestamp", "Status", "Templates", "Skipped (broken)"],
    value_vars=["Macro F1", "Macro Recall", "Macro Precision"],
    var_name="Metric",
    value_name="Value",
)
macro_base = alt.Chart(macro_long_df).encode(
    x=alt.X("Timestamp:N", sort=None, title=None, axis=alt.Axis(labelAngle=-30)),
    y=alt.Y("Value:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%")),
    color=alt.Color(
        "Metric:N",
        legend=alt.Legend(orient="top"),
        scale=alt.Scale(
            domain=["Macro F1", "Macro Recall", "Macro Precision"],
            range=["#1f77b4", "#7fb8d8", "#a8c8e1"],
        ),
    ),
    strokeWidth=alt.condition(
        "datum.Metric == 'Macro F1'",
        alt.value(3),
        alt.value(1.5),
    ),
    tooltip=[
        alt.Tooltip("Run:N"),
        alt.Tooltip("Timestamp:N"),
        alt.Tooltip("Templates:Q"),
        alt.Tooltip("Skipped (broken):Q"),
        alt.Tooltip("Status:N"),
        alt.Tooltip("Metric:N"),
        alt.Tooltip("Value:Q", format=".1%"),
    ],
)
st.altair_chart(macro_base.mark_line(point=True).properties(height=280), width="stretch")
st.caption(
    "Macro F1 is the per-template-averaged F1. Runs that skip broken "
    "templates (e.g. legacy with 0 JDA tokens, empty pine) are scored only "
    "on eligible templates — the **Skipped (broken)** column shows how "
    "many were excluded for each run."
)

st.markdown("**Micro F1 trend** (token-pooled across all templates)")
long_df = chart_df.melt(
    id_vars=["Run", "Run ID", "Timestamp", "Status", "Templates"],
    value_vars=["Micro F1", "Micro Recall", "Micro Precision"],
    var_name="Metric",
    value_name="Value",
)
base = alt.Chart(long_df).encode(
    x=alt.X("Timestamp:N", sort=None, title=None, axis=alt.Axis(labelAngle=-30)),
    y=alt.Y("Value:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%")),
    color=alt.Color("Metric:N", legend=alt.Legend(orient="top")),
    tooltip=[
        alt.Tooltip("Run:N"),
        alt.Tooltip("Timestamp:N"),
        alt.Tooltip("Templates:Q"),
        alt.Tooltip("Status:N"),
        alt.Tooltip("Metric:N"),
        alt.Tooltip("Value:Q", format=".1%"),
    ],
)
st.altair_chart(base.mark_line(point=True, strokeWidth=2).properties(height=220), width="stretch")

def _status_badge(s: str) -> str:
    return {
        "complete": "✅ complete",
        "in_progress": "⏳ in progress",
        "interrupted": "⚠ interrupted",
    }.get(s, s)

table_df = pd.DataFrame([
    {
        "Run ID": r["run_id"],
        "Label": r.get("label", ""),
        "Status": _status_badge(r.get("status", "complete")),
        "Timestamp": r["timestamp"][:19].replace("T", " "),
        "Templates": r["summary"]["total_templates"],
        "Micro F1": r["summary"]["micro_f1"],
        "Micro Recall": r["summary"]["micro_recall"],
        "Micro Precision": r["summary"]["micro_precision"],
        "Macro F1": r["summary"]["macro_f1"],
        "Duration": fmt_duration(r["summary"]["total_duration"]),
    }
    for r in runs
])

run_ids = [r["run_id"] for r in runs]
selected_run_id = st.selectbox(
    "Select a run to inspect",
    options=run_ids,
    format_func=lambda rid: next(
        (f"{r['timestamp'][:19]} — {r.get('label') or rid}" for r in runs if r["run_id"] == rid), rid
    ),
)

st.dataframe(
    table_df.style.format({
        "Micro F1": "{:.1%}", "Micro Recall": "{:.1%}",
        "Micro Precision": "{:.1%}", "Macro F1": "{:.1%}",
    }),
    width="stretch",
    height=200,
)

st.divider()

# ── run detail ────────────────────────────────────────────────────────────────
selected_run = next((r for r in runs if r["run_id"] == selected_run_id), None)
if not selected_run:
    st.stop()

runs_by_time = sorted(runs, key=lambda r: r["timestamp"])
sel_idx = next((i for i, r in enumerate(runs_by_time) if r["run_id"] == selected_run_id), -1)
previous_run = runs_by_time[sel_idx - 1] if sel_idx > 0 else None

s = selected_run["summary"]
status_label = _status_badge(selected_run.get("status", "complete"))
st.subheader(f"Run: {selected_run.get('label') or selected_run_id}")
st.caption(f"{selected_run['timestamp'][:19].replace('T', ' ')}  ·  {status_label}")

def _delta_pct(curr: float, prev: float | None) -> str | None:
    if prev is None:
        return None
    d = curr - prev
    if abs(d) < 1e-6:
        return None
    return f"{d:+.1%}"

prev_s = previous_run["summary"] if previous_run else None
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Micro F1",        pct(s["micro_f1"]),        delta=_delta_pct(s["micro_f1"],        prev_s["micro_f1"] if prev_s else None))
m2.metric("Micro Recall",    pct(s["micro_recall"]),    delta=_delta_pct(s["micro_recall"],    prev_s["micro_recall"] if prev_s else None))
m3.metric("Micro Precision", pct(s["micro_precision"]), delta=_delta_pct(s["micro_precision"], prev_s["micro_precision"] if prev_s else None))
m4.metric("Macro F1",        pct(s["macro_f1"]),        delta=_delta_pct(s["macro_f1"],        prev_s["macro_f1"] if prev_s else None))
m5.metric("Templates",       s["total_templates"])
m6.metric("Duration",        fmt_duration(s["total_duration"]))

skipped_broken = selected_run.get("skipped_broken") or []
if skipped_broken:
    with st.expander(f"Skipped {len(skipped_broken)} broken template(s) — corpus issues, not agent quality"):
        for s_ in skipped_broken:
            st.markdown(f"- **{s_.get('name', '?')}** — {s_.get('reason', '')}")

templates = selected_run.get("templates", [])

if templates:
    per_tmpl_df = pd.DataFrame([
        {
            "Template": t["name"].removesuffix(".rtf"),
            "F1": t["f1"],
            "Recall": t["recall"],
            "Precision": t["precision"],
            "Missing": t["missing_count"],
            "Extra": t["extra_count"],
        }
        for t in templates
    ])
    bar = (
        alt.Chart(per_tmpl_df)
        .mark_bar()
        .encode(
            x=alt.X("Template:N", sort="-y", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("F1:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%")),
            color=alt.Color(
                "F1:Q",
                scale=alt.Scale(
                    domain=[0.0, 0.65, 0.85, 1.0],
                    range=["#c0392b", "#e67e22", "#f1c40f", "#27ae60"],
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("Template:N"),
                alt.Tooltip("F1:Q", format=".1%"),
                alt.Tooltip("Recall:Q", format=".1%"),
                alt.Tooltip("Precision:Q", format=".1%"),
                alt.Tooltip("Missing:Q"),
                alt.Tooltip("Extra:Q"),
            ],
        )
        .properties(height=260, title="Per-template F1 (this run)")
    )
    st.altair_chart(bar, width="stretch")

st.markdown("### Token Analytics — This Run")

run_missing = Counter()
run_extra = Counter()
for t in templates:
    run_missing.update(t.get("missing", []))
    run_extra.update(t.get("extra", []))

if previous_run:
    prev_missing = Counter()
    prev_extra = Counter()
    for t in previous_run.get("templates", []):
        prev_missing.update(t.get("missing", []))
        prev_extra.update(t.get("extra", []))
    prev_label = previous_run.get("label") or previous_run["run_id"]
    st.caption(f"Δ columns compare against previous run: **{prev_label}**")
else:
    prev_missing = prev_extra = Counter()
    st.caption("No previous run to compare against — showing absolute counts.")

def _token_table(curr: Counter, prev: Counter, top: int = 15) -> pd.DataFrame:
    rows = []
    for token, count in curr.most_common(top):
        delta = count - prev.get(token, 0)
        rows.append({"Token": token, "Count": count, "Δ": delta})
    return pd.DataFrame(rows)

an_col1, an_col2 = st.columns(2)

with an_col1:
    st.markdown(f"**Most Common Missing ({sum(run_missing.values())} total)**")
    if run_missing:
        st.dataframe(_token_table(run_missing, prev_missing), width="stretch", hide_index=True, height=380)
    else:
        st.caption("None — all expected tokens were produced.")

    newly_missing = sorted(set(run_missing) - set(prev_missing))
    fixed = sorted(set(prev_missing) - set(run_missing))
    if previous_run and (newly_missing or fixed):
        with st.expander(f"Regressions: {len(newly_missing)} new missing · {len(fixed)} fixed"):
            if newly_missing:
                st.markdown("**Newly missing this run**")
                for t in newly_missing[:50]:
                    st.code(t, language=None)
            if fixed:
                st.markdown("**No longer missing**")
                for t in fixed[:50]:
                    st.code(t, language=None)

with an_col2:
    st.markdown(f"**Most Common Extra ({sum(run_extra.values())} total)**")
    if run_extra:
        st.dataframe(_token_table(run_extra, prev_extra), width="stretch", hide_index=True, height=380)
    else:
        st.caption("None — no hallucinated tokens.")

    newly_extra = sorted(set(run_extra) - set(prev_extra))
    resolved_extra = sorted(set(prev_extra) - set(run_extra))
    if previous_run and (newly_extra or resolved_extra):
        with st.expander(f"Regressions: {len(newly_extra)} new extra · {len(resolved_extra)} resolved"):
            if newly_extra:
                st.markdown("**Newly extra this run**")
                for t in newly_extra[:50]:
                    st.code(t, language=None)
            if resolved_extra:
                st.markdown("**No longer extra**")
                for t in resolved_extra[:50]:
                    st.code(t, language=None)

st.divider()
st.markdown("### Per-template Breakdown")

if not templates:
    st.info("This run produced no per-template results.")
    st.stop()

tmpl_df = pd.DataFrame([
    {
        "Template": t["name"],
        "Recall": t["recall"],
        "Precision": t["precision"],
        "F1": t["f1"],
        "Correct": t["correct_count"],
        "Missing": t["missing_count"],
        "Extra": t["extra_count"],
        "Expected": t["total_expected"],
        "Agent": t["total_agent"],
        "Duration": fmt_duration(t["duration"]),
    }
    for t in templates
])

st.dataframe(
    tmpl_df.style.map(color_f1, subset=["F1"]).format({
        "Recall": "{:.1%}", "Precision": "{:.1%}", "F1": "{:.1%}",
    }),
    width="stretch",
    height=300,
)

selected_tmpl_name = st.selectbox("Select a template to inspect", options=[t["name"] for t in templates])

st.divider()

selected_tmpl = next((t for t in templates if t["name"] == selected_tmpl_name), None)
if not selected_tmpl:
    st.stop()

tr, tp = selected_tmpl["recall"], selected_tmpl["precision"]
st.subheader(f"Template: {selected_tmpl_name}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Recall",    pct(tr))
c2.metric("Precision", pct(tp))
c3.metric("F1",        pct(f1(tr, tp)))
c4.metric("Duration",  f"{selected_tmpl['duration']:.1f}s")

tok_col1, tok_col2, tok_col3 = st.columns(3)
with tok_col1:
    token_box(f"Correct ({selected_tmpl['correct_count']})", selected_tmpl.get("correct", []))
with tok_col2:
    token_box(f"Missing ({selected_tmpl['missing_count']})", selected_tmpl.get("missing", []), color="#f85149")
with tok_col3:
    token_box(f"Extra ({selected_tmpl['extra_count']})", selected_tmpl.get("extra", []), color="#e3b341")

st.divider()
st.subheader("Template Comparison")
col1, col2, col3 = st.columns(3)

with col1:
    render_template_panel(selected_tmpl.get("legacy_content", ""), "legacy", "Legacy (input)")
with col2:
    render_template_panel(selected_tmpl.get("generated_content", ""), "pine", "Generated (agent)")
with col3:
    render_template_panel(selected_tmpl.get("ground_truth_content", ""), "pine", "Ground Truth")

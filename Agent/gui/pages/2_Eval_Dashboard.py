import sys
import html
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# ── path setup ───────────────────────────────────────────────────────────────
GUI_DIR = Path(__file__).resolve().parent.parent
AGENT_DIR = GUI_DIR.parent
SRC_DIR = AGENT_DIR / "src"
EVAL_RUNS_DIR = AGENT_DIR / "eval_runs"
GROUND_TRUTH_DIR = AGENT_DIR / "ground_truth"

DEFAULT_LEGACY_DIR = str(GROUND_TRUTH_DIR / "evaluation_templates" / "jda_to_pine" / "legacy")
DEFAULT_PINE_DIR = str(GROUND_TRUTH_DIR / "evaluation_templates" / "jda_to_pine" / "pine")

sys.path.insert(0, str(GUI_DIR))
from rtf_render import init_renderer, render_rtf, highlight_legacy, highlight_pine, highlight_ground_truth

st.set_page_config(page_title="Eval Dashboard (v1)", layout="wide")
st.title("Evaluation Dashboard")
st.caption(
    "v1 — runs and metrics from the original token-by-token agent. "
    "For the chunk-based pipeline, see the **v2 Pipeline** page."
)

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
    """Render a labelled, fixed-height scrollable box of token strings."""
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
    """Render one RTF panel (legacy or pine) with highlights."""
    st.markdown(f"**{label}**")
    if not rtf_content:
        st.caption(f"No content saved for this run.")
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
    eval_mode = st.radio("Mode", ["Batch", "Single Template"], horizontal=True)

    if eval_mode == "Batch":
        legacy_dir_input = st.text_input("Legacy dir", value=DEFAULT_LEGACY_DIR)
        pine_dir_input   = st.text_input("Pine dir",   value=DEFAULT_PINE_DIR)
        run_label        = st.text_input("Label (optional)", placeholder="e.g. gpt-5-mini baseline")

        # ── template picker ───────────────────────────────────────────────────
        _lp = Path(legacy_dir_input)
        _pp = Path(pine_dir_input)
        matched_pairs = sorted(
            f.name for f in _lp.glob("*.rtf") if (_pp / f.name).exists()
        ) if _lp.exists() and _pp.exists() else []

        if matched_pairs:
            st.caption(f"{len(matched_pairs)} template pairs found")
            c1, c2 = st.columns(2)
            if c1.button("All", use_container_width=True):
                for n in matched_pairs:
                    st.session_state[f"tmpl_{n}"] = True
            if c2.button("None", use_container_width=True):
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
            use_container_width=True,
            disabled=not selected or bool(st.session_state.get("eval_running")),
        )

        if run_btn:
            st.session_state.eval_running             = True
            st.session_state.eval_mode                = "batch"
            st.session_state.eval_legacy_dir          = legacy_dir_input
            st.session_state.eval_pine_dir            = pine_dir_input
            st.session_state.eval_label               = run_label
            st.session_state.eval_total_templates     = len(selected)
            st.session_state.eval_selected_templates  = selected
            st.session_state.single_result            = None

        if st.session_state.get("eval_running") and st.session_state.get("eval_mode") == "batch":
            if st.button("Stop Run", type="secondary", use_container_width=True):
                st.session_state.stop_requested = True
                st.rerun()

    else:
        st.caption("One-off run — not saved to history.")
        legacy_upload = st.file_uploader("Legacy RTF", type=["rtf"])
        pine_upload   = st.file_uploader("Pine reference RTF", type=["rtf"])
        run_single    = st.button(
            "Run Single",
            type="primary",
            use_container_width=True,
            disabled=not (legacy_upload and pine_upload),
        )

        if run_single and legacy_upload and pine_upload:
            st.session_state.eval_running          = True
            st.session_state.eval_mode             = "single"
            st.session_state.single_legacy_content = legacy_upload.read().decode("utf-8", errors="replace")
            st.session_state.single_pine_content   = pine_upload.read().decode("utf-8", errors="replace")
            st.session_state.single_legacy_name    = legacy_upload.name
            st.session_state.single_result         = None

# ── live eval log ─────────────────────────────────────────────────────────────
if st.session_state.get("eval_running"):
    mode = st.session_state.get("eval_mode", "batch")

    if mode == "single":
        st.subheader("Running one-off evaluation…")
        log_area = st.empty()
        tmpdir = Path(tempfile.mkdtemp())
        try:
            legacy_path = tmpdir / st.session_state.single_legacy_name
            pine_path   = tmpdir / "pine_reference.rtf"
            result_path = tmpdir / "result.json"

            legacy_path.write_text(st.session_state.single_legacy_content)
            pine_path.write_text(st.session_state.single_pine_content)

            cmd = [
                sys.executable, "-u",
                str(SRC_DIR / "main.py"),
                "--single-eval",
                "--legacy-file", str(legacy_path),
                "--pine-file",   str(pine_path),
                "--output-json", str(result_path),
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(AGENT_DIR),
            )

            log_lines: list[str] = []
            for line in proc.stdout:
                log_lines.append(line.rstrip())
                log_area.code("\n".join(log_lines[-50:]), language=None)

            proc.wait()

            if proc.returncode == 0 and result_path.exists():
                st.session_state.single_result = json.loads(result_path.read_text())
                st.success("One-off evaluation complete.")
            else:
                st.error(f"Evaluation failed (exit code {proc.returncode}).")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        st.session_state.eval_running = False
        st.rerun()

    else:
        total_templates = st.session_state.get("eval_total_templates", "?")
        combined_area = st.empty()

        cmd = [
            sys.executable, "-u",
            str(SRC_DIR / "main.py"),
            "-e",
            "--legacy-dir", st.session_state.eval_legacy_dir,
            "--pine-dir",   st.session_state.eval_pine_dir,
        ]
        if st.session_state.get("eval_label"):
            cmd += ["--label", st.session_state.eval_label]
        if st.session_state.get("eval_selected_templates"):
            cmd += ["--templates"] + st.session_state.eval_selected_templates

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(AGENT_DIR),
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

# ── single eval result ────────────────────────────────────────────────────────
if st.session_state.get("single_result"):
    sr = st.session_state.single_result
    st.subheader(f"One-off Result — {sr['name']}")
    st.caption("Not saved to run history.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recall",    pct(sr["recall"]))
    c2.metric("Precision", pct(sr["precision"]))
    c3.metric("F1",        pct(sr["f1"]))
    c4.metric("Duration",  f"{sr['duration']:.1f}s")

    tok1, tok2, tok3 = st.columns(3)
    with tok1:
        token_box(f"Correct ({sr['correct_count']})", sr.get("correct", []))
    with tok2:
        token_box(f"Missing ({sr['missing_count']})", sr.get("missing", []), color="#f85149")
    with tok3:
        token_box(f"Extra ({sr['extra_count']})", sr.get("extra", []), color="#e3b341")

    st.markdown("**Template Comparison**")
    v1, v2, v3 = st.columns(3)
    with v1:
        render_template_panel(sr.get("legacy_content", ""),       "legacy", "Legacy (input)")
    with v2:
        render_template_panel(sr.get("generated_content", ""),    "pine",   "Generated (agent)")
    with v3:
        render_template_panel(sr.get("ground_truth_content", ""), "ground_truth", "Ground Truth")

    st.divider()

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
        "Templates": r["summary"]["total_templates"],
        "Micro F1": round(r["summary"]["micro_f1"], 4),
        "Micro Recall": round(r["summary"]["micro_recall"], 4),
        "Micro Precision": round(r["summary"]["micro_precision"], 4),
        "Macro F1": round(r["summary"]["macro_f1"], 4),
    }
    for r in runs
]).sort_values("Timestamp")

# Long-form for multi-metric altair chart
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
line = base.mark_line(point=True, strokeWidth=2)
st.altair_chart(line.properties(height=260), use_container_width=True)

# Runs summary table
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
    use_container_width=True,
    height=200,
)

st.divider()

# ── run detail ────────────────────────────────────────────────────────────────
selected_run = next((r for r in runs if r["run_id"] == selected_run_id), None)
if not selected_run:
    st.stop()

# Find the previous run (by timestamp) for delta comparison
runs_by_time = sorted(runs, key=lambda r: r["timestamp"])
sel_idx = next((i for i, r in enumerate(runs_by_time) if r["run_id"] == selected_run_id), -1)
previous_run = runs_by_time[sel_idx - 1] if sel_idx > 0 else None

s = selected_run["summary"]
status_label = _status_badge(selected_run.get("status", "complete"))
st.subheader(f"Run: {selected_run.get('label') or selected_run_id}")
st.caption(f"{selected_run['timestamp'][:19].replace('T', ' ')}  ·  {status_label}")

# Metric deltas vs previous run (when available)
def _delta_pct(curr: float, prev: float | None) -> str | None:
    if prev is None:
        return None
    d = curr - prev
    if abs(d) < 1e-6:
        return None
    return f"{d:+.1%}"

prev_s = previous_run["summary"] if previous_run else None
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Micro F1",        pct(s["micro_f1"]),        delta=_delta_pct(s["micro_f1"], prev_s["micro_f1"] if prev_s else None))
m2.metric("Micro Recall",    pct(s["micro_recall"]),    delta=_delta_pct(s["micro_recall"], prev_s["micro_recall"] if prev_s else None))
m3.metric("Micro Precision", pct(s["micro_precision"]), delta=_delta_pct(s["micro_precision"], prev_s["micro_precision"] if prev_s else None))
m4.metric("Macro F1",        pct(s["macro_f1"]),        delta=_delta_pct(s["macro_f1"], prev_s["macro_f1"] if prev_s else None))
m5.metric("Templates",       s["total_templates"])
m6.metric("Duration",        fmt_duration(s["total_duration"]))

templates = selected_run.get("templates", [])

# ── per-template F1 bar chart for the selected run ────────────────────────────
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
    st.altair_chart(bar, use_container_width=True)

# ── token analytics for the selected run (with delta vs previous) ─────────────
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
        df = _token_table(run_missing, prev_missing)
        st.dataframe(df, use_container_width=True, hide_index=True, height=380)
    else:
        st.caption("None — all expected tokens were produced.")

    # New missing this run
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
        df = _token_table(run_extra, prev_extra)
        st.dataframe(df, use_container_width=True, hide_index=True, height=380)
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

def _style_f1(val):
    return color_f1(val)

st.dataframe(
    tmpl_df.style.map(_style_f1, subset=["F1"]).format({
        "Recall": "{:.1%}", "Precision": "{:.1%}", "F1": "{:.1%}",
    }),
    use_container_width=True,
    height=300,
)

tmpl_names = [t["name"] for t in templates]
selected_tmpl_name = st.selectbox("Select a template to inspect", options=tmpl_names)

st.divider()

# ── template detail ───────────────────────────────────────────────────────────
selected_tmpl = next((t for t in templates if t["name"] == selected_tmpl_name), None)
if not selected_tmpl:
    st.stop()

tr, tp = selected_tmpl["recall"], selected_tmpl["precision"]
tf = f1(tr, tp)

st.subheader(f"Template: {selected_tmpl_name}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Recall",    pct(tr))
c2.metric("Precision", pct(tp))
c3.metric("F1",        pct(tf))
c4.metric("Duration",  f"{selected_tmpl['duration']:.1f}s")

# Token sets
tok_col1, tok_col2, tok_col3 = st.columns(3)
with tok_col1:
    token_box(f"Correct ({selected_tmpl['correct_count']})", selected_tmpl.get("correct", []))
with tok_col2:
    token_box(f"Missing ({selected_tmpl['missing_count']})", selected_tmpl.get("missing", []), color="#f85149")
with tok_col3:
    token_box(f"Extra ({selected_tmpl['extra_count']})", selected_tmpl.get("extra", []), color="#e3b341")

st.divider()

# Three-column template viewer
st.subheader("Template Comparison")
v1, v2, v3 = st.columns(3)

with v1:
    render_template_panel(selected_tmpl.get("legacy_content", ""), "legacy", "Legacy (input)")

with v2:
    render_template_panel(selected_tmpl.get("generated_content", ""), "pine", "Generated (agent)")

with v3:
    render_template_panel(selected_tmpl.get("ground_truth_content", ""), "pine", "Ground Truth")


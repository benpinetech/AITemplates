import sys
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

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
from rtf_render import init_renderer, render_rtf, highlight_pine, highlight_legacy

st.set_page_config(page_title="Eval Dashboard", layout="wide")
st.title("Evaluation Dashboard")

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
        highlighted = highlight_legacy(rendered) if token_type == "legacy" else highlight_pine(rendered)
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
        run_btn          = st.button("Run Batch", type="primary", use_container_width=True)

        if run_btn:
            st.session_state.eval_running    = True
            st.session_state.eval_mode       = "batch"
            st.session_state.eval_legacy_dir = legacy_dir_input
            st.session_state.eval_pine_dir   = pine_dir_input
            st.session_state.eval_label      = run_label
            st.session_state.single_result   = None

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
        st.subheader("Running batch evaluation…")
        log_area = st.empty()

        cmd = [
            sys.executable, "-u",
            str(SRC_DIR / "main.py"),
            "-e",
            "--legacy-dir", st.session_state.eval_legacy_dir,
            "--pine-dir",   st.session_state.eval_pine_dir,
        ]
        if st.session_state.get("eval_label"):
            cmd += ["--label", st.session_state.eval_label]

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
        st.session_state.eval_running = False

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
        st.markdown(f"**Correct ({sr['correct_count']})**")
        for tok in sr.get("correct", []):
            st.markdown(f"<small><code>{tok}</code></small>", unsafe_allow_html=True)
    with tok2:
        st.markdown(f"**Missing ({sr['missing_count']})**")
        for tok in sr.get("missing", []):
            st.markdown(f'<small><code style="color:red">{tok}</code></small>', unsafe_allow_html=True)
    with tok3:
        st.markdown(f"**Extra ({sr['extra_count']})**")
        for tok in sr.get("extra", []):
            st.markdown(f'<small><code style="color:orange">{tok}</code></small>', unsafe_allow_html=True)

    st.markdown("**Template Comparison**")
    v1, v2, v3 = st.columns(3)
    with v1:
        render_template_panel(sr.get("legacy_content", ""),       "legacy", "Legacy (input)")
    with v2:
        render_template_panel(sr.get("generated_content", ""),    "pine",   "Generated (agent)")
    with v3:
        render_template_panel(sr.get("ground_truth_content", ""), "pine",   "Ground Truth")

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
        "Timestamp": r["timestamp"][:19].replace("T", " "),
        "Micro F1": round(r["summary"]["micro_f1"], 4),
        "Micro Recall": round(r["summary"]["micro_recall"], 4),
        "Micro Precision": round(r["summary"]["micro_precision"], 4),
    }
    for r in runs
]).sort_values("Timestamp")

st.line_chart(
    chart_df.set_index("Timestamp")[["Micro F1", "Micro Recall", "Micro Precision"]],
    height=250,
)

# Runs summary table
table_df = pd.DataFrame([
    {
        "Run ID": r["run_id"],
        "Label": r.get("label", ""),
        "Timestamp": r["timestamp"][:19].replace("T", " "),
        "Templates": r["summary"]["total_templates"],
        "Micro F1": r["summary"]["micro_f1"],
        "Micro Recall": r["summary"]["micro_recall"],
        "Micro Precision": r["summary"]["micro_precision"],
        "Macro F1": r["summary"]["macro_f1"],
        "Duration (s)": round(r["summary"]["total_duration"], 1),
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

s = selected_run["summary"]
st.subheader(f"Run: {selected_run.get('label') or selected_run_id}")
st.caption(selected_run["timestamp"][:19].replace("T", " "))

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Micro F1",        pct(s["micro_f1"]))
m2.metric("Micro Recall",    pct(s["micro_recall"]))
m3.metric("Micro Precision", pct(s["micro_precision"]))
m4.metric("Macro F1",        pct(s["macro_f1"]))
m5.metric("Templates",       s["total_templates"])
m6.metric("Duration",        f"{s['total_duration']:.1f}s")

templates = selected_run.get("templates", [])
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
        "Duration (s)": round(t["duration"], 1),
    }
    for t in templates
])

def _style_f1(val):
    return color_f1(val)

st.dataframe(
    tmpl_df.style.applymap(_style_f1, subset=["F1"]).format({
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
    st.markdown(f"**Correct ({selected_tmpl['correct_count']})**")
    for tok in selected_tmpl.get("correct", []):
        st.markdown(f"<small><code>{tok}</code></small>", unsafe_allow_html=True)

with tok_col2:
    st.markdown(f"**Missing ({selected_tmpl['missing_count']})**")
    for tok in selected_tmpl.get("missing", []):
        st.markdown(
            f'<small><code style="color:red">{tok}</code></small>',
            unsafe_allow_html=True,
        )

with tok_col3:
    st.markdown(f"**Extra ({selected_tmpl['extra_count']})**")
    for tok in selected_tmpl.get("extra", []):
        st.markdown(
            f'<small><code style="color:orange">{tok}</code></small>',
            unsafe_allow_html=True,
        )

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


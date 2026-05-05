import sys
import html
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

import streamlit as st

# ── path setup ────────────────────────────────────────────────────────────────
GUI_DIR  = Path(__file__).resolve().parent.parent
AGENT_DIR = GUI_DIR.parent
SRC_DIR  = AGENT_DIR / "src"
TEMPLATE_OUTPUT_DIR = AGENT_DIR / "template_output"

sys.path.insert(0, str(GUI_DIR))
from rtf_render import init_renderer, render_rtf, highlight_pine, highlight_legacy

st.set_page_config(page_title="Template Runner (v1)", layout="wide")
st.title("Template Runner")
st.caption(
    "v1 — runs the original `Agent/src/main.py` agent on a single RTF. "
    "For the chunk-based pipeline, see the **v2 Pipeline** page."
)

# ── handle pending stop request ───────────────────────────────────────────────
if st.session_state.get("runner_stop_requested") and st.session_state.get("runner_proc_pid"):
    try:
        os.kill(st.session_state.runner_proc_pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    st.session_state.pop("runner_stop_requested", None)
    st.session_state.pop("runner_proc_pid", None)
    st.session_state.runner_running = False
    st.warning("Run stopped early.")

# ── init renderer once ────────────────────────────────────────────────────────
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
        highlighted = highlight_legacy(rendered) if token_type == "legacy" else highlight_pine(rendered)
        st.markdown(
            f'<div style="font-family:monospace;white-space:pre-wrap;font-size:11px;'
            f'max-height:500px;overflow-y:auto;border:1px solid #ddd;padding:8px">'
            f"{highlighted}</div>",
            unsafe_allow_html=True,
        )
    with st.expander("Raw RTF"):
        st.code(rtf_content[:4000], language=None)


# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Run Template")
    legacy_upload = st.file_uploader("Legacy RTF", type=["rtf"])

    run_btn = st.button(
        "Run",
        type="primary",
        use_container_width=True,
        disabled=not legacy_upload or st.session_state.get("runner_running", False),
    )

    if run_btn and legacy_upload:
        st.session_state.runner_running        = True
        st.session_state.runner_legacy_name    = legacy_upload.name
        st.session_state.runner_legacy_content = legacy_upload.read().decode("utf-8", errors="replace")
        st.session_state.runner_generated_content = None

    if st.session_state.get("runner_running"):
        if st.button("Stop", type="secondary", use_container_width=True):
            st.session_state.runner_stop_requested = True
            st.rerun()

# ── live run log ──────────────────────────────────────────────────────────────
if st.session_state.get("runner_running"):
    st.subheader("Running…")
    combined_area = st.empty()

    legacy_name    = st.session_state.runner_legacy_name
    legacy_content = st.session_state.runner_legacy_content

    tmpdir = Path(tempfile.mkdtemp())
    try:
        legacy_path = tmpdir / legacy_name
        legacy_path.write_text(legacy_content)

        cmd = [
            sys.executable, "-u",
            str(SRC_DIR / "main.py"),
            str(legacy_path),
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(AGENT_DIR),
        )
        st.session_state.runner_proc_pid = proc.pid

        def _render(lines: list[str], status: str):
            escaped = html.escape("\n".join(lines))
            status_escaped = html.escape(status)
            combined_area.html(
                f'<div style="border:1px solid #30363d;border-radius:6px;overflow:hidden;font-family:monospace">'
                f'<div style="height:360px;overflow-y:scroll;display:flex;flex-direction:column-reverse;'
                f'background:#0d1117">'
                f'<div style="font-size:11px;padding:10px;color:#e6edf3;white-space:pre-wrap;word-wrap:break-word">'
                f'{escaped}</div>'
                f'</div>'
                f'<div style="border-top:1px solid #30363d;padding:7px 12px;background:#0d1117;'
                f'color:#3fb950;font-size:14px">&#9654; {status_escaped}</div>'
                f'</div>'
            )

        def _is_noise(line: str) -> bool:
            s = line.strip()
            return s.startswith(("from ", "import ")) and not s.startswith(("from __", "import __"))

        log_lines: list[str] = []
        status_msg = ""
        for line in proc.stdout:
            clean = line.rstrip()
            if _is_noise(clean):
                continue
            log_lines.append(clean)
            if clean:
                status_msg = clean
            _render(log_lines, status_msg)

        proc.wait()

        if proc.returncode == 0:
            output_path = TEMPLATE_OUTPUT_DIR / legacy_name
            if output_path.exists():
                st.session_state.runner_generated_content = output_path.read_text(errors="replace")
            else:
                st.warning(f"Run finished but output file not found at {output_path}")
        else:
            st.error(f"Run failed (exit code {proc.returncode}).")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    st.session_state.runner_running = False
    st.session_state.pop("runner_proc_pid", None)
    st.rerun()

# ── results ───────────────────────────────────────────────────────────────────
legacy_content    = st.session_state.get("runner_legacy_content")
generated_content = st.session_state.get("runner_generated_content")

if not legacy_content and not generated_content:
    st.info("Upload a legacy RTF file and click Run to get started.")
    st.stop()

left, right = st.columns(2, gap="large")

with left:
    render_panel(legacy_content or "", "legacy", "Legacy (input)")

with right:
    if generated_content:
        render_panel(generated_content, "pine", "Generated (agent)")
    else:
        st.markdown("**Generated (agent)**")
        st.caption("Output will appear here after the run completes.")

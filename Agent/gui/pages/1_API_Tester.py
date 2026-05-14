import sys
import json
import time
from pathlib import Path

import requests
import streamlit as st

# ── path setup ──────────────────────────────────────────────────────────────
GUI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GUI_DIR))
from rtf_render import init_renderer, render_rtf, highlight_pine, highlight_legacy

st.set_page_config(page_title="API Tester (v1)", layout="wide")
st.title("API Tester")
st.caption(
    "v1 — sends requests to the v1 FastAPI server. "
    "For the chunk-based pipeline, see the **v2 Pipeline** page."
)

# ── init RTF renderer once per session ──────────────────────────────────────
if "renderer_checked" not in st.session_state:
    dotnet_url = st.session_state.get("dotnet_url", "http://localhost:5000")
    available = init_renderer(dotnet_url)
    st.session_state.renderer_checked = True
    st.session_state.dotnet_available = available

# ── sidebar config ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Connection")
    base_url = st.text_input("FastAPI base URL", value="http://localhost:8000")
    endpoint = st.text_input("Endpoint path", value="/migrate")
    method = st.selectbox("Method", ["POST", "GET"])

    st.divider()
    st.header("Rendering")
    dotnet_url = st.text_input(".NET server URL (for RTF→HTML)", value="http://localhost:5000")
    if st.button("Test .NET connection"):
        ok = init_renderer(dotnet_url)
        st.session_state.dotnet_available = ok
        if ok:
            st.success("Connected")
        else:
            st.warning("Not reachable — using striprtf fallback")

    renderer_label = "dotnet" if st.session_state.get("dotnet_available") else "striprtf"
    st.caption(f"Renderer: **{renderer_label}**")

# ── main layout ──────────────────────────────────────────────────────────────
left, right = st.columns([1, 1], gap="large")

with left:
    st.subheader("Request")

    uploaded = st.file_uploader("Upload RTF file", type=["rtf"])
    pasted = st.text_area("…or paste RTF / plain text", height=120, placeholder="Paste content here")

    # Build default body when a file or paste is provided
    source_content = None
    if uploaded is not None:
        source_content = uploaded.read().decode("utf-8", errors="replace")
    elif pasted.strip():
        source_content = pasted.strip()

    # Editable JSON body — pre-populated from source, but fully editable
    if "request_body" not in st.session_state:
        st.session_state.request_body = json.dumps({"rtf_content": ""}, indent=2)

    if source_content and st.button("← Load into request body"):
        try:
            existing = json.loads(st.session_state.request_body)
        except Exception:
            existing = {}
        existing["rtf_content"] = source_content
        st.session_state.request_body = json.dumps(existing, indent=2)

    body_text = st.text_area(
        "Request body (JSON)",
        value=st.session_state.request_body,
        height=300,
        key="body_editor",
    )
    st.session_state.request_body = body_text

    send = st.button("Send", type="primary", width="stretch")

# ── send request ─────────────────────────────────────────────────────────────
if send:
    url = base_url.rstrip("/") + endpoint
    try:
        parsed_body = json.loads(body_text) if body_text.strip() else {}
    except json.JSONDecodeError as e:
        st.error(f"Invalid JSON in request body: {e}")
        st.stop()

    with right:
        with st.spinner("Sending…"):
            t0 = time.perf_counter()
            try:
                if method == "POST":
                    resp = requests.post(url, json=parsed_body, timeout=300)
                else:
                    resp = requests.get(url, params=parsed_body, timeout=300)
                elapsed = time.perf_counter() - t0
            except requests.exceptions.ConnectionError:
                st.error(f"Could not connect to {url}")
                st.stop()
            except requests.exceptions.Timeout:
                st.error("Request timed out (300s)")
                st.stop()

    with right:
        # Status bar
        color = "green" if resp.status_code < 300 else "red"
        st.markdown(
            f"**Status:** :{color}[{resp.status_code}]  &nbsp;&nbsp; "
            f"**Time:** {elapsed:.2f}s  &nbsp;&nbsp; "
            f"**Size:** {len(resp.content):,} bytes"
        )
        st.divider()

        # Try to parse as JSON
        try:
            data = resp.json()
        except Exception:
            st.subheader("Raw response")
            st.code(resp.text[:5000])
            st.stop()

        # ── Structured response view ──────────────────────────────────────
        # Detect RTF strings and mapping lists anywhere in the response
        rtf_fields: dict[str, str] = {}
        mapping_list: list | None = None

        def _walk(obj, path=""):
            if isinstance(obj, str) and obj.lstrip().startswith("{\\rtf"):
                rtf_fields[path or "content"] = obj
            elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
                keys = set(obj[0].keys())
                if {"legacy", "pine"} <= keys or {"Legacy", "Pine"} <= keys:
                    return obj
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    result = _walk(v, k)
                    if result is not None:
                        return result
            return None

        mapping_list = _walk(data)

        # Mappings table
        if mapping_list:
            import pandas as pd
            st.subheader(f"Mappings ({len(mapping_list)})")
            df = pd.DataFrame(mapping_list)
            st.dataframe(df, width="stretch", height=300)
            st.divider()

        # RTF rendered views
        if rtf_fields:
            # Gather the input RTF from the request body for side-by-side
            input_rtf = parsed_body.get("rtf_content") or parsed_body.get("legacy_template", "")

            tabs_labels = []
            tabs_content = []

            if input_rtf:
                tabs_labels.append("Legacy (input)")
                tabs_content.append(("legacy", input_rtf))

            for field_name, rtf_val in rtf_fields.items():
                tabs_labels.append(f"Generated ({field_name})")
                tabs_content.append(("pine", rtf_val))

            if tabs_labels:
                tabs = st.tabs(tabs_labels)
                for tab, (token_type, rtf_val) in zip(tabs, tabs_content):
                    with tab:
                        rendered, method_used = render_rtf(rtf_val)
                        st.caption(f"Rendered via {method_used}")
                        if method_used == "dotnet":
                            st.components.v1.html(rendered, height=500, scrolling=True)
                        else:
                            highlighted = (
                                highlight_legacy(rendered)
                                if token_type == "legacy"
                                else highlight_pine(rendered)
                            )
                            st.markdown(
                                f'<div style="font-family:monospace;white-space:pre-wrap;font-size:12px">'
                                f"{highlighted}</div>",
                                unsafe_allow_html=True,
                            )
                        with st.expander("Raw RTF"):
                            st.code(rtf_val[:3000], language=None)

        # Raw JSON (always available)
        with st.expander("Raw JSON response"):
            st.json(data)

else:
    with right:
        st.subheader("Response")
        st.caption("Response will appear here after sending.")

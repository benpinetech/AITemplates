import streamlit as st

st.set_page_config(page_title="Agent GUI", page_icon="⚖️", layout="wide")

st.title("JDA → Pine Agent")
st.markdown("""
Two pipelines run side by side. Pages on the left are tagged **(v1)** for the
original token-by-token agent and **(v2)** for the new chunk-based pattern engine.

| Page | Pipeline | Purpose |
|------|---------|---------|
| **API Tester** | v1 | Send requests to your running FastAPI and inspect responses |
| **Eval Dashboard** | v1 | Run evaluations, track metrics over time, drill into template diffs |
| **Template Runner** | v1 | Run the v1 agent against a single RTF file end-to-end |
| **v2 Pipeline** | v2 | Run the chunk-based engine on an RTF or pasted source — shows per-segment provenance, validation issues, and converted RTF |
""")

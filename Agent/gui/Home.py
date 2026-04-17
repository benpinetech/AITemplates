import streamlit as st

st.set_page_config(page_title="Agent GUI", page_icon="⚖️", layout="wide")

st.title("JDA → Pine Agent")
st.markdown("""
Use the sidebar to navigate between tools.

| Page | Purpose |
|------|---------|
| **API Tester** | Send requests to your running FastAPI and inspect responses |
| **Eval Dashboard** | Run evaluations, track metrics over time, drill into template diffs |
""")

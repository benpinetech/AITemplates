AGENT_DIR   := Agent
SRC_DIR     := $(AGENT_DIR)/src
GUI_DIR     := $(AGENT_DIR)/gui
VENV        := venv
PYTHON      := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
STREAMLIT   := $(VENV)/bin/streamlit

.PHONY: run\:eval dev install

# Start the FastAPI agent server and the Streamlit eval GUI in parallel.
# Ctrl-C kills both.
run\:eval:
	@$(PYTHON) $(SRC_DIR)/server.py & \
	SERVER_PID=$$!; \
	STREAMLIT_BROWSER_GATHER_USAGE_STATS=false $(STREAMLIT) run $(GUI_DIR)/Home.py --server.port 8501; \
	kill $$SERVER_PID 2>/dev/null; \
	wait $$SERVER_PID 2>/dev/null

# Streamlit only, with hot reload on save. No API server — assumes it's already running.
dev:
	STREAMLIT_BROWSER_GATHER_USAGE_STATS=false $(STREAMLIT) run $(GUI_DIR)/Home.py --server.port 8501 --server.runOnSave true

install:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		python3 -m venv $(VENV); \
	fi
	$(PIP) install -r requirements.txt

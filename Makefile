AGENT_DIR     := Agent
SRC_DIR       := $(AGENT_DIR)/src
GUI_DIR       := $(AGENT_DIR)/gui
CONVERTER_DIR := $(AGENT_DIR)/converter_app
VENV          := venv
PYTHON        := $(VENV)/bin/python
PIP           := $(VENV)/bin/pip
STREAMLIT     := $(VENV)/bin/streamlit

.PHONY: run\:eval run\:converter dev install cleandb help

# ─── Production converter (Electron + Svelte) ─────────────────────────────
# Two-pane document UI. Spawns the v2 pipeline (the project venv's Python
# in dev mode; a PyInstaller-built sidecar in packaged builds) via IPC.
# The Electron postinstall sometimes fails to extract its prebuilt
# binary under newer Node versions — this target self-heals that.
run\:converter:
	@command -v npm >/dev/null 2>&1 || { \
	  echo "✗ Node not found. Install: https://nodejs.org/"; exit 1; }
	cd $(CONVERTER_DIR) && \
	  if [ ! -d node_modules ]; then npm install; fi && \
	  if ! ./node_modules/.bin/electron --version >/dev/null 2>&1; then \
	    echo "→ Electron install is broken. Repairing…"; \
	    rm -rf node_modules/electron/dist node_modules/electron/path.txt && \
	    npm install electron --no-save >/dev/null 2>&1 || true; \
	    if ! ./node_modules/.bin/electron --version >/dev/null 2>&1; then \
	      echo "  npm postinstall didn't extract the binary"; \
	      echo "  (known incompatibility between extract-zip + newer Node versions)."; \
	      echo "  Falling back to manual unzip from the cached download…"; \
	      ZIP=$$(find ~/.cache/electron -name 'electron-v*-linux-*.zip' -printf '%T@ %p\n' 2>/dev/null \
	             | sort -rn | awk '{print $$2}' | head -1); \
	      if [ -z "$$ZIP" ] || [ ! -f "$$ZIP" ]; then \
	        echo "✗ No cached Electron zip in ~/.cache/electron. Try:"; \
	        echo "    cd $(CONVERTER_DIR) && rm -rf node_modules package-lock.json && npm install"; \
	        exit 1; \
	      fi; \
	      command -v unzip >/dev/null 2>&1 || { echo "✗ unzip not installed."; exit 1; }; \
	      rm -rf node_modules/electron/dist && \
	      unzip -q "$$ZIP" -d node_modules/electron/dist && \
	      printf "electron" > node_modules/electron/path.txt && \
	      chmod +x node_modules/electron/dist/electron && \
	      ./node_modules/.bin/electron --version >/dev/null 2>&1 || { \
	        echo "✗ Manual extract didn't help."; exit 1; }; \
	      echo "  ✓ repaired via manual unzip"; \
	    fi; \
	  fi && \
	  npm run dev

# ─── Streamlit eval dashboard ─────────────────────────────────────────────
# FastAPI agent server + Streamlit multi-page app in parallel.
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

# ─── Setup / housekeeping ─────────────────────────────────────────────────

install:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		python3 -m venv $(VENV); \
	fi
	$(PIP) install -r requirements.txt

cleandb:
	rm -rf $(AGENT_DIR)/chroma_db $(AGENT_DIR)/mapping_db
	@echo "Deleted chroma_db and mapping_db"

help:
	@echo "Targets:"
	@echo "  make run:converter   — production GUI (Electron + Svelte)"
	@echo "  make run:eval        — FastAPI server + Streamlit eval dashboard"
	@echo "  make dev             — Streamlit only (hot reload)"
	@echo ""
	@echo "Setup:"
	@echo "  make install         — create venv, install Python deps"
	@echo "  make cleandb         — wipe chroma_db / mapping_db"

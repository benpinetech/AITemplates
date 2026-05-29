CONVERTER_DIR := converter_app
DASHBOARD_DIR := dashboard
VENV          := venv
PYTHON        := $(VENV)/bin/python
PIP           := $(VENV)/bin/pip
STREAMLIT     := $(VENV)/bin/streamlit

.PHONY: run run\:converter run\:dashboard dev install build test help

# ─── Default entry point ──────────────────────────────────────────────────
# Safe to run immediately after cloning — creates the venv and installs deps
# if missing, then launches the converter.
run: _ensure-venv _ensure-npm run\:converter

_ensure-venv:
	@if [ ! -d "$(VENV)" ]; then \
		echo "→ Creating virtual environment…"; \
		python3 -m venv $(VENV); \
		echo "→ Installing Python dependencies…"; \
		$(PIP) install -r requirements.txt; \
	fi

_ensure-npm:
	@command -v npm >/dev/null 2>&1 || { \
	  echo "✗ Node/npm not found. Install from https://nodejs.org/ then re-run."; exit 1; }
	@if [ ! -d "$(CONVERTER_DIR)/node_modules" ]; then \
	  echo "→ Installing Node dependencies…"; \
	  cd $(CONVERTER_DIR) && npm install; \
	fi

.PHONY: _ensure-venv _ensure-npm

# ─── Production converter (Electron + Svelte) ─────────────────────────────
run\:converter:
	cd $(CONVERTER_DIR) && \
	  if ! ./node_modules/.bin/electron --version >/dev/null 2>&1; then \
	    echo "→ Electron install is broken. Repairing…"; \
	    rm -rf node_modules/electron/dist node_modules/electron/path.txt && \
	    npm install electron --no-save >/dev/null 2>&1 || true; \
	    if ! ./node_modules/.bin/electron --version >/dev/null 2>&1; then \
	      echo "  Falling back to manual unzip from the cached download…"; \
	      ZIP=$$(find ~/.cache/electron -name 'electron-v*-linux-*.zip' -printf '%T@ %p\n' 2>/dev/null \
	             | sort -rn | awk '{print $$2}' | head -1); \
	      if [ -z "$$ZIP" ] || [ ! -f "$$ZIP" ]; then \
	        echo "✗ No cached Electron zip. Try:"; \
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
run\:dashboard:
	STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
	  $(STREAMLIT) run $(DASHBOARD_DIR)/Home.py --server.port 8501

dev:
	STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
	  $(STREAMLIT) run $(DASHBOARD_DIR)/Home.py --server.port 8501 --server.runOnSave true

# ─── Tests ────────────────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest pipeline/tests -q

# ─── Setup ────────────────────────────────────────────────────────────────
install:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment…"; \
		python3 -m venv $(VENV); \
	fi
	$(PIP) install -r requirements.txt

# ─── Installer / packaged build ───────────────────────────────────────────
# Produces a distributable installer for the current platform.
# Requires: pip install pyinstaller  AND  npm install -g electron-builder
# Steps:
#   1. PyInstaller — packages the Python pipeline into a self-contained binary
#   2. electron-builder — wraps Electron + the sidecar into an OS installer
build: _ensure-venv _ensure-npm
	@echo "→ Building PyInstaller sidecar (onedir)…"
	$(PYTHON) -m PyInstaller sidecar.spec --noconfirm
	@echo "→ Staging sidecar bundle into converter_app/binaries/…"
	@rm -rf converter_app/binaries/jda_pine_sidecar
	@mkdir -p converter_app/binaries
	@cp -r dist/jda_pine_sidecar converter_app/binaries/jda_pine_sidecar
	@echo "→ Building Electron installer…"
	cd $(CONVERTER_DIR) && npx electron-builder --$(if $(filter Darwin,$(shell uname -s)),mac,$(if $(filter Windows_NT,$(OS)),win,linux))
	@echo "✓ Installer written to converter_app/release/"

help:
	@echo "Targets:"
	@echo "  make run             — first-run entry point: venv + deps + converter"
	@echo "  make run:converter   — launch the converter (assumes deps installed)"
	@echo "  make run:dashboard   — Streamlit eval dashboard"
	@echo "  make dev             — Streamlit dashboard with hot reload"
	@echo "  make test            — run the test suite"
	@echo "  make install         — create venv, install Python deps"
	@echo "  make build           — build distributable installer (requires pyinstaller)"

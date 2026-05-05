"""Main window for the JDA → Pine converter desktop app.

Layout:

    ┌──────────────────────────────────────────────────────────┐
    │ File   Edit   View   Help                                │   ← menu bar
    ├──────────────────────────────────────────────────────────┤
    │ ▸ Open  ▸ Convert  ▸ Save  │  Org [oba ▼]  │  ☐ LLM     │   ← toolbar
    ├──────────────────────────┬───────────────────────────────┤
    │ Source RTF               │  Converted RTF                │   ← QSplitter
    │ (highlighted)            │  (highlighted)                │
    │                          │                               │
    │                          │                               │
    ├──────────────────────────┴───────────────────────────────┤
    │ Suggestions │ Issues │ Segments                          │   ← bottom tabs
    │                                                          │
    ├──────────────────────────────────────────────────────────┤
    │  ✓ Idle  •  3 patterns matched, 1 LLM, 0 unmatched       │   ← status
    └──────────────────────────────────────────────────────────┘

Conversion is dispatched to a QThread (``conversion_worker``) so the
UI stays responsive even when the LLM is in the loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, QThread
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QWidget,
)

from . import settings as cfg
from .conversion_worker import run_conversion_async
from .widgets.issues_panel import IssuesPanel
from .widgets.rtf_viewer import RtfViewer
from .widgets.segments_panel import SegmentsPanel
from .widgets.settings_dialog import open_settings_dialog
from .widgets.spinner import BusyPage
from .widgets.suggestion_panel import SuggestionPanel


# Org options offered in the toolbar dropdown. Add to this list as new
# org_overrides files appear under v2/grammar/org_overrides/.
_ORG_OPTIONS = [("Oklahoma Bar Association (OBA)", "oba"),
                ("Any (org-agnostic)", "any")]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JDA → Pine Converter")
        self.resize(1280, 820)

        # Runtime state.
        self._source_rtf: str = ""
        self._source_path: Optional[Path] = None
        self._result = None    # last v2.pipeline.ConversionResult, or None
        self._thread: Optional[QThread] = None
        self._llm_skipped_for_no_key: bool = False

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_bottom_dock()
        self._build_status_bar()

        self._restore_geometry()

    # ─────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────
    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action_open = QAction("&Open RTF…", self)
        self._action_open.setShortcut(QKeySequence.Open)
        self._action_open.triggered.connect(self.open_file_dialog)
        file_menu.addAction(self._action_open)

        self._action_save = QAction("&Save Converted As…", self)
        self._action_save.setShortcut(QKeySequence.Save)
        self._action_save.setEnabled(False)
        self._action_save.triggered.connect(self.save_converted_dialog)
        file_menu.addAction(self._action_save)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        edit_menu = bar.addMenu("&Edit")
        # Single Settings… entry — opens a modal dialog where the user
        # can paste their OpenAI key, override the model, or set a
        # base URL for OpenAI-compatible endpoints. On macOS Qt
        # automatically reroutes Preferences-shortcut actions into the
        # application menu, so Cmd+, opens this dialog there too.
        settings_action = QAction("&Settings…", self)
        settings_action.setMenuRole(QAction.PreferencesRole)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.open_settings)
        edit_menu.addAction(settings_action)

        help_menu = bar.addMenu("&Help")
        about_action = QAction("&About JDA → Pine Converter", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(toolbar)

        self._open_btn = QAction("Open", self)
        self._open_btn.triggered.connect(self.open_file_dialog)
        toolbar.addAction(self._open_btn)

        self._convert_btn = QAction("Convert", self)
        self._convert_btn.triggered.connect(self.run_conversion)
        self._convert_btn.setEnabled(False)
        toolbar.addAction(self._convert_btn)

        self._save_btn = QAction("Save", self)
        self._save_btn.triggered.connect(self.save_converted_dialog)
        self._save_btn.setEnabled(False)
        toolbar.addAction(self._save_btn)

        toolbar.addSeparator()

        org_label = QLabel("Org ")
        org_label.setObjectName("toolbarLabel")
        toolbar.addWidget(org_label)

        self._org_combo = QComboBox()
        for label, value in _ORG_OPTIONS:
            self._org_combo.addItem(label, value)
        # Restore previous choice.
        saved_org = cfg.get_org()
        for i in range(self._org_combo.count()):
            if self._org_combo.itemData(i) == saved_org:
                self._org_combo.setCurrentIndex(i)
                break
        self._org_combo.currentIndexChanged.connect(
            lambda _: cfg.set_org(self._org_combo.currentData())
        )
        toolbar.addWidget(self._org_combo)

        # Spacer.
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy(),
                             spacer.sizePolicy().verticalPolicy())
        spacer.setMinimumWidth(16)
        toolbar.addWidget(spacer)

        # LLM fallback is always on (using OpenAI). The mapper sees a
        # quiet status indicator so they know whether the API key is
        # configured. Click it to open Settings.
        self._llm_status_btn = QAction("LLM: not configured", self)
        self._llm_status_btn.triggered.connect(self.open_settings)
        toolbar.addAction(self._llm_status_btn)
        self._refresh_llm_status()

    def _build_central(self) -> None:
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setObjectName("mainSplitter")
        self._splitter.setHandleWidth(2)
        self._splitter.setChildrenCollapsible(False)

        self._source_view = RtfViewer(
            placeholder="Open a JDA RTF (Cmd/Ctrl+O) to get started.",
        )
        self._converted_view = RtfViewer(placeholder="")

        # Stack the converted viewer + a spinner page. Conversions can
        # take several seconds with the LLM in the loop; swapping to
        # the spinner gives the user immediate feedback that something
        # is happening.
        self._busy_page = BusyPage("Converting…")
        self._converted_stack = QStackedWidget()
        self._converted_stack.addWidget(self._converted_view)   # index 0
        self._converted_stack.addWidget(self._busy_page)         # index 1

        self._splitter.addWidget(self._wrap_with_caption("Legacy (JDA)", self._source_view))
        self._splitter.addWidget(self._wrap_with_caption("Converted (Pine)", self._converted_stack))
        self._splitter.setSizes([640, 640])
        self.setCentralWidget(self._splitter)

    def _wrap_with_caption(self, caption: str, body: QWidget) -> QWidget:
        """Wrap any pane body (RtfViewer, QStackedWidget, …) with a
        small caption strip on top."""
        container = QWidget()
        from PySide6.QtWidgets import QVBoxLayout
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        cap = QLabel(caption)
        cap.setObjectName("paneCaption")
        v.addWidget(cap)
        v.addWidget(body)
        return container

    def _build_bottom_dock(self) -> None:
        self._tabs = QTabWidget()
        self._tabs.setObjectName("bottomTabs")
        self._tabs.setDocumentMode(True)

        self._suggestion_panel = SuggestionPanel(
            on_accept=self._handle_accept,
            on_reject=self._handle_reject,
        )
        self._issues_panel = IssuesPanel()
        self._segments_panel = SegmentsPanel()

        self._tabs.addTab(self._suggestion_panel, "Suggestions")
        self._tabs.addTab(self._issues_panel, "Issues")
        self._tabs.addTab(self._segments_panel, "Segments")

        from PySide6.QtWidgets import QDockWidget
        dock = QDockWidget("Review", self)
        dock.setObjectName("bottomDock")
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setWidget(self._tabs)
        dock.setMinimumHeight(280)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

    def _build_status_bar(self) -> None:
        self._status = QStatusBar()
        self._status.setSizeGripEnabled(False)
        self._status_label = QLabel("Idle. Open an RTF to begin.")
        self._status.addWidget(self._status_label, 1)
        self._stats_label = QLabel("")
        self._stats_label.setObjectName("statsLabel")
        self._status.addPermanentWidget(self._stats_label)
        self.setStatusBar(self._status)

    # ─────────────────────────────────────────────────────────────────
    # Geometry persistence
    # ─────────────────────────────────────────────────────────────────
    def _restore_geometry(self) -> None:
        s = cfg.app_settings()
        geom = s.value(cfg.KEY_GEOMETRY)
        if geom:
            self.restoreGeometry(geom)
        state = s.value(cfg.KEY_WINDOW_STATE)
        if state:
            self.restoreState(state)

    def closeEvent(self, event) -> None:
        s = cfg.app_settings()
        s.setValue(cfg.KEY_GEOMETRY, self.saveGeometry())
        s.setValue(cfg.KEY_WINDOW_STATE, self.saveState())
        super().closeEvent(event)

    # ─────────────────────────────────────────────────────────────────
    # File / API actions
    # ─────────────────────────────────────────────────────────────────
    def open_file_dialog(self) -> None:
        last_dir = cfg.get_last_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Open JDA RTF", last_dir,
            "RTF Files (*.rtf);;Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self._source_path = path
        self._source_rtf = content
        cfg.set_last_dir(str(path.parent))
        self._source_view.set_text(content)
        self._converted_view.set_text("")
        # New file → make sure the spinner page isn't lingering from
        # an interrupted run.
        self._busy_page.stop()
        self._converted_stack.setCurrentIndex(0)
        self._suggestion_panel.populate([])
        self._issues_panel.populate([])
        self._segments_panel.populate([])
        self._stats_label.setText("")
        self._status_label.setText(f"Opened {path.name}")
        self._convert_btn.setEnabled(True)
        self._save_btn.setEnabled(False)
        self._action_save.setEnabled(False)

    def save_converted_dialog(self) -> None:
        if self._result is None:
            return
        last_dir = cfg.get_last_dir()
        suggested = (self._source_path.stem + ".pine.rtf"
                     if self._source_path else "converted.rtf")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Converted RTF", str(Path(last_dir) / suggested),
            "RTF Files (*.rtf);;All Files (*)",
        )
        if not path:
            return
        Path(path).write_text(self._result.converted_rtf, encoding="utf-8")
        self._status_label.setText(f"Saved to {path}")

    def open_settings(self) -> None:
        """Show the modal Settings dialog. Refreshes the toolbar's LLM
        status indicator on close so the mapper sees the new state."""
        if open_settings_dialog(self):
            self._refresh_llm_status()

    def _refresh_llm_status(self) -> None:
        """Update the toolbar text + tooltip based on whether an
        OpenAI key is currently stored."""
        if cfg.load_api_key():
            model = cfg.get_model()
            self._llm_status_btn.setText(f"LLM: OpenAI · {model}")
            self._llm_status_btn.setToolTip(
                "OpenAI API key is configured. Click to open Settings."
            )
        else:
            self._llm_status_btn.setText("LLM: not configured")
            self._llm_status_btn.setToolTip(
                "No OpenAI API key set. Click to open Settings and paste one."
            )

    def _show_about(self) -> None:
        from . import __version__
        QMessageBox.about(
            self, "About JDA → Pine Converter",
            f"<h3>JDA → Pine Converter</h3>"
            f"<p>Version {__version__}</p>"
            f"<p>Desktop GUI for the v2 chunk-based converter. "
            f"See <code>Agent/v2/README.md</code> for the architecture.</p>",
        )

    # ─────────────────────────────────────────────────────────────────
    # Conversion
    # ─────────────────────────────────────────────────────────────────
    def run_conversion(self) -> None:
        if not self._source_rtf:
            return
        org = self._org_combo.currentData()
        api_key = cfg.load_api_key()

        # No key? Run deterministic-only and surface a status note. We
        # don't block the conversion — the mapper can still see what
        # patterns covered and discover which tokens need attention.
        self._llm_skipped_for_no_key = not bool(api_key)

        self._set_busy(True)
        self._converted_stack.setCurrentIndex(1)   # show the spinner page
        self._busy_page.set_caption(
            "Converting…" if api_key
            else "Converting (deterministic only — no API key configured)…"
        )
        self._busy_page.start()
        self._status_label.setText(
            "Converting…" if api_key
            else "Converting (deterministic only — no API key configured)…"
        )
        self._thread, self._worker = run_conversion_async(
            self._source_rtf, org, api_key,
            on_finished=self._on_conversion_finished,
            on_failed=self._on_conversion_failed,
            on_progress=lambda msg: self._status_label.setText(msg),
            model=cfg.get_model(),
            base_url=(cfg.get_base_url() or None),
        )

    def _on_conversion_finished(self, result) -> None:
        self._result = result
        self._converted_view.set_text(result.converted_rtf)
        self._busy_page.stop()
        self._converted_stack.setCurrentIndex(0)   # back to the viewer

        # Suggestions panel — the LLM-fallback segments only.
        from v2 import pipeline
        items = []
        for i, seg in enumerate(result.segments):
            if seg.provenance == pipeline.PROV_LLM:
                jda = " | ".join(t.unparse() for t in seg.source_jda_tokens)
                pine = " | ".join(t.unparse() for t in seg.pine_outputs)
                items.append((i, jda, pine, len(seg.issues)))
        self._suggestion_panel.populate(items)
        self._issues_panel.populate(result.issues)
        self._segments_panel.populate(result.segments)

        prov = result.by_provenance
        self._stats_label.setText(
            f"  {result.total_jda_tokens} JDA → {result.total_pine_tokens} Pine  •  "
            f"{prov[pipeline.PROV_PATTERN]} pattern  •  "
            f"{prov[pipeline.PROV_LLM]} LLM  •  "
            f"{prov[pipeline.PROV_UNMATCHED]} unmatched  •  "
            f"{len(result.issues)} issue{'s' if len(result.issues) != 1 else ''}"
        )
        errors = [i for i in result.issues if i.severity == "error"]
        if self._llm_skipped_for_no_key:
            self._status_label.setText(
                "Done — deterministic only. Open Edit → Settings… to set "
                "your OpenAI API key and enable LLM fallback."
            )
        elif errors:
            self._status_label.setText(
                f"Done with {len(errors)} validation error"
                f"{'s' if len(errors) != 1 else ''}."
            )
        else:
            self._status_label.setText("Done.")
        self._save_btn.setEnabled(True)
        self._action_save.setEnabled(True)
        self._set_busy(False)

    def _on_conversion_failed(self, message: str) -> None:
        self._busy_page.stop()
        self._converted_stack.setCurrentIndex(0)   # back to the viewer
        QMessageBox.critical(self, "Conversion failed", message)
        self._status_label.setText("Conversion failed.")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._convert_btn.setEnabled(not busy and bool(self._source_rtf))
        self._open_btn.setEnabled(not busy)

    # ─────────────────────────────────────────────────────────────────
    # Suggestion accept / reject
    # ─────────────────────────────────────────────────────────────────
    def _handle_accept(self, segment_index: int) -> None:
        from v2.engine import suggestion_store
        seg = self._result.segments[segment_index]
        org = self._result.org
        for src_tok, pine_tok in zip(seg.source_jda_tokens, seg.pine_outputs):
            suggestion_store.accept_suggestion(
                src_tok.unparse(),
                pine_tok.unparse(),
                org=org,
                source_template=(self._source_path.name if self._source_path else None),
                source_segment_index=segment_index,
            )
        self._status_label.setText(
            f"Accepted segment #{segment_index} as a verified pattern. "
            "Re-run Convert to see it match deterministically."
        )

    def _handle_reject(self, segment_index: int) -> None:
        from v2.engine import suggestion_store
        seg = self._result.segments[segment_index]
        org = self._result.org
        for src_tok, pine_tok in zip(seg.source_jda_tokens, seg.pine_outputs):
            suggestion_store.reject_suggestion(
                src_tok.unparse(),
                pine_tok.unparse(),
                org=org,
                source_template=(self._source_path.name if self._source_path else None),
                source_segment_index=segment_index,
            )
        self._status_label.setText(
            f"Rejected segment #{segment_index} (logged for audit)."
        )

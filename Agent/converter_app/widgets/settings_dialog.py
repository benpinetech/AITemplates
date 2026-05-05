"""Settings dialog — one place to configure OpenAI access.

Three fields:

  - **OpenAI API key** (stored in OS keychain via ``keyring``)
  - **Model** (stored in QSettings; default ``gpt-4o-mini``)
  - **Base URL** (stored in QSettings; blank = OpenAI default; set this
    to use an OpenAI-compatible endpoint like Anyscale, Together, or
    a local proxy)

Open with menu **JDA Pine Converter → Settings…** (macOS, system-wide
shortcut Cmd+,) or **Edit → Settings…** (other platforms).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import settings as cfg


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(540)

        # Title row.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 22)
        outer.setSpacing(18)

        title = QLabel("OpenAI Access")
        title.setObjectName("settingsHeader")
        outer.addWidget(title)

        form = QFormLayout()
        # Generous vertical spacing so 38px-tall fields have visual
        # breathing room between rows. Horizontal spacing (label ↔ field)
        # is set separately and stays tight.
        form.setVerticalSpacing(16)
        form.setHorizontalSpacing(14)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)

        # API key row with a Show / Hide toggle.
        existing_key = cfg.load_api_key() or ""
        self._key_edit = QLineEdit(existing_key)
        self._key_edit.setEchoMode(QLineEdit.Password)
        self._key_edit.setPlaceholderText("sk-…")

        self._reveal_btn = QPushButton("Show")
        self._reveal_btn.setObjectName("secondaryButton")
        self._reveal_btn.setCheckable(True)
        self._reveal_btn.setFixedWidth(72)
        self._reveal_btn.toggled.connect(self._toggle_reveal)

        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        key_layout.addWidget(self._key_edit, stretch=1)
        key_layout.addWidget(self._reveal_btn)
        form.addRow("API key", key_row)

        # Model.
        self._model_edit = QLineEdit(cfg.get_model())
        self._model_edit.setPlaceholderText(cfg.DEFAULT_MODEL)
        form.addRow("Model", self._model_edit)
        form.itemAt(form.rowCount() - 1, QFormLayout.FieldRole).widget().setToolTip(
            f"Default: {cfg.DEFAULT_MODEL}. Examples: gpt-4o, gpt-4-turbo."
        )

        # Base URL.
        self._base_edit = QLineEdit(cfg.get_base_url())
        self._base_edit.setPlaceholderText("https://api.openai.com/v1  (default)")
        form.addRow("Base URL", self._base_edit)
        form.itemAt(form.rowCount() - 1, QFormLayout.FieldRole).widget().setToolTip(
            "Leave blank for OpenAI. Set this to use an OpenAI-API-compatible "
            "endpoint like Anyscale, Together, or a local proxy."
        )

        outer.addLayout(form)

        # Footer with Clear key + Cancel / Save.
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self._clear_btn = QPushButton("Clear stored key")
        self._clear_btn.setObjectName("secondaryButton")
        self._clear_btn.clicked.connect(self._clear_key)
        if not existing_key:
            self._clear_btn.setEnabled(False)
        footer.addWidget(self._clear_btn)
        footer.addStretch()

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        save_btn = self._buttons.button(QDialogButtonBox.Save)
        save_btn.setObjectName("primaryButton")
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        footer.addWidget(self._buttons)

        outer.addLayout(footer)

    # ── actions ─────────────────────────────────────────────────────────
    def _toggle_reveal(self, checked: bool) -> None:
        self._key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._reveal_btn.setText("Hide" if checked else "Show")

    def _clear_key(self) -> None:
        cfg.clear_api_key()
        self._key_edit.setText("")
        self._clear_btn.setEnabled(False)

    def _save(self) -> None:
        key = self._key_edit.text().strip()
        if key:
            cfg.save_api_key(key)
        else:
            cfg.clear_api_key()
        cfg.set_model(self._model_edit.text())
        cfg.set_base_url(self._base_edit.text())
        self.accept()


def open_settings_dialog(parent=None) -> bool:
    """Show the modal dialog. Returns True if the user clicked Save."""
    dialog = SettingsDialog(parent)
    return dialog.exec() == QDialog.Accepted

"""Per-segment provenance table — which pattern matched what."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)


# Map provenance string → display label + colour role for QSS.
_PROV_DISPLAY = {
    "pattern": ("pattern", "provPattern"),
    "llm-fallback": ("LLM", "provLlm"),
    "unmatched": ("unmatched", "provUnmatched"),
}


class SegmentsPanel(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segmentsTable")
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["#", "Provenance", "JDA source", "Pine output"])
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)

    def populate(self, segments: Sequence) -> None:
        self.setRowCount(len(segments))
        for row, seg in enumerate(segments):
            label, _role = _PROV_DISPLAY.get(seg.provenance, (seg.provenance, ""))
            if seg.pattern is not None:
                label = f"{label}: {seg.pattern.id}"
            jda = " | ".join(t.unparse() for t in seg.source_jda_tokens)
            pine = " | ".join(t.unparse() for t in seg.pine_outputs) or "—"

            idx_item = QTableWidgetItem(str(row))
            idx_item.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, 0, idx_item)

            prov_item = QTableWidgetItem(label)
            prov_item.setData(Qt.UserRole, seg.provenance)
            self.setItem(row, 1, prov_item)

            self.setItem(row, 2, QTableWidgetItem(jda))
            self.setItem(row, 3, QTableWidgetItem(pine))

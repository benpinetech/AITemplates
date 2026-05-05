"""Validation issues panel — list of errors and warnings from the
validator, color-coded by severity."""

from __future__ import annotations

from typing import List, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class IssuesPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(24, 22, 24, 22)
        self._layout.setSpacing(10)
        self._layout.addStretch()
        self.setWidget(self._inner)
        self._items: List[QWidget] = []
        self._empty: QLabel | None = None

    def populate(self, issues: Sequence) -> None:
        for w in self._items:
            w.deleteLater()
        self._items.clear()
        if self._empty is not None:
            self._empty.deleteLater()
            self._empty = None

        if not issues:
            self._empty = QLabel("No validation issues.")
            self._empty.setObjectName("emptyState")
            self._empty.setAlignment(Qt.AlignCenter)
            self._layout.insertWidget(0, self._empty)
            return

        for issue in issues:
            row = QFrame()
            row.setObjectName(
                "issueRowError" if issue.severity == "error" else "issueRowWarn"
            )
            v = QVBoxLayout(row)
            v.setContentsMargins(16, 14, 16, 14)
            v.setSpacing(4)

            severity = issue.severity.upper()
            idx = (
                f" @token #{issue.token_index}"
                if issue.token_index is not None else ""
            )
            head = QLabel(f"[{severity}] {issue.rule_id}{idx}")
            head.setObjectName(
                "issueHeadError" if issue.severity == "error" else "issueHeadWarn"
            )
            v.addWidget(head)

            body = QLabel(issue.message)
            body.setWordWrap(True)
            body.setObjectName("issueBody")
            v.addWidget(body)

            if issue.token_text:
                tok = QLabel(issue.token_text)
                tok.setObjectName("issueTokenText")
                tok.setWordWrap(True)
                tok.setTextInteractionFlags(Qt.TextSelectableByMouse)
                v.addWidget(tok)

            self._layout.insertWidget(self._layout.count() - 1, row)
            self._items.append(row)

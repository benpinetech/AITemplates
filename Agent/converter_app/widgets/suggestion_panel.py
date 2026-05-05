"""LLM-suggestion review panel.

Shows each LLM-fallback segment as a card with the source JDA and
proposed Pine, plus Accept / Reject buttons that call into
``v2.engine.suggestion_store``. After a decision, the buttons swap
out for a status badge.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def _apply_card_shadow(widget: QWidget) -> None:
    """Subtle elevation effect — looks like a small offset shadow
    with a soft blur. Same shape Stripe / Linear cards use; reads as
    'lifted' without being heavy."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(20)
    shadow.setOffset(0, 2)
    shadow.setColor(QColor(15, 23, 42, 26))   # slate-900 at ~10% alpha
    widget.setGraphicsEffect(shadow)


class _SuggestionCard(QFrame):
    """One LLM suggestion. Self-contained: knows its segment index,
    holds its own buttons, emits signals when accepted or rejected."""

    accepted = Signal(int)
    rejected = Signal(int)

    def __init__(
        self,
        segment_index: int,
        jda_text: str,
        pine_text: str,
        issue_count: int,
        parent=None,
    ):
        super().__init__(parent)
        self._segment_index = segment_index
        self.setObjectName("suggestionCard")
        self.setFrameShape(QFrame.NoFrame)
        _apply_card_shadow(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        # Header row.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        idx_label = QLabel(f"#{segment_index}")
        idx_label.setObjectName("suggestionIndex")
        header.addWidget(idx_label)
        header.addStretch()
        if issue_count:
            issue_label = QLabel(f"⚠ {issue_count} validator issue"
                                 f"{'s' if issue_count != 1 else ''}")
            issue_label.setObjectName("suggestionIssueWarn")
            header.addWidget(issue_label)
        outer.addLayout(header)

        # JDA / Pine pair.
        pair = QHBoxLayout()
        pair.setSpacing(12)

        jda_box = QFrame()
        jda_box.setObjectName("legacyChip")
        jbl = QVBoxLayout(jda_box)
        jbl.setContentsMargins(14, 12, 14, 14)
        jbl.setSpacing(6)
        jda_caption = QLabel("JDA")
        jda_caption.setObjectName("chipCaption")
        jda_value = QLabel(jda_text)
        jda_value.setObjectName("chipValue")
        jda_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        jda_value.setWordWrap(True)
        jbl.addWidget(jda_caption)
        jbl.addWidget(jda_value)

        arrow = QLabel("→")
        arrow.setObjectName("arrowGlyph")
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        pine_box = QFrame()
        pine_box.setObjectName("pineChip")
        pbl = QVBoxLayout(pine_box)
        pbl.setContentsMargins(14, 12, 14, 14)
        pbl.setSpacing(6)
        pine_caption = QLabel("Pine (LLM suggestion)")
        pine_caption.setObjectName("chipCaption")
        pine_value = QLabel(pine_text)
        pine_value.setObjectName("chipValue")
        pine_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pine_value.setWordWrap(True)
        pbl.addWidget(pine_caption)
        pbl.addWidget(pine_value)

        pair.addWidget(jda_box, stretch=1)
        pair.addWidget(arrow)
        pair.addWidget(pine_box, stretch=1)
        outer.addLayout(pair)

        # Action row.
        actions = QHBoxLayout()
        actions.addStretch()
        self._accept_btn = QPushButton("Accept")
        self._accept_btn.setObjectName("primaryButton")
        self._accept_btn.clicked.connect(
            lambda: self.accepted.emit(self._segment_index)
        )
        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setObjectName("secondaryButton")
        self._reject_btn.clicked.connect(
            lambda: self.rejected.emit(self._segment_index)
        )
        self._status_label = QLabel("")
        self._status_label.setObjectName("statusBadge")
        self._status_label.hide()

        actions.addWidget(self._accept_btn)
        actions.addWidget(self._reject_btn)
        actions.addWidget(self._status_label)
        outer.addLayout(actions)

    def mark_accepted(self) -> None:
        self._accept_btn.hide()
        self._reject_btn.hide()
        self._status_label.setText("✓ Accepted — saved as a verified pattern")
        self._status_label.setObjectName("statusAccepted")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.show()

    def mark_rejected(self) -> None:
        self._accept_btn.hide()
        self._reject_btn.hide()
        self._status_label.setText("✗ Rejected — logged for audit")
        self._status_label.setObjectName("statusRejected")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.show()


class SuggestionPanel(QScrollArea):
    """Scrollable list of suggestion cards. The host wires
    ``on_accept`` / ``on_reject`` callbacks to the suggestion store."""

    def __init__(
        self,
        on_accept: Callable[[int], None],
        on_reject: Callable[[int], None],
        parent=None,
    ):
        super().__init__(parent)
        self._on_accept = on_accept
        self._on_reject = on_reject

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        # Generous margins so the card drop-shadows aren't clipped at
        # the scroll area edges.
        self._layout.setContentsMargins(24, 22, 24, 22)
        self._layout.setSpacing(16)
        self._layout.addStretch()
        self.setWidget(self._inner)

        self._cards: List[_SuggestionCard] = []
        self._empty_label: Optional[QLabel] = None

    def populate(self, items: List[Tuple[int, str, str, int]]) -> None:
        """Replace the contents with the given list.

        Each item is ``(segment_index, jda_text, pine_text, issue_count)``.
        """
        # Clear previous cards + empty-state label.
        for c in self._cards:
            c.deleteLater()
        self._cards.clear()
        if self._empty_label is not None:
            self._empty_label.deleteLater()
            self._empty_label = None

        if not items:
            self._empty_label = QLabel(
                "No LLM suggestions for this run.\n\n"
                "Either every chunk matched a deterministic pattern, "
                "or the LLM fallback isn't enabled. Toggle it on in the "
                "toolbar and re-run to see suggestions for unmatched chunks."
            )
            self._empty_label.setObjectName("emptyState")
            self._empty_label.setAlignment(Qt.AlignCenter)
            self._empty_label.setWordWrap(True)
            # Insert above the stretch.
            self._layout.insertWidget(0, self._empty_label)
            return

        for idx, jda, pine, issues in items:
            card = _SuggestionCard(idx, jda, pine, issues)
            card.accepted.connect(self._handle_accept)
            card.rejected.connect(self._handle_reject)
            # Insert above the trailing stretch.
            self._layout.insertWidget(self._layout.count() - 1, card)
            self._cards.append(card)

    def _card_for(self, segment_index: int) -> Optional[_SuggestionCard]:
        for c in self._cards:
            if c._segment_index == segment_index:
                return c
        return None

    def _handle_accept(self, segment_index: int) -> None:
        self._on_accept(segment_index)
        card = self._card_for(segment_index)
        if card is not None:
            card.mark_accepted()

    def _handle_reject(self, segment_index: int) -> None:
        self._on_reject(segment_index)
        card = self._card_for(segment_index)
        if card is not None:
            card.mark_rejected()

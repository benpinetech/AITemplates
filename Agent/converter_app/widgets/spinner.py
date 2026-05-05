"""Indeterminate-progress spinner widget.

A small custom widget that paints 8 fading dots in a ring and rotates
them by 45° each tick. Lighter than embedding a QMovie GIF and
avoids shipping an asset; the colour matches the app's indigo accent.

Pair with :class:`BusyPage` for a labelled overlay (spinner + caption).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


_DOT_COUNT = 8
_TICK_MS = 80          # ~12.5 fps — gentle, low-CPU
_ANGLE_STEP = 360 // _DOT_COUNT


class Spinner(QWidget):
    """A circular spinner. Call ``start()`` to animate, ``stop()`` to
    freeze. Hidden visibility automatically pauses the timer to
    avoid burning CPU when off-screen."""

    def __init__(
        self,
        parent: QWidget | None = None,
        diameter: int = 36,
        dot_radius: int = 4,
        color: QColor = QColor(99, 102, 241),   # indigo-500
    ):
        super().__init__(parent)
        self._step = 0
        self._diameter = diameter
        self._dot_radius = dot_radius
        self._color = QColor(color)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(diameter, diameter)

    # ── lifecycle ───────────────────────────────────────────────────
    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(_TICK_MS)

    def stop(self) -> None:
        self._timer.stop()
        self._step = 0
        self.update()

    def hideEvent(self, event):    # auto-pause to save cycles
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):    # resume when shown
        self.start()
        super().showEvent(event)

    # ── painting ────────────────────────────────────────────────────
    def _tick(self) -> None:
        self._step = (self._step + 1) % _DOT_COUNT
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        cx = self.width() / 2
        cy = self.height() / 2
        ring_radius = (min(self.width(), self.height()) - self._dot_radius * 2) / 2

        for i in range(_DOT_COUNT):
            # Dots fade from full opacity at the "head" position to ~15%
            # at the tail; head moves +1 dot every tick.
            distance = (i - self._step) % _DOT_COUNT
            opacity = max(0.15, 1.0 - distance / _DOT_COUNT)
            c = QColor(self._color)
            c.setAlphaF(opacity)
            painter.setBrush(c)

            angle_rad = math.radians(i * _ANGLE_STEP - 90)   # start at 12 o'clock
            x = cx + ring_radius * math.cos(angle_rad)
            y = cy + ring_radius * math.sin(angle_rad)
            painter.drawEllipse(QPointF(x, y), self._dot_radius, self._dot_radius)


class BusyPage(QWidget):
    """A spinner with a caption underneath, centred. Drop this into a
    QStackedWidget alongside the real content widget; show it while
    work is in flight, hide it when results arrive."""

    def __init__(self, caption: str = "Converting…", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("busyPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addStretch()

        self._spinner = Spinner(self)
        # Wrap in a centring HBox so the spinner is horizontally centred.
        from PySide6.QtWidgets import QHBoxLayout
        spinner_row = QHBoxLayout()
        spinner_row.addStretch()
        spinner_row.addWidget(self._spinner)
        spinner_row.addStretch()
        layout.addLayout(spinner_row)

        self._caption = QLabel(caption)
        self._caption.setObjectName("busyCaption")
        self._caption.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._caption)

        layout.addStretch()

    def set_caption(self, text: str) -> None:
        self._caption.setText(text)

    def start(self) -> None:
        self._spinner.start()

    def stop(self) -> None:
        self._spinner.stop()

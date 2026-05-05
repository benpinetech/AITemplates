"""Read-only RTF / text viewer with bracketed-expression highlights.

The viewer accepts raw RTF (with ``\\rtf1\\ansi…`` control codes), strips
the codes via ``striprtf``, and shows the resulting plain text with
``%[...]`` and ``@[...]`` expressions highlighted. The mapper sees
readable prose without the noise of RTF markup.

Full Word-style rendering (fonts, italics, page layout) would need a
heavier solution like the dotnet sidecar the eval app uses or a
webview embedding rtf.js — out of scope here. For the converter
workflow, plain-text-with-highlights is what the mapper needs.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextOption,
)
from PySide6.QtWidgets import QPlainTextEdit

try:
    from striprtf.striprtf import rtf_to_text
except ImportError:    # pragma: no cover - the v1 stack already pulls this in
    rtf_to_text = None


# Colour palette — keep in sync with resources/styles.qss.
_LEGACY_BG = "#fde8d3"   # warm orange tint for %[ ... ]
_LEGACY_FG = "#7a3a00"
_PINE_BG   = "#dcebff"   # cool blue tint for @[ ... ]
_PINE_FG   = "#0a3a7a"


class _BracketHighlighter(QSyntaxHighlighter):
    """Highlights ``%[...]`` (legacy) and ``@[...]`` (pine) inline."""

    LEGACY_RE = re.compile(r"%\[[^\]]*\]")
    PINE_RE = re.compile(r"@\[[^\]]*\]")

    def __init__(self, document):
        super().__init__(document)
        self._legacy_fmt = QTextCharFormat()
        self._legacy_fmt.setBackground(QColor(_LEGACY_BG))
        self._legacy_fmt.setForeground(QColor(_LEGACY_FG))
        self._legacy_fmt.setFontWeight(QFont.DemiBold)

        self._pine_fmt = QTextCharFormat()
        self._pine_fmt.setBackground(QColor(_PINE_BG))
        self._pine_fmt.setForeground(QColor(_PINE_FG))
        self._pine_fmt.setFontWeight(QFont.DemiBold)

    def highlightBlock(self, text: str) -> None:
        for m in self.LEGACY_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._legacy_fmt)
        for m in self.PINE_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._pine_fmt)


class RtfViewer(QPlainTextEdit):
    """Drop-in viewer for RTF (or plain-text-with-brackets) content.

    The widget is read-only by default. Subclasses or callers can
    toggle ``setReadOnly(False)`` if direct editing is ever needed.
    """

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Wrap long lines to the viewer width so the horizontal scroll
        # bar doesn't appear. Vertical scroll still works for tall content.
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setPlaceholderText(placeholder)

        # Pleasant monospaced font.
        font = QFont("Menlo, Consolas, Monaco, monospace")
        font.setPointSize(11)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)

        # Live syntax highlighter on this widget's document.
        self._highlighter = _BracketHighlighter(self.document())

    def set_text(self, text: str) -> None:
        """Replace contents and rerun the highlighter.

        If the input looks like RTF, it's run through ``striprtf`` so
        the displayed text is readable prose rather than ``\\rtf1\\ansi``
        markup. The bracketed expressions survive the strip (they're
        ordinary characters, not RTF control words) so the
        highlighter still works.
        """
        displayed = self._render_for_display(text)
        self.setPlainText(displayed)

    @staticmethod
    def _render_for_display(text: str) -> str:
        if not text:
            return ""
        # Heuristic: only treat as RTF if it begins with the standard
        # opener. Anything else is shown as-is.
        stripped = text.lstrip("﻿").lstrip()
        if rtf_to_text is None or not stripped.startswith(r"{\rtf"):
            return text
        try:
            rendered = rtf_to_text(text)
        except Exception:    # noqa: BLE001 — striprtf is fussy on edge cases
            return text
        # Normalise excessive blank lines (striprtf can produce runs).
        return re.sub(r"\n{3,}", "\n\n", rendered)

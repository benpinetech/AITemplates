"""Entry point: instantiate QApplication, load the QSS theme,
show the main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

# Make ``v2`` importable when running from a checkout. The shipped
# build will package this differently.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("JDA Pine Converter")
    app.setOrganizationName("JDA-Pine")
    app.setOrganizationDomain("jda-pine.local")

    # Load the QSS theme.
    qss_path = Path(__file__).resolve().parent / "resources" / "styles.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    from .main_window import MainWindow
    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

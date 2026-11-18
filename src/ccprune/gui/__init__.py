"""The cc-prune window. Needs PySide6: pip install 'cc-prune[gui]'."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.exit("The window needs PySide6. Install it with: uv sync --extra gui "
                 "(or pip install 'cc-prune[gui]')")
    from .theme import apply_theme
    from .window import MainWindow

    args = list(sys.argv if argv is None else argv)
    app = QApplication(args)
    app.setApplicationName("cc-prune")
    apply_theme(app)
    window = MainWindow(root=args[1] if len(args) > 1 else None)
    window.show()
    sys.exit(app.exec())

"""A dark Fusion theme."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QToolTip

BG = "#1e2024"
PANEL = "#26292e"
FIELD = "#17191c"
TEXT = "#d8dbe0"
MUTED = "#8b919b"
ACCENT = "#4f9cf0"
GOOD = "#58c27d"
WARN = "#e0a44a"
BAD = "#e5605e"
HOVER = "#353a42"
SELECTED = "#2a3f58"


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: FIELD, QPalette.ColorRole.AlternateBase: PANEL,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: PANEL,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.ToolTipBase: PANEL,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.Highlight: SELECTED,
        QPalette.ColorRole.HighlightedText: TEXT, QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5d626b"))
    app.setPalette(pal)
    tip = QPalette(pal)
    tip.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL))
    tip.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    tip.setColor(QPalette.ColorRole.Window, QColor(PANEL))
    tip.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    QToolTip.setPalette(tip)
    app.setStyleSheet(f"""
        QGroupBox {{ border: 1px solid #343840; border-radius: 6px; margin-top: 10px;
                     padding: 8px 6px 6px 6px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px;
                            color: {MUTED}; }}
        QPushButton {{ padding: 5px 12px; border: 1px solid #3a3f47; border-radius: 5px;
                       background: {PANEL}; }}
        QPushButton:hover {{ border-color: {ACCENT}; }}
        QPushButton:disabled {{ color: #5d626b; }}
        QPushButton#apply {{ background: {ACCENT}; color: white; font-weight: 600;
                             padding: 8px 12px; border: none; }}
        QPushButton#apply:disabled {{ background: #2c3a4b; color: #6f7d90; }}
        QPushButton#armed {{ background: {WARN}; color: #1b1b1b; font-weight: 600;
                             padding: 8px 12px; border: none; }}
        QTableView, QPlainTextEdit, QTextBrowser, QLineEdit {{ border: 1px solid #343840;
                                                               border-radius: 4px; }}
        QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none;
                                padding: 4px 6px; }}
        QLabel#muted {{ color: {MUTED}; }}
        QLabel#big {{ font-size: 26px; font-weight: 600; }}
        QToolTip {{ color: {TEXT}; background-color: {PANEL}; border: 1px solid #4a505a;
                    padding: 4px; }}
        QCheckBox::indicator, QGroupBox::indicator {{ width: 14px; height: 14px;
            border: 1px solid #5a606a; border-radius: 3px; background: {FIELD}; }}
        QCheckBox::indicator:checked, QGroupBox::indicator:checked {{
            background: {ACCENT}; border-color: {ACCENT}; }}
        QCheckBox::indicator:disabled, QGroupBox::indicator:disabled {{ border-color: #3a3f47; }}
        QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid #5a606a;
            border-radius: 8px; background: {FIELD}; }}
        QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
        QRadioButton::indicator:disabled {{ border-color: #3a3f47; }}
        QHeaderView::section {{ text-align: left; }}

    """)

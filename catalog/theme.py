"""Светлая тема Qt Widgets и общие размеры интерфейса."""

from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QFrame, QVBoxLayout

WINDOW = "#F7F8FA"
SURFACE = "#FFFFFF"
LINE = "#E5E7EB"
TEXT = "#1F2937"
SECONDARY = "#667085"
HEADER = "#F8FAFC"
GROUP = "#F1F5F9"
HOVER = "#F3F7FC"
SELECTION = "#E4F0FF"
FOCUS = "#2684E8"
DISABLED = "#F1F3F6"
DISABLED_TEXT = "#828D9A"

OUTER_MARGIN = 12
CONTROL_GAP = 8
PANEL_GAP = 10
PANEL_INSET = 10
ROW_HEIGHT = 28

STYLESHEET = f"""
QMainWindow, QDialog {{
    background: {WINDOW};
}}
QFrame#catalogPanel {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 12px;
}}
QPushButton, QLineEdit {{
    background: {SURFACE};
    border: 1px solid #D5DBE3;
    border-radius: 9px;
    color: {TEXT};
    min-height: 20px;
    padding: 6px 12px;
}}
QPushButton {{
    font-weight: 600;
}}
QPushButton:hover {{
    background: {HOVER};
    border-color: #AEBCCC;
}}
QPushButton:pressed {{
    background: {SELECTION};
}}
QPushButton[primaryAction="true"] {{
    background: #EAF3FF;
    border-color: #CBDFF8;
    color: #215487;
}}
QPushButton[primaryAction="true"]:hover {{
    background: #DFEEFF;
}}
QPushButton[primaryAction="true"]:pressed {{
    background: #D2E7FF;
}}
QPushButton:focus, QPushButton[primaryAction="true"]:focus, QLineEdit:focus {{
    border-color: {FOCUS};
}}
QPushButton:disabled {{
    background: {DISABLED};
    border-color: {LINE};
    color: {DISABLED_TEXT};
}}
QLineEdit {{
    placeholder-text-color: {SECONDARY};
}}
QListView, QTreeView, QTableView {{
    background-color: {SURFACE};
    border: none;
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
}}
QListView::item {{
    padding: 0 9px;
    border-radius: 6px;
}}
QTreeView::item {{
    min-height: {ROW_HEIGHT}px;
    padding: 0 4px;
}}
QListView::item:hover:!selected, QTreeView::item:hover:!selected, QTableView::item:hover:!selected {{
    background-color: {HOVER};
}}
QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {{
    background-color: {SELECTION};
    color: {TEXT};
}}
QHeaderView {{
    background: {HEADER};
    border: none;
}}
QHeaderView::section {{
    background-color: {HEADER};
    border: none;
    border-right: 1px solid #EFF2F6;
    border-bottom: 1px solid {LINE};
    color: {SECONDARY};
    font-weight: 600;
    padding: 6px 7px;
}}
QHeaderView::section:hover {{
    background-color: {GROUP};
}}
QSplitter::handle {{
    background: {WINDOW};
}}
QSplitter::handle:hover {{
    background: #E7EEF7;
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {SURFACE};
    border: none;
    margin: 0;
}}
QScrollBar:vertical {{ width: 12px; }}
QScrollBar:horizontal {{ height: 12px; }}
QScrollBar::handle {{
    background: #C6CFDB;
    border: 2px solid {SURFACE};
    border-radius: 6px;
    min-width: 24px;
    min-height: 24px;
}}
QScrollBar::handle:hover {{ background: #A9B6C6; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: {SURFACE}; }}
QMenu {{
    background: {SURFACE};
    border: 1px solid #D5DBE3;
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 10px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {SELECTION};
    color: {TEXT};
}}
QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid #D5DBE3;
    padding: 3px;
}}
"""


def apply_theme(app):
    app.setStyle("Fusion")
    font = app.font()
    if QFontDatabase.hasFamily("Segoe UI"):
        font.setFamily("Segoe UI")
    font.setPointSizeF(10)
    app.setFont(font)

    palette = app.palette()
    for role, color in {
        QPalette.ColorRole.Window: WINDOW,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: GROUP,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: SELECTION,
        QPalette.ColorRole.HighlightedText: TEXT,
        QPalette.ColorRole.PlaceholderText: SECONDARY,
    }.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED_TEXT))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)


def panel(view):
    frame = QFrame()
    frame.setObjectName("catalogPanel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(PANEL_INSET, PANEL_INSET, PANEL_INSET, PANEL_INSET)
    layout.addWidget(view)
    return frame

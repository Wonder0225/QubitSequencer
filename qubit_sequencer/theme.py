"""Shared light and dark colors for the desktop interface and chip maps."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette


UI_COLORS = {
    "light": {
        "window": "#F5F8FB", "panel": "#FFFFFF", "toolbar": "#EAF1F6",
        "text": "#172A3D", "muted": "#5F7385", "border": "#CBDCE7",
        "hover": "#D7EBF3", "selected": "#CDE4F0", "accent": "#17678D",
        "grid": "#E3EBF1", "alternate": "#F3F8FB",
    },
    "dark": {
        "window": "#121A24", "panel": "#1B2633", "toolbar": "#202F3D",
        "text": "#E7F0F6", "muted": "#A4B7C6", "border": "#3A5062",
        "hover": "#304B60", "selected": "#31546A", "accent": "#82D2E6",
        "grid": "#324657", "alternate": "#233240",
    },
}

DIAGRAM_COLORS = {
    "light": {
        "ink": "#172A3D", "muted": "#6C7D8E", "paper": "#F7FAFC",
        "data": "#E5AD48", "x_check": "#3980AF", "z_check": "#70B4CE",
        "coupler": "#39A88E", "direct": "#577A9A", "missing": "#C7D1DA",
        "qubit": "#648DAF", "dot_major": "#D3DFE8", "dot_minor": "#DFE7ED",
        "halo": "#E7A944", "heat_low": "#315C9A", "heat_mid": "#4FB6AE",
        "heat_high": "#F0B24F",
        "categories": ["#397EAE", "#D99538", "#39A88E", "#9866A9", "#C15F73", "#8C993F"],
    },
    "dark": {
        "ink": "#E7F0F6", "muted": "#A4B7C6", "paper": "#131F2B",
        "data": "#E7AD47", "x_check": "#408AB9", "z_check": "#74B9D1",
        "coupler": "#45BAA2", "direct": "#80A8CF", "missing": "#506172",
        "qubit": "#79A7CC", "dot_major": "#314555", "dot_minor": "#283A49",
        "halo": "#FFD06E", "heat_low": "#4269AD", "heat_mid": "#45ADAD",
        "heat_high": "#E6A845",
        "categories": ["#5B9BC9", "#E6A845", "#47B9A2", "#B18BCB", "#D27891", "#A9BA64"],
    },
}


def palette(mode: str) -> QPalette:
    colors = UI_COLORS[mode]
    result = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "window"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "panel"),
        (QPalette.ColorRole.AlternateBase, "alternate"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "toolbar"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.Highlight, "selected"),
        (QPalette.ColorRole.ToolTipBase, "panel"),
        (QPalette.ColorRole.ToolTipText, "text"),
    ):
        result.setColor(role, QColor(colors[key]))
    result.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["text"]))
    result.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                    QColor(colors["muted"]))
    result.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                    QColor(colors["muted"]))
    return result


def stylesheet(mode: str) -> str:
    c = UI_COLORS[mode]
    return f"""
        QMainWindow, QDialog, QWidget {{ background: {c['window']}; color: {c['text']}; }}
        QMenuBar, QMenu, QStatusBar {{ background: {c['toolbar']}; color: {c['text']}; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background: {c['hover']}; }}
        QToolBar {{ background: {c['toolbar']}; border: 0; spacing: 8px; padding: 6px; }}
        QToolBar QToolButton {{ padding: 7px 11px; border-radius: 6px; }}
        QToolBar QToolButton:hover {{ background: {c['hover']}; }}
        QToolBar QToolButton:checked {{ background: {c['selected']}; color: {c['accent']}; font-weight: bold; }}
        QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['panel']}; }}
        QTabBar::tab {{ padding: 8px 15px; background: {c['toolbar']}; }}
        QTabBar::tab:selected {{ background: {c['panel']}; border-top: 3px solid {c['accent']}; }}
        QPushButton {{ background: {c['toolbar']}; color: {c['text']}; border: 1px solid {c['border']};
                       border-radius: 5px; padding: 6px 10px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {c['panel']};
            color: {c['text']}; selection-background-color: {c['selected']};
            border: 1px solid {c['border']}; border-radius: 4px; padding: 4px; }}
        QComboBox QAbstractItemView {{ background: {c['panel']}; color: {c['text']};
            selection-background-color: {c['selected']}; }}
        QTableWidget {{ background: {c['panel']}; color: {c['text']};
            alternate-background-color: {c['alternate']}; gridline-color: {c['grid']};
            selection-background-color: {c['selected']}; selection-color: {c['text']}; }}
        QHeaderView::section {{ background: {c['toolbar']}; color: {c['text']}; padding: 6px; border: 0;
            border-bottom: 1px solid {c['border']}; font-weight: bold; }}
        QTableCornerButton::section {{ background: {c['toolbar']}; border: 0; }}
        QGraphicsView {{ background: {c['panel']}; border: 1px solid {c['border']}; }}
        QSplitter::handle {{ background: {c['border']}; }}
        QScrollBar:vertical, QScrollBar:horizontal {{ background: {c['window']}; }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {c['border']};
            border-radius: 4px; min-height: 18px; min-width: 18px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
        QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; }}
        QLabel#popupTitle {{ font-size: 15px; font-weight: bold; color: {c['accent']}; }}
    """

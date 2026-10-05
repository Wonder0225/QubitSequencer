"""Shared colors and compact workbench styles for the desktop interface."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette


UI_COLORS = {
    "light": {
        "window": "#F5F6F8", "panel": "#FFFFFF", "toolbar": "#F0F2F5",
        "text": "#20252E", "muted": "#606B78", "border": "#D7DDE5",
        "hover": "#EBEFF5", "selected": "#E8F0FF", "accent": "#2563EB",
        "grid": "#E8ECF1", "alternate": "#F8F9FB",
        "pressed": "#E1E7F0", "focus": "#2563EB", "selected_text": "#1D4ED8",
        "accent_hover": "#1D4ED8", "accent_pressed": "#1E40AF", "accent_text": "#FFFFFF",
        "disabled": "#EEF0F3", "disabled_text": "#9099A5", "disabled_border": "#E0E4EA",
        "danger": "#B42332", "danger_hover": "#9B1C2B", "danger_pressed": "#821725",
        "danger_soft": "#FFF0F1", "success": "#18794E", "warning": "#9A6700",
        "shadow": "#C9D1DC",
    },
    "dark": {
        "window": "#15181E", "panel": "#1D222A", "toolbar": "#232A34",
        "text": "#E8EDF4", "muted": "#AAB5C3", "border": "#343D49",
        "hover": "#2C3542", "selected": "#273C5D", "accent": "#79A7FF",
        "grid": "#2B333E", "alternate": "#202630",
        "pressed": "#354052", "focus": "#79A7FF", "selected_text": "#A9C8FF",
        "accent_hover": "#93B8FF", "accent_pressed": "#6597EF", "accent_text": "#10213D",
        "disabled": "#242A33", "disabled_text": "#697585", "disabled_border": "#303844",
        "danger": "#FF8C98", "danger_hover": "#FFA6AF", "danger_pressed": "#EA7582",
        "danger_soft": "#40272F", "success": "#6ACB99", "warning": "#E5BC68",
        "shadow": "#0D1015",
    },
}

DIAGRAM_COLORS = {
    "light": {
        "ink": "#20252E", "muted": "#606B78", "paper": "#F8F9FB",
        "data": "#E5AD48", "x_check": "#3980AF", "z_check": "#70B4CE",
        "coupler": "#39A88E", "direct": "#577A9A", "missing": "#C7D1DA",
        "qubit": "#648DAF", "dot_major": "#D7DDE5", "dot_minor": "#E6EAF0",
        "halo": "#E7A944", "heat_low": "#315C9A", "heat_mid": "#4FB6AE",
        "heat_high": "#F0B24F",
        "categories": ["#397EAE", "#D99538", "#39A88E", "#9866A9", "#C15F73", "#8C993F"],
    },
    "dark": {
        "ink": "#E8EDF4", "muted": "#AAB5C3", "paper": "#171C23",
        "data": "#E7AD47", "x_check": "#408AB9", "z_check": "#74B9D1",
        "coupler": "#45BAA2", "direct": "#80A8CF", "missing": "#506172",
        "qubit": "#79A7CC", "dot_major": "#343D49", "dot_minor": "#282F39",
        "halo": "#FFD06E", "heat_low": "#4269AD", "heat_mid": "#45ADAD",
        "heat_high": "#E6A845",
        "categories": ["#5B9BC9", "#E6A845", "#47B9A2", "#B18BCB", "#D27891", "#A9BA64"],
    },
}


def palette(mode: str) -> QPalette:
    """Supply native widgets with matching active, inactive and disabled colors."""
    c = UI_COLORS[mode]
    result = QPalette()
    roles = {
        QPalette.ColorRole.Window: "window",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "panel",
        QPalette.ColorRole.AlternateBase: "alternate",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "toolbar",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.BrightText: "accent_text",
        QPalette.ColorRole.Highlight: "selected",
        QPalette.ColorRole.HighlightedText: "selected_text",
        QPalette.ColorRole.ToolTipBase: "panel",
        QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.Link: "accent",
        QPalette.ColorRole.LinkVisited: "accent_pressed",
        QPalette.ColorRole.Light: "panel",
        QPalette.ColorRole.Midlight: "hover",
        QPalette.ColorRole.Mid: "border",
        QPalette.ColorRole.Dark: "border",
        QPalette.ColorRole.Shadow: "shadow",
        QPalette.ColorRole.PlaceholderText: "muted",
    }
    roles[QPalette.ColorRole.Accent] = "accent"
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, key in roles.items():
            result.setColor(group, role, QColor(c[key]))
    disabled_roles = dict(roles)
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText, QPalette.ColorRole.ToolTipText,
        QPalette.ColorRole.HighlightedText, QPalette.ColorRole.PlaceholderText,
        QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited, QPalette.ColorRole.Accent,
    ):
        disabled_roles[role] = "disabled_text"
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Button, QPalette.ColorRole.Highlight):
        disabled_roles[role] = "disabled"
    for role in (QPalette.ColorRole.Mid, QPalette.ColorRole.Dark):
        disabled_roles[role] = "disabled_border"
    for role, key in disabled_roles.items():
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(c[key]))
    return result


def stylesheet(mode: str) -> str:
    """Compact, semantic widget styles; dimensions are logical Qt pixels."""
    c = UI_COLORS[mode]
    assets = Path(__file__).resolve().parent / "assets"
    down = (assets / f"chevron-down-{mode}.svg").as_posix()
    up = (assets / f"chevron-up-{mode}.svg").as_posix()
    return f"""
        QMainWindow, QDialog {{ background: {c['window']}; }}
        QWidget {{ color: {c['text']}; font-size: 14px; }}
        QWidget#projectBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; }}
        QWidget#navigation {{ background: {c['panel']}; border-right: 1px solid {c['border']}; }}
        QWidget#inspector {{ background: {c['panel']}; border-left: 1px solid {c['border']}; }}
        QWidget#canvasToolbar, QWidget#pageToolbar {{
            background: {c['panel']}; border-bottom: 1px solid {c['border']};
        }}
        QWidget#editorSurface {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; }}
        QLabel {{ background: transparent; border: 0; }}
        QLabel[role="heading"] {{ font-size: 16px; font-weight: 600; color: {c['text']}; }}
        QLabel[role="muted"] {{ color: {c['muted']}; }}
        QLabel[role="danger"] {{ color: {c['danger']}; }}
        QLabel[role="caption"] {{ font-size: 12px; color: {c['muted']}; }}
        QLabel#popupTitle {{ font-size: 16px; font-weight: 600; color: {c['text']}; }}
        QLabel:disabled, QLabel[role]:disabled {{ color: {c['disabled_text']}; }}

        QMenuBar, QMenu, QStatusBar {{ background: {c['panel']}; color: {c['text']}; }}
        QMenuBar {{ border-bottom: 1px solid {c['border']}; }}
        QMenuBar::item {{ padding: 5px 8px; background: transparent; }}
        QMenu {{ border: 1px solid {c['border']}; padding: 4px; }}
        QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 3px; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background: {c['hover']}; }}
        QMenu::item:disabled {{ color: {c['disabled_text']}; }}
        QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
        QStatusBar {{ border-top: 1px solid {c['border']}; font-size: 12px; }}
        QStatusBar::item {{ border: 0; }}
        QToolBar {{ background: {c['panel']}; border: 0; spacing: 4px; padding: 4px; }}
        QToolBar::separator {{ background: {c['border']}; width: 1px; margin: 4px; }}

        QPushButton, QToolButton {{
            background: {c['toolbar']}; color: {c['text']}; border: 1px solid {c['border']};
            border-radius: 6px; min-height: 20px; max-height: 20px; padding: 5px 10px;
        }}
        QToolButton {{ padding: 5px 7px; }}
        QPushButton[compact="true"] {{
            min-height: 18px; max-height: 18px; padding: 3px 8px; font-size: 13px;
        }}
        QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
        QPushButton:pressed, QToolButton:pressed {{ background: {c['pressed']}; }}
        QPushButton:checked, QToolButton:checked {{
            background: {c['selected']}; color: {c['selected_text']}; border-color: {c['border']};
        }}
        QPushButton:focus, QToolButton:focus {{ border-color: {c['focus']}; }}
        QPushButton[role="primary"], QToolButton[role="primary"] {{
            background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 600;
        }}
        QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover {{
            background: {c['accent_hover']}; border-color: {c['accent_hover']};
        }}
        QPushButton[role="primary"]:pressed, QToolButton[role="primary"]:pressed {{
            background: {c['accent_pressed']}; border-color: {c['accent_pressed']};
        }}
        QPushButton[role="primary"]:focus, QToolButton[role="primary"]:focus {{
            border-color: {c['text']};
        }}
        QPushButton[role="secondary"], QToolButton[role="secondary"] {{ background: {c['panel']}; }}
        QPushButton[role="secondary"]:hover, QToolButton[role="secondary"]:hover {{ background: {c['hover']}; }}
        QPushButton[role="secondary"]:pressed, QToolButton[role="secondary"]:pressed {{ background: {c['pressed']}; }}
        QPushButton[role="ghost"], QToolButton[role="ghost"] {{ background: transparent; border-color: transparent; }}
        QPushButton[role="ghost"]:hover, QToolButton[role="ghost"]:hover {{ background: {c['hover']}; }}
        QPushButton[role="ghost"]:pressed, QToolButton[role="ghost"]:pressed {{ background: {c['pressed']}; }}
        QPushButton[role="ghost"]:checked, QToolButton[role="ghost"]:checked {{ background: {c['selected']}; color: {c['selected_text']}; }}
        QPushButton[role="ghost"]:focus, QToolButton[role="ghost"]:focus {{ border-color: {c['focus']}; }}
        QPushButton[role="danger"], QToolButton[role="danger"] {{
            background: {c['panel']}; color: {c['danger']}; border-color: {c['border']};
        }}
        QPushButton[role="danger"]:hover, QToolButton[role="danger"]:hover {{ background: {c['danger_soft']}; color: {c['danger_hover']}; }}
        QPushButton[role="danger"]:pressed, QToolButton[role="danger"]:pressed {{ background: {c['danger_soft']}; color: {c['danger_pressed']}; }}
        QPushButton[role="danger"]:focus, QToolButton[role="danger"]:focus {{ border-color: {c['danger']}; }}
        QPushButton[navItem="true"] {{
            background: transparent; border: 1px solid transparent; text-align: left; padding: 6px 10px;
        }}
        QPushButton[navItem="true"]:hover {{ background: {c['hover']}; }}
        QPushButton[navItem="true"]:pressed {{ background: {c['pressed']}; }}
        QPushButton[navItem="true"]:checked {{ background: {c['selected']}; color: {c['selected_text']}; font-weight: 600; }}
        QPushButton[navItem="true"]:focus {{ border-color: {c['focus']}; }}
        QPushButton:disabled, QToolButton:disabled,
        QPushButton[role]:disabled, QToolButton[role]:disabled, QPushButton[navItem="true"]:disabled {{
            background: {c['disabled']}; color: {c['disabled_text']}; border-color: {c['disabled_border']};
        }}

        QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['panel']}; }}
        QTabBar::tab {{
            padding: 7px 12px; background: {c['toolbar']}; color: {c['muted']};
            border-bottom: 2px solid transparent;
        }}
        QTabBar::tab:hover {{ background: {c['hover']}; }}
        QTabBar::tab:selected {{ background: {c['panel']}; color: {c['text']}; border-bottom-color: {c['accent']}; }}
        QTabBar::tab:disabled {{ color: {c['disabled_text']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
            background: {c['panel']}; color: {c['text']}; selection-background-color: {c['selected']};
            selection-color: {c['selected_text']}; border: 1px solid {c['border']}; border-radius: 6px;
            padding: 4px 6px;
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 22px; }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {c['muted']}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {c['focus']}; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
        QTextEdit:disabled, QPlainTextEdit:disabled {{
            background: {c['disabled']}; color: {c['disabled_text']}; border-color: {c['disabled_border']};
        }}
        QComboBox::drop-down {{ border: 0; width: 22px; }}
        QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
        QSpinBox::up-button, QDoubleSpinBox::up-button,
        QSpinBox::down-button, QDoubleSpinBox::down-button {{ width: 18px; border: 0; }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
        QComboBox QAbstractItemView {{
            background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
            selection-background-color: {c['selected']}; selection-color: {c['selected_text']};
        }}
        QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}
        QCheckBox:disabled, QRadioButton:disabled {{ color: {c['disabled_text']}; }}
        QGroupBox {{ border: 1px solid {c['border']}; border-radius: 6px; margin-top: 10px; padding-top: 10px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['muted']}; }}

        QTableView, QTableWidget, QTreeView, QListView, QListWidget {{
            background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
            alternate-background-color: {c['alternate']}; gridline-color: {c['grid']};
            selection-background-color: {c['selected']}; selection-color: {c['selected_text']};
        }}
        QAbstractItemView::item:hover {{ background: {c['hover']}; }}
        QAbstractItemView::item:selected {{ background: {c['selected']}; color: {c['selected_text']}; }}
        QAbstractItemView:disabled {{ color: {c['disabled_text']}; }}
        QListWidget#navigation {{
            background: {c['panel']}; color: {c['muted']}; border: 0;
            border-right: 1px solid {c['border']}; padding: 6px 0; outline: 0;
        }}
        QListWidget#navigation::item {{
            border: 1px solid transparent; border-radius: 6px; padding: 6px 8px;
            margin: 0 5px; min-height: 20px;
        }}
        QListWidget#navigation::item:hover {{ background: {c['hover']}; color: {c['text']}; }}
        QListWidget#navigation::item:selected {{
            background: {c['selected']}; color: {c['selected_text']}; font-weight: 600;
        }}
        QListWidget#navigation::item:focus {{ border-color: {c['focus']}; }}
        QListWidget#navigation:focus {{ border-right-color: {c['focus']}; }}
        QListWidget#navigation::item:disabled {{ color: {c['disabled_text']}; }}
        QHeaderView::section {{
            background: {c['toolbar']}; color: {c['muted']}; padding: 6px 8px; border: 0;
            border-bottom: 1px solid {c['border']}; font-weight: 600; font-size: 13px;
        }}
        QTableCornerButton::section {{ background: {c['toolbar']}; border: 0; border-bottom: 1px solid {c['border']}; }}
        QGraphicsView {{ background: {c['panel']}; border: 1px solid {c['border']}; }}
        QSplitter::handle {{ background: {c['border']}; }}
        QSplitter::handle:hover {{ background: {c['accent']}; }}
        QScrollBar:vertical {{ background: {c['window']}; width: 10px; margin: 0; }}
        QScrollBar:horizontal {{ background: {c['window']}; height: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 24px; margin: 1px 2px; }}
        QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 24px; margin: 2px 1px; }}
        QScrollBar::handle:hover {{ background: {c['muted']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px 6px; }}
    """

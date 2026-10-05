"""Persistent recent chips and a searchable, keyboard-friendly file picker."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout,
)

from .ui_icons import icon


class RecentFiles:
    KEY = "files/recent"
    LIMIT = 20

    def __init__(self, settings):
        self.settings = settings

    def paths(self) -> list[str]:
        saved = self.settings.value(self.KEY, [])
        if isinstance(saved, str):
            saved = [saved]
        result, seen = [], set()
        for raw in saved or []:
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = str(Path(raw).expanduser().resolve())
            key = os.path.normcase(path)
            if key not in seen:
                seen.add(key); result.append(path)
        return result[:self.LIMIT]

    def record(self, path):
        canonical = str(Path(path).resolve())
        paths = [canonical] + [old for old in self.paths()
                               if os.path.normcase(old) != os.path.normcase(canonical)]
        self.settings.setValue(self.KEY, paths[:self.LIMIT])
        self.settings.sync()

    def clear(self):
        self.settings.remove(self.KEY)
        self.settings.sync()


class RecentFileDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(420, 56)

    def paint(self, painter, option, index):
        decorated = QStyleOptionViewItem(option)
        self.initStyleOption(decorated, index)
        decorated.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, decorated, painter, option.widget)
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        color = option.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        muted = option.palette.color(QPalette.ColorRole.PlaceholderText)
        left = option.rect.left()+12
        symbol = icon("chip", color.name())
        symbol.paint(painter, QRect(left, option.rect.top()+18, 20, 20))
        name_rect = QRect(left+32, option.rect.top()+5, max(20, option.rect.width()-56), 24)
        path_rect = QRect(left+32, option.rect.top()+29, max(20, option.rect.width()-56), 20)
        font = QFont(option.font); font.setWeight(QFont.Weight.Medium)
        painter.setFont(font); painter.setPen(color)
        path = Path(str(index.data(Qt.ItemDataRole.UserRole)))
        name = path.name + ("（文件已移动或删除）" if not path.is_file() else "")
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width()))
        font.setPixelSize(12); font.setWeight(QFont.Weight.Normal)
        painter.setFont(font); painter.setPen(muted)
        painter.drawText(path_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(str(path.parent), Qt.TextElideMode.ElideMiddle, path_rect.width()))
        painter.restore()


class RecentFilesDialog(QDialog):
    def __init__(self, recent: RecentFiles, parent=None):
        super().__init__(parent)
        self.recent = recent
        self.other_requested = False
        self.setWindowTitle("打开最近的文件")
        self.resize(700, 460)
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 16, 16, 16); layout.setSpacing(12)
        self.search = QLineEdit(); self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("输入文件名或路径筛选 · Enter 打开 · Esc 关闭")
        layout.addWidget(self.search)
        self.files = QListWidget(); self.files.setItemDelegate(RecentFileDelegate(self.files))
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.files, 1)
        self.empty = QLabel(); self.empty.setProperty("role", "muted"); layout.addWidget(self.empty)
        row = QHBoxLayout()
        self.clear_button = QPushButton("清除记录"); self.clear_button.setProperty("role", "ghost")
        self.clear_button.clicked.connect(self.clear_history); row.addWidget(self.clear_button)
        row.addStretch()
        other = QPushButton("打开其他文件…"); other.clicked.connect(self.open_other); row.addWidget(other)
        self.open_button = QPushButton("打开文件"); self.open_button.setProperty("role", "primary")
        self.open_button.clicked.connect(self.open_selection); self.open_button.setDefault(True); row.addWidget(self.open_button)
        layout.addLayout(row)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.open_selection)
        self.files.itemDoubleClicked.connect(self.open_selection)
        self.files.currentRowChanged.connect(self.selection_changed)
        self.refresh(); self.search.setFocus()

    def refresh(self, *_):
        selected = self.selected_path()
        needle = self.search.text().strip().casefold()
        self.files.clear()
        for path in self.recent.paths():
            if needle in path.casefold():
                item = QListWidgetItem(Path(path).name)
                item.setData(Qt.ItemDataRole.UserRole, path); item.setToolTip(path)
                self.files.addItem(item)
                if path == selected:
                    self.files.setCurrentItem(item)
        if self.files.currentRow() < 0 and self.files.count():
            self.files.setCurrentRow(0)
        self.empty.setText("" if self.files.count() else "未找到匹配文件" if needle else "暂无最近打开的文件")
        self.empty.setVisible(not self.files.count())
        self.clear_button.setEnabled(bool(self.recent.paths()))
        self.selection_changed()

    def selected_path(self):
        item = self.files.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selection_changed(self, *_):
        path = self.selected_path()
        self.open_button.setEnabled(bool(path and Path(path).is_file()))

    def open_selection(self, *_):
        if self.open_button.isEnabled():
            self.accept()

    def open_other(self):
        self.other_requested = True
        self.accept()

    def clear_history(self):
        self.recent.clear(); self.refresh()

"""Arrange the workbench's existing page widgets in resizable columns."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QSplitter,
    QToolButton, QVBoxLayout, QWidget,
)


class WorkspaceColumns(QWidget):
    currentChanged = Signal(int)
    layoutChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)
        self.pages = []
        self.columns = []
        self._order = []
        self._widths = {}
        self._current = -1
        self._arranging = False
        self.multicolumn = False
        self.splitter.splitterMoved.connect(self._remember_widths)
        QApplication.instance().focusChanged.connect(self._focus_changed)

    def addWidget(self, page, title):
        index = len(self.pages)
        self.pages.append(page)
        column = QWidget(); column.setMinimumWidth(124)
        layout = QVBoxLayout(column); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        header = QWidget(); header.setObjectName("workspaceColumnHeader")
        bar = QHBoxLayout(header); bar.setContentsMargins(4, 2, 4, 2); bar.setSpacing(1)
        activate = QToolButton(); activate.setText(title); activate.setToolTip(f"激活{title}")
        activate.setCheckable(True); activate.setProperty("role", "ghost")
        activate.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        activate.clicked.connect(lambda: self.setCurrentIndex(index))
        bar.addWidget(activate, 1)
        controls = []
        for text, tip, callback in (("‹", "向左移动此列", lambda: self.move_column(index, -1)),
                                    ("›", "向右移动此列", lambda: self.move_column(index, 1)),
                                    ("×", "关闭此列", lambda: self.close_column(index))):
            button = QToolButton(); button.setText(text); button.setToolTip(tip); button.setAccessibleName(tip)
            button.setProperty("role", "ghost"); button.setFixedWidth(24)
            button.clicked.connect(callback); bar.addWidget(button); controls.append(button)
        layout.addWidget(header)
        scroll = QScrollArea(); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page); layout.addWidget(scroll, 1)
        self.columns.append((column, header, activate, *controls))
        self.splitter.addWidget(column)
        column.hide()

    def count(self):
        return len(self.pages)

    def widget(self, index):
        return self.pages[index]

    def currentIndex(self):
        return self._current

    def currentWidget(self):
        return self.pages[self._current] if self._current >= 0 else None

    def setCurrentWidget(self, page):
        self.setCurrentIndex(self.pages.index(page))

    def visible_indices(self):
        return tuple(self._order) if self.multicolumn else ((self._current,) if self._current >= 0 else ())

    def is_page_visible(self, page):
        return self.pages.index(page) in self.visible_indices()

    def setCurrentIndex(self, index):
        if not 0 <= index < self.count():
            return
        focus_page = self._page_for(QApplication.focusWidget())
        changed = index != self._current
        self._current = index
        added = self.multicolumn and index not in self._order
        if added:
            self._order.append(index)
        if added or (changed and not self.multicolumn):
            self._arrange()
        if changed:
            self._update_headers()
            if self.multicolumn and focus_page is not None and focus_page != index:
                self.columns[index][2].setFocus(Qt.FocusReason.OtherFocusReason)
            self.currentChanged.emit(index)

    def set_multicolumn(self, enabled):
        if enabled == self.multicolumn:
            return
        previous = self._current
        self.multicolumn = enabled
        if enabled:
            if not self._order:
                self._order.append(previous)
            elif previous not in self._order:
                self._current = self._order[0]
        self._arrange()
        if previous != self._current:
            self.currentChanged.emit(self._current)

    def move_column(self, index, offset):
        position = self._order.index(index)
        other = position + offset
        if 0 <= other < len(self._order):
            self._order[position], self._order[other] = self._order[other], self._order[position]
            self._arrange()

    def close_column(self, index):
        if len(self._order) <= 1:
            return
        position = self._order.index(index)
        self._order.remove(index)
        if self._current == index:
            self.setCurrentIndex(self._order[min(position, len(self._order) - 1)])
        self._arrange()

    def layout_state(self):
        return list(self._order), [self._widths.get(index, 100) for index in self._order]

    def restore_layout(self, order, widths):
        self._order = list(dict.fromkeys(index for index in order if 0 <= index < self.count()))
        self._widths = {}
        for index, raw_width in zip(order, widths):
            try:
                width = int(raw_width)
            except (TypeError, ValueError):
                continue
            if index in self._order and width > 0:
                self._widths[index] = width

    def _remember_widths(self, *_):
        if self.multicolumn:
            sizes = self.splitter.sizes()
            self._widths.update((index, sizes[self.splitter.indexOf(self.columns[index][0])])
                                for index in self._order)

    def _arrange(self):
        self._arranging = True
        visible = self.visible_indices()
        for position, index in enumerate(visible):
            self.splitter.insertWidget(position, self.columns[index][0])
        for index, (column, header, *_) in enumerate(self.columns):
            header.setVisible(self.multicolumn)
            column.setVisible(index in visible)
        if self.multicolumn:
            default = sum(self._widths.values()) // len(self._widths) if self._widths else 100
            self.splitter.setSizes([self._widths.get(index, default) for index in visible]
                                   + [0] * (len(self.columns) - len(visible)))
        self._update_headers()
        self._arranging = False
        self.layoutChanged.emit()

    def _update_headers(self):
        for position, index in enumerate(self.visible_indices()):
            _, _, activate, left, right, close = self.columns[index]
            activate.setChecked(index == self._current)
            left.setEnabled(position > 0)
            right.setEnabled(position < len(self._order) - 1)
            close.setEnabled(len(self._order) > 1)

    def _page_for(self, widget):
        if widget is not None and widget.window() is self.window():
            return next((i for i in self.visible_indices() if self.columns[i][0].isAncestorOf(widget)), None)
        return None

    def _focus_changed(self, _old, widget):
        if self.multicolumn and not self._arranging:
            index = self._page_for(widget)
            if index is not None and index != self._current:
                self.setCurrentIndex(index)

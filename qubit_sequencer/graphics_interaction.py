"""Shared mouse navigation for selectable graphics canvases."""

from PySide6.QtCore import Qt


class RightPanMixin:
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._right_pan_pos = event.position().toPoint()
            self._right_pan_start = self._right_pan_pos
            self._right_pan_moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_right_pan_pos", None) is not None and event.buttons() & Qt.MouseButton.RightButton:
            pos = event.position().toPoint()
            if (pos - self._right_pan_start).manhattanLength() >= 4:
                self._right_pan_moved = True
            if self._right_pan_moved:
                delta = pos - self._right_pan_pos
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
                if hasattr(self, "_fit_active"): self._fit_active = False
                if hasattr(self, "fitted"): self.fitted = False
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            self._right_pan_pos = pos
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and getattr(self, "_right_pan_pos", None) is not None:
            self._right_pan_pos = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        if getattr(self, "_right_pan_moved", False):
            self._right_pan_moved = False
            event.accept()
            return
        super().contextMenuEvent(event)

"""Physical chip layout viewer with persistent device name anchors."""

from html import escape
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QGraphicsItem, QGraphicsObject,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .tex_labels import tex_to_html
from .device_types import DEVICE_NAMES, DEVICE_COLORS
from .attachment_ui import clipboard_payload
from .graphics_interaction import RightPanMixin
from .theme import custom_device_colors


IMAGE_FILTER = "设计图 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;所有文件 (*)"


def design_image(data):
    image = QImage.fromData(data)
    if image.isNull():
        raise ValueError("无法读取设计图，请选择有效的 PNG、JPEG、BMP、TIFF 或 WebP 图片")
    return image


def read_design_file(path):
    file = Path(path)
    data = file.read_bytes()
    design_image(data)
    return file.name, data


class DesignMarker(QGraphicsObject):
    activated = Signal(str, QPoint)
    moved = Signal(str, float, float)

    def __init__(self, row, image_item, editing, custom_colors):
        super().__init__(image_item)
        self.device_id = row["device_id"]
        self.kind = row["kind"]
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(1)
        self.document = QTextDocument()
        font = QFont(); font.setPixelSize(14); font.setBold(True)
        self.document.setDefaultFont(font)
        self.document.setDocumentMargin(0)
        self.update_label(row, custom_colors)
        rect = image_item.boundingRect()
        self.setPos(row["x"] * rect.width(), row["y"] * rect.height())
        self.set_editing(editing)

    def update_label(self, row, custom_colors):
        self.prepareGeometryChange()
        label = row["label"]
        subtype = row["subtype"] if self.kind == "custom" else ""
        self.color = QColor(custom_colors.get(subtype, DEVICE_COLORS[self.kind]))
        text_color = "#15181E" if subtype and self.color.lightnessF() > .48 else "white"
        text = tex_to_html(label)
        if subtype:
            text += f'<br><span style="font-size:11px">{escape(subtype)}</span>'
        self.document.setHtml(f'<span style="color:{text_color}">{text}</span>')
        size = self.document.size()
        self.badge = QRectF(9, -size.height() - 12, size.width() + 16, size.height() + 8)
        self.setToolTip(f"{subtype or DEVICE_NAMES[self.kind]} · {label}")
        self.update()

    def set_editing(self, editing):
        self.editing = editing
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, editing)
        self.setCursor(Qt.CursorShape.SizeAllCursor if editing else Qt.CursorShape.PointingHandCursor)

    def boundingRect(self):
        return self.badge.united(QRectF(-7, -7, 14, 14)).adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        highlight = self.isSelected()
        painter.setPen(QPen(QColor("#f59e0b" if highlight and self.kind == "custom" else "white"), 2))
        painter.setBrush(QColor("#f59e0b") if highlight and self.kind != "custom" else self.color)
        if self.kind == "readout":
            painter.drawRoundedRect(QRectF(-6, -3, 12, 6), 2, 2)
        elif self.kind == "filter":
            painter.drawRoundedRect(QRectF(-6, -6, 12, 12), 2, 2)
        else:
            painter.drawEllipse(QPointF(0, 0), 6, 6)
        painter.drawLine(QPointF(4, -4), self.badge.bottomLeft())
        painter.drawRoundedRect(self.badge, 5, 5)
        painter.translate(self.badge.topLeft() + QPointF(8, 4))
        self.document.drawContents(painter)

    def mousePressEvent(self, event):
        self.start_pos = self.pos()
        self._other_movable = []
        if self.isSelected():
            for item in self.scene().selectedItems():
                if item is not self and item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
                    self._other_movable.append(item); item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        for item in self._other_movable:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        if self.editing and self.pos() != self.start_pos:
            rect = self.parentItem().boundingRect()
            x = max(0, min(1, self.pos().x() / rect.width()))
            y = max(0, min(1, self.pos().y() / rect.height()))
            self.setPos(x * rect.width(), y * rect.height())
            self.moved.emit(self.device_id, x, y)
        elif not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.activated.emit(self.device_id, event.screenPos())


class DesignView(RightPanMixin, QGraphicsView):
    placed = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.design_scene = QGraphicsScene(self)
        self.setScene(self.design_scene)
        self.image_item = None
        self.markers = {}
        self.editing = False
        self.marker_opacity = 1.0
        self.fitted = True
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def set_image(self, image=None, angle=0):
        self.design_scene.clear()
        self.markers = {}
        self.image_item = None
        self.resetTransform()
        if image is not None:
            self.image_item = self.design_scene.addPixmap(QPixmap.fromImage(image))
            self.image_item.setTransformOriginPoint(self.image_item.boundingRect().center())
            self.set_rotation(angle)
        else:
            self.design_scene.setSceneRect(QRectF())

    def set_rotation(self, angle):
        self.image_item.setRotation(angle)
        self.design_scene.setSceneRect(self.image_item.sceneBoundingRect().adjusted(-80, -80, 80, 80))
        self.fit_image()

    def sync_markers(self, rows, custom_colors=None):
        if custom_colors is None:
            custom_colors = custom_device_colors(row["subtype"] for row in rows if row["kind"] == "custom")
        wanted = {row["device_id"] for row in rows}
        for ident in set(self.markers) - wanted:
            item = self.markers.pop(ident)
            self.design_scene.removeItem(item)
            item.setParentItem(None)
            item.deleteLater()
        for row in rows:
            ident = row["device_id"]
            if ident in self.markers:
                item = self.markers[ident]
                item.update_label(row, custom_colors)
                rect = self.image_item.boundingRect()
                item.setPos(row["x"] * rect.width(), row["y"] * rect.height())
            else:
                self.markers[ident] = DesignMarker(row, self.image_item, self.editing, custom_colors)
            self.markers[ident].setOpacity(self.marker_opacity)

    def set_marker_opacity(self, opacity):
        self.marker_opacity = opacity
        for item in self.markers.values():
            item.setOpacity(opacity)

    def set_editing(self, editing):
        self.editing = editing
        for item in self.markers.values():
            item.set_editing(editing)

    def fit_image(self):
        self.fitted = True
        if self.image_item:
            self.fitInView(self.design_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        if self.image_item:
            self.fitted = False
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            scale = self.transform().m11() * factor
            if .02 < scale < 40:
                self.scale(factor, factor)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_image()

    def showEvent(self, event):
        super().showEvent(event)
        if self.fitted:
            self.fit_image()

    def mousePressEvent(self, event):
        self.press_pos = event.position().toPoint()
        self.pressed_marker = isinstance(self.itemAt(self.press_pos), DesignMarker)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if (event.button() == Qt.MouseButton.LeftButton and self.editing and self.image_item
                and not self.pressed_marker
                and (event.position().toPoint() - self.press_pos).manhattanLength() < 5):
            pos = self.image_item.mapFromScene(self.mapToScene(event.position().toPoint()))
            rect = self.image_item.boundingRect()
            if rect.contains(pos):
                self.placed.emit(pos.x() / rect.width(), pos.y() / rect.height())


class ChipDesignPanel(QWidget):
    saved = Signal(str)
    failed = Signal(str)
    device_clicked = Signal(str, QPoint)

    def __init__(self, device_combo=None):
        super().__init__()
        self.store = None
        self.readout_visible = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        self.import_button = QPushButton("导入设计图")
        self.import_button.clicked.connect(self.import_image)
        toolbar.addWidget(self.import_button)
        self.clipboard_import_button = QPushButton("从剪贴板导入")
        self.clipboard_import_button.setToolTip("先复制图片或截图，再从剪贴板导入为芯片设计图")
        self.clipboard_import_button.clicked.connect(self.import_clipboard_image)
        toolbar.addWidget(self.clipboard_import_button)
        self.remove_image_button = QPushButton("移除设计图")
        self.remove_image_button.clicked.connect(self.remove_image)
        toolbar.addWidget(self.remove_image_button)
        toolbar.addStretch()
        self.rotate_left = QPushButton("左转 90°")
        self.rotate_left.clicked.connect(lambda: self.angle.setValue((self.angle.value() - 90) % 360))
        toolbar.addWidget(self.rotate_left)
        self.rotate_right = QPushButton("右转 90°")
        self.rotate_right.clicked.connect(lambda: self.angle.setValue((self.angle.value() + 90) % 360))
        toolbar.addWidget(self.rotate_right)
        self.angle = QDoubleSpinBox()
        self.angle.setRange(0, 359.9); self.angle.setDecimals(1); self.angle.setSingleStep(5)
        self.angle.setSuffix("°"); self.angle.setWrapping(True)
        self.angle.setToolTip("顺时针旋转角度，标注随图旋转，名称保持正向")
        self.angle.valueChanged.connect(self.rotate_image)
        toolbar.addWidget(self.angle)
        self.fit_button = QPushButton("适配设计图")
        self.fit_button.clicked.connect(lambda: self.view.fit_image())
        toolbar.addWidget(self.fit_button)
        layout.addLayout(toolbar)
        row = QHBoxLayout()
        self.edit_button = QPushButton("编辑标注"); self.edit_button.setCheckable(True)
        self.edit_button.toggled.connect(self.set_editing)
        row.addWidget(self.edit_button)
        self.translucent_button = QPushButton("半透明标注")
        self.translucent_button.setCheckable(True)
        self.translucent_button.setToolTip("以 50% 不透明度显示标注，便于查看下方版图")
        row.addWidget(self.translucent_button)
        self.device_kind = QComboBox()
        self.device_kind.addItem("全部元件", None)
        for kind, caption in DEVICE_NAMES.items():
            self.device_kind.addItem(caption, kind)
        self.device_kind.currentIndexChanged.connect(self.refresh_markers)
        row.addWidget(self.device_kind)
        self.devices = device_combo if device_combo is not None else QComboBox()
        self.devices.setMinimumWidth(180)
        self.devices.currentIndexChanged.connect(self.update_controls)
        row.addWidget(self.devices, 1)
        self.remove_marker_button = QPushButton("移除所选标注")
        self.remove_marker_button.clicked.connect(self.remove_marker)
        row.addWidget(self.remove_marker_button)
        layout.addLayout(row)
        self.view = DesignView(); self.view.placed.connect(self.place_marker)
        self.translucent_button.toggled.connect(lambda checked: self.view.set_marker_opacity(.5 if checked else 1.0))
        layout.addWidget(self.view, 1)
        self.hint = QLabel(); self.hint.setWordWrap(True); self.hint.setProperty("role", "muted")
        layout.addWidget(self.hint)
        self.summary = QLabel(); self.summary.setProperty("role", "muted")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.update_controls()

    def refresh(self, store):
        if store is not self.store:
            self.store = store
            self.edit_button.setChecked(False)
            self.reload_image()
        self.refresh_markers()

    def reload_image(self):
        row = self.store.chip_design() if self.store else None
        self.angle.blockSignals(True)
        self.angle.setValue(row["rotation"] if row else 0)
        self.angle.blockSignals(False)
        self.view.set_image(design_image(row["data"]) if row else None, row["rotation"] if row else 0)
        self.file_name = row["file_name"] if row else ""

    def refresh_markers(self, *_):
        chosen = self.devices.currentData()
        rows = self.store.design_markers() if self.store else []
        devices = self.store.devices() if self.store else []
        if self.view.image_item:
            colors = custom_device_colors(device["subtype"] for device in devices if device["kind"] == "custom")
            self.view.sync_markers(rows, colors)
            for item in self.view.markers.values():
                if not getattr(item, "connected", False):
                    item.activated.connect(self.activate_marker)
                    item.moved.connect(self.move_marker)
                    item.connected = True
                item.setVisible(self.readout_visible or item.kind not in ("readout", "filter"))
        marked = {row["device_id"] for row in rows}
        self.devices.blockSignals(True)
        self.devices.clear()
        if self.store:
            for device in devices:
                if not self.readout_visible and device["kind"] in ("readout", "filter"):
                    continue
                if self.device_kind.currentData() and device["kind"] != self.device_kind.currentData():
                    continue
                status = "已标注" if device["id"] in marked else "未标注"
                kind = device["subtype"] if device["kind"] == "custom" else DEVICE_NAMES[device["kind"]]
                self.devices.addItem(f"{kind} · {device['label']} · {status}", device["id"])
        self.devices.setCurrentIndex(max(0, self.devices.findData(chosen)))
        self.devices.blockSignals(False)
        total = len(devices)
        self.summary.setText(f"{self.file_name} · 已标注 {len(rows)}/{total} 个器件 · 更改立即保存"
                             if self.view.image_item else "尚未导入芯片设计图；图片和标注将内嵌保存至 .qchip")
        self.update_controls()

    def update_controls(self, *_):
        has_image = self.view.image_item is not None
        self.import_button.setEnabled(self.store is not None)
        self.clipboard_import_button.setEnabled(self.store is not None)
        for button in (self.remove_image_button, self.rotate_left, self.rotate_right,
                       self.angle, self.fit_button, self.edit_button, self.translucent_button):
            button.setEnabled(has_image)
        editing = self.edit_button.isChecked()
        self.devices.setEnabled(has_image)
        self.device_kind.setEnabled(has_image)
        chosen = self.devices.currentData()
        self.remove_marker_button.setEnabled(has_image and editing and chosen in self.view.markers)
        for ident, item in self.view.markers.items():
            item.setSelected(ident == chosen)
        self.hint.setText("选择器件后点击设计图放置或重新定位；拖动标注调整位置。右键拖动平移，滚轮缩放。"
                          if editing else "查看模式 · 左键点选或框选标注；右键拖动平移，滚轮缩放。开启“编辑标注”后登记位置。")

    def set_editing(self, editing):
        self.view.set_editing(editing)
        self.update_controls()

    def import_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入芯片设计图", "", IMAGE_FILTER)
        if not path:
            return
        try:
            name, data = read_design_file(path)
            self._replace_design(name, data)
        except Exception as exc:
            self.failed.emit(str(exc))

    def import_clipboard_image(self):
        try:
            payload = clipboard_payload()
            if payload is None:
                raise ValueError("剪贴板中没有图片，请先复制图片或截图")
            self._replace_design(payload.file_name, payload.data)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _replace_design(self, name, data):
        if self.view.image_item and QMessageBox.question(
                self, "替换设计图", "替换后将清除当前设计图的标注和旋转角度，是否继续？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.store.set_chip_design(name, data)
        self.reload_image(); self.refresh_markers()
        self.saved.emit("芯片设计图")

    def remove_image(self):
        if QMessageBox.question(self, "移除设计图", "移除设计图及其全部标注？芯片逻辑构型将保留。"
                                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.remove_chip_design()
            self.edit_button.setChecked(False)
            self.reload_image(); self.refresh_markers()
            self.saved.emit("设计图移除")
        except Exception as exc:
            self.failed.emit(str(exc))

    def rotate_image(self, angle):
        try:
            self.store.set_design_rotation(angle)
            self.view.set_rotation(angle)
            self.saved.emit("设计图旋转")
        except Exception as exc:
            self.angle.blockSignals(True)
            self.angle.setValue(self.view.image_item.rotation())
            self.angle.blockSignals(False)
            self.failed.emit(str(exc))

    def place_marker(self, x, y):
        ident = self.devices.currentData()
        if ident:
            self.move_marker(ident, x, y)

    def move_marker(self, ident, x, y):
        try:
            self.store.set_design_marker(ident, x, y)
            self.devices.setCurrentIndex(self.devices.findData(ident))
            self.refresh_markers()
            self.saved.emit("设计图标注")
        except Exception as exc:
            self.refresh_markers()
            self.failed.emit(str(exc))

    def remove_marker(self):
        try:
            self.store.remove_design_marker(self.devices.currentData())
            self.refresh_markers()
            self.saved.emit("标注移除")
        except Exception as exc:
            self.failed.emit(str(exc))

    def activate_marker(self, ident, screen_pos):
        if self.devices.findData(ident) < 0:
            self.device_kind.setCurrentIndex(0)
        self.devices.setCurrentIndex(self.devices.findData(ident))
        if not self.edit_button.isChecked():
            self.device_clicked.emit(ident, screen_pos)

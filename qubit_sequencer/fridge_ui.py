"""Cryostat port map and sample allocation workspace."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGraphicsEllipseItem, QGraphicsItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .fridge_store import display_line_type, stored_line_type
from .fridge_templates import GRID_NAMES, TEMPLATE_NAMES, grid_height, snap_to_grid
from .fridge_circuit_ui import CircuitDialog, PresetDialog, component_text
from .device_types import DEVICE_NAMES
from .sample_box_store import BOX_ROLES, box_number_source
from .undo import EditHistory
from .wiring_store import LINE_TYPES
from .theme import DIAGRAM_COLORS, UI_COLORS
from .ui_icons import icon
from .graphics_interaction import RightPanMixin


def port_color(kind, theme):
    colors = DIAGRAM_COLORS[theme]
    key = {"XY": "x_check", "Z": "x_check", "XY/Z": "x_check",
           "直流": "muted", "读取输入": "data", "读取输出": "coupler", "读入": "data", "读出": "coupler"}.get(kind, "missing")
    return QColor(colors[key])


class PortView(RightPanMixin, QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._fit_active = False
        self._resize_callback = None
        self._resize_scheduled = False
        self._extending = False
        self.placement_callback = None

    def _extend_scene(self):
        scene = self.scene()
        if scene is None or self._extending:
            return
        self._extending = True
        try:
            visible = self.mapToScene(self.viewport().rect()).boundingRect()
            current = scene.sceneRect()
            if (visible.left() < current.left() + 100 or visible.right() > current.right() - 100
                    or visible.top() < current.top() + 100 or visible.bottom() > current.bottom() - 100):
                scene.setSceneRect(current.united(visible.adjusted(-600, -600, 600, 600)))
        finally:
            self._extending = False

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._extend_scene()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        if event.angleDelta().y() and 0.08 <= self.transform().m11() * factor <= 8.0:
            self._fit_active = False
            self.scale(factor, factor)
            self._extend_scene()
        event.accept()

    def mousePressEvent(self, event):
        if self.placement_callback and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            self.placement_callback(point)
            event.accept()
            return
        self._drag_origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, "_drag_origin")
                and (event.position().toPoint() - self._drag_origin).manhattanLength() > 4):
            self._fit_active = False
        super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._extend_scene()
        if self._resize_callback and not self._resize_scheduled:
            self._resize_scheduled = True
            QTimer.singleShot(0, self._after_resize)

    def _after_resize(self):
        self._resize_scheduled = False
        if self._resize_callback:
            self._resize_callback()


class PlateScene(QGraphicsScene):
    def __init__(self, panel):
        super().__init__(panel); self.panel = panel

    def drawBackground(self, painter, rect):
        panel = self.panel
        colors = DIAGRAM_COLORS[panel.theme]
        painter.fillRect(rect, QColor(colors["paper"]))
        if panel._closed or panel.is_overview():
            return
        grid = panel.store.board_grid(panel.board.currentData())
        step_x, step_y = 58.0, 58.0 * grid_height(grid)
        left, right = math.floor(rect.left() / step_x) - 1, math.ceil(rect.right() / step_x) + 1
        top, bottom = math.floor(rect.top() / step_y) - 1, math.ceil(rect.bottom() / step_y) + 1
        count = (right - left + 1) * (bottom - top + 1)
        stride = max(1, math.ceil(math.sqrt(count / 6000)))
        painter.save(); painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(colors["dot_major"]))
        for row in range(math.ceil(top / stride) * stride, bottom + 1, stride):
            offset = 0.5 if grid == "triangle" and row % 2 else 0.0
            for col in range(math.ceil(left / stride) * stride, right + 1, stride):
                painter.drawEllipse(QPointF((col + offset) * step_x, row * step_y), 2.2, 2.2)
        painter.restore()


def buttons(dialog, layout, label="保存"):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setText(label)
    box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    box.accepted.connect(dialog.accept); box.rejected.connect(dialog.reject)
    layout.addWidget(box)


def line_type_choices(combo, value):
    combo.setEditable(True)
    combo.addItems(list(dict.fromkeys(display_line_type(kind) for kind in LINE_TYPES)))
    combo.setCurrentText(display_line_type(value))


def line_type_value(combo, original):
    value = combo.currentText()
    return original if original and value == display_line_type(original) else stored_line_type(value)


class PortDialog(QDialog):
    def __init__(self, parent, port=None):
        super().__init__(parent); self.setWindowTitle("编辑端口" if port else "新增端口")
        layout = QVBoxLayout(self); form = QFormLayout(); layout.addLayout(form)
        self.board = QLineEdit(port["board"] if port else "A")
        self.number = QLineEdit(port["number"] if port else "")
        self.original_kind = port["kind"] if port else ""
        self.kind = QComboBox(); line_type_choices(self.kind, self.original_kind or "XY/Z")
        self.x = QDoubleSpinBox(); self.y = QDoubleSpinBox()
        for spin in (self.x, self.y):
            spin.setRange(-1000, 1000); spin.setDecimals(2); spin.setSingleStep(0.5)
        if port:
            self.x.setValue(port["x"]); self.y.setValue(port["y"])
        self.original_position = (port["x"], port["y"]) if port else None
        self.position_edited = False
        self.x.valueChanged.connect(self._position_changed); self.y.valueChanged.connect(self._position_changed)
        for title, widget in (("盘号", self.board), ("端口编号", self.number), ("用途", self.kind),
                              ("横向位置", self.x), ("纵向位置", self.y)):
            form.addRow(title, widget)
        buttons(self, layout)

    def _position_changed(self, *_):
        self.position_edited = True

    def position(self):
        if self.original_position and not self.position_edited:
            return self.original_position
        return self.x.value(), self.y.value()


class BoardDialog(QDialog):
    def __init__(self, parent, default_name):
        super().__init__(parent); self.setWindowTitle("新建盘")
        layout = QVBoxLayout(self); form = QFormLayout(); layout.addLayout(form)
        self.name = QLineEdit(default_name)
        self.template = QComboBox()
        for key, label in TEMPLATE_NAMES.items(): self.template.addItem(label, key)
        form.addRow("新盘名称", self.name); form.addRow("内置模板", self.template)
        self.with_preset = QCheckBox("同时应用该盘的低温线路预设")
        layout.addWidget(self.with_preset)
        note = QLabel("模板复制盘位、编号和端口类型，不复制样品分配。")
        note.setWordWrap(True); layout.addWidget(note); buttons(self, layout, "创建")


class AssignmentDialog(QDialog):
    def __init__(self, parent, store, port, branch=None):
        super().__init__(parent); self.store = store; self.port = port; self.branch = branch
        data = dict(port)
        if branch is not None:
            assigned = next((r for r in store.branch_assignments(port["id"]) if r["branch"] == branch), None)
            for key in ("sample_id", "pin", "drawing", "usage", "notes", "devices_json", "box_port_id"):
                data[key] = assigned[key] if assigned else None
        self.data = data
        self.setWindowTitle(f"分配 {port['board']} 盘 · {port['number']}" + (f" · 分支 {branch}" if branch else ""))
        self.resize(450, 480)
        layout = QVBoxLayout(self); form = QFormLayout(); layout.addLayout(form); self.form = form
        self.sample = QComboBox()
        for s in store.samples():
            self.sample.addItem(f"{s['name']} · {Path(s['path']).name}", s["id"])
        index = self.sample.findData(data["sample_id"])
        if index >= 0:
            self.sample.setCurrentIndex(index); self.sample.setEnabled(False)
        self.pin = QLineEdit(data["pin"] or ""); self.drawing = QLineEdit(data["drawing"] or "")
        self.notes = QLineEdit(data["notes"] or "")
        self.original_usage = data["usage"] or ""
        self.usage = QComboBox(); line_type_choices(self.usage, self.original_usage or port["kind"])
        self.mode = QComboBox(); self.mode.addItem("直接关联器件", "direct"); self.mode.addItem("样品盒接口", "box")
        self.box_port = QComboBox()
        self.box_detail = QLabel(); self.box_detail.setTextFormat(Qt.TextFormat.PlainText); self.box_detail.setWordWrap(True)
        self.box_ports = {}
        form.addRow("样品", self.sample); form.addRow("线路用途", self.usage)
        form.addRow("分配方式", self.mode); form.addRow("样品引脚", self.pin)
        form.addRow("样品盒接口", self.box_port); form.addRow(self.box_detail)
        form.addRow("历史图纸文字／信号名称", self.drawing); form.addRow("备注", self.notes)
        self.devices_label = QLabel("关联器件（可多选）"); layout.addWidget(self.devices_label)
        self.devices = QListWidget(); layout.addWidget(self.devices)
        self.error = QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        self.sample.currentIndexChanged.connect(self.load_devices)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.box_port.currentIndexChanged.connect(self.show_box_port)
        self.load_devices(); buttons(self, layout)

    def load_devices(self):
        self.devices.clear(); self.error.clear()
        self.box_ports = {}
        self.box_port.blockSignals(True); self.box_port.clear()
        self.box_port.addItem("请选择接口", None)
        sample_id = self.sample.currentData()
        try:
            selected = {d["id"] for d in json.loads(self.data["devices_json"] or "[]")}
            devices = self.store.sample_devices(sample_id)
            known = {device["id"] for device in devices}
            devices += [dict(device, status="器件已删除") for device in json.loads(self.data["devices_json"] or "[]")
                        if device["id"] not in known]
            for device in devices:
                number = f" · 图纸：{device['drawing_number']}" if device.get("drawing_number") else ""
                status = f"（{device['status']}）" if device.get("status") else ""
                kind = DEVICE_NAMES.get(device["kind"], device["kind"])
                if device["kind"] == "custom" and device.get("subtype"):
                    kind += f"（{device['subtype']}）"
                item = QListWidgetItem(f"{device['label']}  ·  {kind}{number}{status}")
                item.setData(Qt.ItemDataRole.UserRole, device["id"])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if device["id"] in selected else Qt.CheckState.Unchecked)
                self.devices.addItem(item)
            self.box_ports = {p["id"]: p for p in self.store.sample_box_ports(sample_id)}
            occupied = {row["box_port_id"]: row for row in self.store.box_connections(sample_id)}
            for port in sorted(self.box_ports.values(), key=lambda p: (0, int(p["number"])) if p["number"].isdecimal() else (1, p["number"])):
                label = f"{port['number']} · {port['signal_name'] or port['device_label'] or '未关联元件'}"
                connection = occupied.get(port["id"])
                if connection and (connection["id"], connection["branch"]) != (self.port["id"], self.branch):
                    branch = f" · 分支 {connection['branch']}" if connection["branch"] is not None else ""
                    label += f"（已接 {connection['board']} 盘 {connection['number']}{branch}）"
                self.box_port.addItem(label, port["id"])
        except Exception as exc:
            self.error.setText(f"无法读取样品，请先重新定位：{exc}")
        existing = self.data.get("box_port_id")
        if existing and self.box_port.findData(existing) < 0:
            self.box_port.addItem("原样品盒接口不可用", existing)
        self.box_port.setCurrentIndex(max(0, self.box_port.findData(existing)))
        self.box_port.blockSignals(False)
        self.mode.model().item(1).setEnabled(bool(self.box_ports or existing))
        self.mode.setCurrentIndex(1 if existing or (not self.data["sample_id"] and self.box_ports) else 0)
        self.update_mode()

    def update_mode(self):
        use_box = self.mode.currentData() == "box"
        self.form.setRowVisible(self.pin, not use_box)
        self.form.setRowVisible(self.box_port, use_box)
        self.form.setRowVisible(self.box_detail, use_box)
        self.devices_label.setVisible(not use_box); self.devices.setVisible(not use_box)
        self.show_box_port()

    def show_box_port(self):
        port = self.box_ports.get(self.box_port.currentData())
        if port is None:
            self.box_detail.clear()
            return
        device = f"{port['device_label'] or '未关联元件'} {BOX_ROLES.get(port['role'], '')}".rstrip()
        subtype = f" · {port['device_subtype']}" if port["device_kind"] == "custom" else ""
        self.box_detail.setText(f"{box_number_source(port)} · {device}{subtype}")

    def device_ids(self):
        return [self.devices.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.devices.count())
                if self.devices.item(i).checkState() == Qt.CheckState.Checked]


class PortItem(QGraphicsEllipseItem):
    def __init__(self, port, window, grid):
        super().__init__(-22, -22, 44, 44); self.port = port; self.window = window
        self.grid = grid; self.board_offset = None
        self.setPos(port["x"] * 58, port["y"] * 58)
        color = port_color(port["kind"], window.theme)
        self.setBrush(QBrush(color))
        self.setPen(QPen(QColor(DIAGRAM_COLORS[window.theme]["ink"]), 1.2))
        if port["sample_id"] or window.store.branch_marks(port["id"]):
            for width, shade in ((5, DIAGRAM_COLORS[window.theme]["paper"]),
                                 (3, "#00B8D9" if window.theme == "light" else "#52E6FF")):
                ring = QGraphicsEllipseItem(-25, -25, 50, 50, self)
                ring.setPen(QPen(QColor(shade), width)); ring.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                ring.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if port["manually_occupied"]:
            badge = QGraphicsEllipseItem(11, -25, 19, 19, self)
            badge.setPen(QPen(QColor("#FFFFFF"), 1)); badge.setBrush(QBrush(QColor("#B91C1C" if port["manual_status"] == "damaged" else "#C2410C")))
            badge.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            mark = QGraphicsSimpleTextItem("损" if port["manual_status"] == "damaged" else "占", badge); mark.setBrush(QBrush(QColor("#FFFFFF")))
            font = mark.font(); font.setPixelSize(12); mark.setFont(font)
            bounds = mark.boundingRect(); mark.setPos(20.5 - bounds.width() / 2, -15.5 - bounds.height() / 2)
        self.setToolTip(f"{port['board']} · {port['number']}\n{display_line_type(port['kind'])}\n{port['sample_name'] or '未分配'}" + ("\n已损坏" if port["manual_status"] == "damaged" else "\n已占用（手动标记）" if port["manually_occupied"] else ""))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not window.preview and not window.is_overview())
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
        label = QGraphicsSimpleTextItem(port["number"], self)
        label.setBrush(QBrush(QColor("#20252E" if brightness > 150 else "#FFFFFF")))
        font = label.font(); font.setPixelSize(20); label.setFont(font)
        branches = window.store.circuit(port["id"]).get("switch", [])
        rect = label.boundingRect(); label.setPos(-rect.width() / 2, -rect.height() / 2 - (6 if branches else 0))
        if branches:
            assigned = {row["branch"] for row in window.store.branch_assignments(port["id"])}
            used = assigned | window.store.branch_marks(port["id"])
            ratio = QGraphicsSimpleTextItem(f"{len(used)}/{len(branches)}", self)
            ratio.setBrush(label.brush()); font = ratio.font(); font.setPixelSize(10); font.setBold(True); ratio.setFont(font)
            rect = ratio.boundingRect(); ratio.setPos(-rect.width() / 2, 7); ratio.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.setToolTip(self.toolTip() + f"\n开关分支：{len(used)}/{len(branches)} 已占用")

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.board_offset is not None
                and self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
            x = (value.x() - self.board_offset[0]) / 58
            y = (value.y() - self.board_offset[1]) / 58
            x, y = snap_to_grid(x, y, self.grid)
            return QPointF(self.board_offset[0] + x * 58, self.board_offset[1] + y * 58)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self.start = self.pos(); self.press_screen = event.screenPos(); self.drag_started = False
        self._other_movable = []
        if self.isSelected():
            for item in self.scene().selectedItems():
                if item is not self and item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
                    self._other_movable.append(item); item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.window.select_port(self.port["id"])
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:
            if not self.drag_started and (event.screenPos() - self.press_screen).manhattanLength() < QApplication.startDragDistance():
                event.accept(); return
            self.drag_started = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        for item in self._other_movable:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        if self.drag_started and (self.pos() - self.start).manhattanLength() > 3:
            p = self.port
            window = self.window
            x = (self.pos().x() - self.board_offset[0]) / 58
            y = (self.pos().y() - self.board_offset[1]) / 58
            def save_position():
                if not window._closed:
                    window.perform(lambda: window.store.save_port(p["id"], p["board"], p["number"], x, y, p["kind"]))
            QTimer.singleShot(0, save_position)

    def mouseDoubleClickEvent(self, event):
        if self.window.is_overview():
            self.window.open_board(self.port["board"])
        elif not self.window.preview:
            self.window.select_port(self.port["id"]); self.window.edit_assignment()
        event.accept()


class FridgePanel(QWidget):
    def __init__(self, store, parent=None, host=None, preview=False):
        super().__init__(parent); self.store = store; self.host = host; self.selected = None
        self.preview = preview; self.selected_branch = None
        self._selection_order = []
        self._closed = False; self._fitting = False; self.placing_port = False
        self.sample_watcher = QFileSystemWatcher(self)
        self.sample_refresh_timer = QTimer(self); self.sample_refresh_timer.setSingleShot(True)
        self.sample_refresh_timer.setInterval(150); self.sample_refresh_timer.timeout.connect(self._samples_changed)
        self.sample_watcher.fileChanged.connect(lambda _: self.sample_refresh_timer.start())
        self.sample_watcher.directoryChanged.connect(lambda _: self.sample_refresh_timer.start())
        self.theme = getattr(host, "theme", "light")
        self.icon_bindings = []
        self.setWindowTitle(f"{store.name()} — 制冷机接线"); self.resize(1250, 820)
        self.history = EditHistory(self._history_changed); self.history.bind(store.db); store.undo_history = self.history
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        top = QHBoxLayout(); layout.addLayout(top)
        for title, entries in (("样品管理", (("关联样品", self.add_sample), ("重新定位样品", self.relocate_sample), ("移除样品", self.remove_sample))),
                               ("导出", (("打包导出 ZIP", self.export_bundle), ("导出布线表", self.export_table)))):
            button = QPushButton(title); menu = QMenu(button)
            self.bind_icon(button, "samples" if title == "样品管理" else "export")
            for name, slot in entries: menu.addAction(name, slot)
            button.setMenu(menu); button.setEnabled(not preview); top.addWidget(button)
        migrate = QPushButton("迁移旧接线"); migrate.clicked.connect(self.migrate)
        self.bind_icon(migrate, "connect")
        migrate.setEnabled(not preview); top.addWidget(migrate)
        preset = QPushButton("应用线路预设"); preset.clicked.connect(self.apply_preset)
        self.bind_icon(preset, "wiring")
        preset.setEnabled(not preview); top.addWidget(preset)
        self.check_wiring_button = QPushButton("检查接线类型")
        self.check_wiring_button.setToolTip("核对整个制冷机项目中已分配的端口和开关分支")
        self.check_wiring_button.setEnabled(not preview)
        self.check_wiring_button.clicked.connect(self.check_wiring_types)
        top.addWidget(self.check_wiring_button)
        new_board = QPushButton("新建盘"); new_board.clicked.connect(self.add_board)
        self.bind_icon(new_board, "new")
        new_board.setEnabled(not preview); top.addWidget(new_board)
        top.addStretch()
        filters = QHBoxLayout(); layout.addLayout(filters)
        self.board = QComboBox(); self.sample = QComboBox(); self.kind = QComboBox(); self.status = QComboBox()
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索端口、样品、引脚或器件")
        for combo in (self.board, self.sample, self.kind, self.status): filters.addWidget(combo)
        self.board.currentIndexChanged.connect(self.board_changed)
        for combo in (self.sample, self.kind, self.status): combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.search, 1); self.search.textChanged.connect(self.refresh)
        self.legend = QLabel()
        self.legend.setWordWrap(True); self.legend.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.legend.setProperty("role", "muted"); layout.addWidget(self.legend)
        canvas_bar = QWidget(); canvas_bar.setObjectName("canvasToolbar")
        canvas_layout = QHBoxLayout(canvas_bar); canvas_layout.setContentsMargins(8, 6, 8, 6)
        self.mode_hint = QLabel(); self.mode_hint.setWordWrap(True)
        self.mode_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.mode_hint.setProperty("role", "muted"); canvas_layout.addWidget(self.mode_hint, 1)
        self.grid_choice = QComboBox()
        for key, label in GRID_NAMES.items(): self.grid_choice.addItem(label, key)
        self.grid_choice.setToolTip("切换格子时端口自动吸附，之前的撤回记录会清空。")
        self.grid_choice.currentIndexChanged.connect(self.change_grid)
        canvas_layout.addWidget(self.grid_choice)
        self.overview_button = QPushButton("返回总览"); self.overview_button.clicked.connect(lambda: self.board.setCurrentIndex(0))
        self.bind_icon(self.overview_button, "overview")
        canvas_layout.addWidget(self.overview_button)
        self.fit_button = QPushButton("适配视图"); self.fit_button.clicked.connect(self.fit_map); canvas_layout.addWidget(self.fit_button)
        self.bind_icon(self.fit_button, "fit")
        layout.addWidget(canvas_bar)
        splitter = QSplitter(Qt.Orientation.Horizontal); layout.addWidget(splitter, 1)
        self.views = QTabWidget(); splitter.addWidget(self.views)
        self.graphics = PortView(); self.graphics._resize_callback = self.view_resized
        self.overview_columns = None
        self.scene = PlateScene(self); self.graphics.setScene(self.scene)
        self.scene.selectionChanged.connect(self.canvas_selection_changed)
        self.views.addTab(self.graphics, "盘位图")
        self.table = QTableWidget(); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.cellClicked.connect(self.table_selected); self.table.cellDoubleClicked.connect(self.table_activated)
        self.views.addTab(self.table, "布线表")
        self.views.currentChanged.connect(self.view_changed)
        inspector = QWidget(); self.inspector = inspector; inspector.setObjectName("inspector"); inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(14, 12, 14, 12); inspector_layout.setSpacing(8)
        title = QLabel("端口详情"); title.setProperty("role", "heading"); inspector_layout.addWidget(title)
        self.info = QLabel("选择端口查看详情")
        self.info.setWordWrap(True); self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inspector_layout.addWidget(self.info)
        inspector_layout.addWidget(QLabel("端口整体状态"))
        self.occupied = QComboBox()
        self.occupied.addItem("未分配", ""); self.occupied.addItem("已占用", "occupied"); self.occupied.addItem("已损坏", "damaged")
        self.occupied.setToolTip("手动标记的占用或损坏端口排除新的分配；已分配样品自动显示为已占用。")
        self.occupied.currentIndexChanged.connect(self.toggle_occupied); inspector_layout.addWidget(self.occupied)
        self.occupied_branch = QComboBox(); self.occupied_branch.currentIndexChanged.connect(self.show_occupied_drawing)
        inspector_layout.addWidget(self.occupied_branch)
        self.branch_occupied = QCheckBox("当前分支手动占用")
        self.branch_occupied.setToolTip("仅保留所选分支；不会影响其他分支。")
        self.branch_occupied.toggled.connect(self.toggle_branch_occupied); inspector_layout.addWidget(self.branch_occupied)
        self.occupied_drawing = QLineEdit(); self.occupied_drawing.setPlaceholderText("接线目标图纸编号（可选）")
        self.occupied_drawing.editingFinished.connect(self.save_occupied_drawing); inspector_layout.addWidget(self.occupied_drawing)
        self.circuit_info = QLabel("尚未选择端口")
        self.circuit_info.setWordWrap(True); self.circuit_info.setTextFormat(Qt.TextFormat.PlainText)
        detail_scroll = QScrollArea(); detail_scroll.setWidgetResizable(True)
        detail_scroll.setWidget(self.circuit_info); detail_scroll.setMinimumHeight(100); detail_scroll.setMaximumHeight(210)
        inspector_layout.addWidget(detail_scroll)
        self.port_buttons = []
        for title, slot, symbol in (("分配／编辑", self.edit_assignment, "connect"), ("解除分配", self.unassign, "unlink"), ("查看迁移原记录", self.view_legacy, "history"),
                            ("新增端口", self.add_port, "new"), ("编辑端口", self.edit_port, "edit"),
                            ("删除端口", self.delete_port, "delete"), ("查看 / 编辑低温线路", self.edit_circuit, "wiring")):
            b = QPushButton(title); self.bind_icon(b, symbol); b.clicked.connect(slot); inspector_layout.addWidget(b); self.port_buttons.append(b)
        inspector_layout.addStretch(); inspector.setMinimumWidth(220); inspector.setMaximumWidth(350)
        splitter.addWidget(inspector); splitter.setSizes([900, 260]); splitter.setStretchFactor(0, 1)
        self.cancel_action = QAction(self)
        if not host:
            self.cancel_action.setShortcut("Esc")
            self.cancel_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.cancel_action.triggered.connect(self.cancel_placement); self.cancel_action.setEnabled(False)
        self.addAction(self.cancel_action)
        self.refresh_filters(); self.refresh(); QTimer.singleShot(0, self.fit_map)

    def _history_changed(self):
        if self.host:
            self.host._update_undo_action()
            if self.host.fridge_panel is self and self.host.tabs.is_page_visible(self.host.sample_box_panel):
                self.host.sample_box_panel.refresh_connections()
            if (self.host.fridge_panel is self and self.host.store and self.host.scene and
                    (self.host.field_combo.currentData() or '').startswith('wiring.')):
                self.host.refresh_scene()

    def is_overview(self):
        return not self.board.currentData()

    def board_changed(self, *_):
        self.cancel_placement()
        self.selected = None; self.selected_branch = None
        self.sync_grid()
        self.refresh()
        self._history_changed()
        QTimer.singleShot(0, self.fit_map)

    def overview_column_count(self):
        return max(1, min(4, self.graphics.viewport().width() // 300))

    def view_resized(self):
        if self._closed: return
        if self.is_overview() and self.overview_columns != self.overview_column_count():
            self.refresh()
            self.fit_map()
        elif self.graphics._fit_active:
            self.fit_map()

    def view_changed(self, *_):
        if self._closed: return
        self.cancel_placement()
        self.refresh()
        QTimer.singleShot(0, self.fit_map)

    def open_board(self, board):
        index = self.board.findData(board)
        if index >= 0: self.board.setCurrentIndex(index)

    def set_theme(self, theme):
        self.theme = theme
        for button, symbol in self.icon_bindings:
            button.setIcon(icon(symbol, UI_COLORS[theme]["text"]))
        self.refresh()

    def bind_icon(self, button, symbol):
        self.icon_bindings.append((button, symbol))
        button.setIcon(icon(symbol, UI_COLORS[self.theme]["text"]))

    def sync_grid(self):
        board = self.board.currentData()
        self.grid_choice.blockSignals(True)
        if board:
            self.grid_choice.setCurrentIndex(self.grid_choice.findData(self.store.board_grid(board)))
        self.grid_choice.setVisible(bool(board))
        self.grid_choice.setEnabled(bool(board) and not self.preview)
        self.grid_choice.blockSignals(False)

    def change_grid(self, *_):
        if self.preview or self.is_overview(): return
        self.cancel_placement()
        self.perform(lambda: self.store.set_board_grid(self.board.currentData(), self.grid_choice.currentData()))
        QTimer.singleShot(0, self.fit_map)

    def update_port_actions(self):
        editable = not self.preview and not self.is_overview()
        selected = self.store.port(self.selected) if self.selected else None
        multiple = len(self.selected_port_ids()) > 1
        self.occupied.setEnabled(editable and bool(selected))
        branch = self.occupied_branch.currentData() or ""
        marked = self.store.branch_marks(selected["id"]) if selected else set()
        can_draw = bool(not multiple and selected and selected["manual_status"] != "damaged" and (selected["manually_occupied"] or selected["sample_id"] or branch in marked))
        self.occupied_branch.setEnabled(editable and bool(not multiple and selected and (self.store.circuit(selected["id"]).get("switch") or can_draw)))
        self.branch_occupied.setEnabled(editable and bool(not multiple and selected and branch and not selected["manually_occupied"]))
        self.occupied_drawing.setEnabled(editable and can_draw)
        if not selected:
            self.occupied.blockSignals(True); self.occupied.setCurrentIndex(0); self.occupied.blockSignals(False)
        locked = bool(selected and selected["manually_occupied"])
        enabled = (bool(selected and not locked), bool(selected and selected["sample_id"] and not locked),
                   bool(selected and ((selected["legacy_json"] and selected["legacy_json"] != "{}") or selected["branch_count"])),
                   True, bool(selected), bool(selected and not locked), bool(selected))
        for button, allowed in zip(self.port_buttons, enabled):
            button.setEnabled(editable and allowed and (not multiple or button is self.port_buttons[3]))
        if multiple:
            removable, _ = self.store.unassign_preview(self.selected_port_ids())
            self.port_buttons[1].setEnabled(editable and bool(removable))

    def perform(self, operation):
        try:
            operation(); self.refresh_filters(); self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", str(exc)); self.refresh()

    def refresh_filters(self):
        ports, samples = self.store.ports(), self.store.samples()
        desired = set()
        for sample in samples:
            path = self.store.sample_path(sample["id"])
            if path.is_file(): desired.add(str(path))
            if path.parent.is_dir(): desired.add(str(path.parent))
        watched = set(self.sample_watcher.files() + self.sample_watcher.directories())
        if watched - desired: self.sample_watcher.removePaths(list(watched - desired))
        if desired - watched: self.sample_watcher.addPaths(list(desired - watched))
        usages = {display_line_type(p["usage"] or p["kind"]) for p in ports}
        usages.update(display_line_type(row["usage"]) for p in ports for row in self.store.branch_assignments(p["id"]))
        choices = [(self.board, "总览（只读）", self.store.boards()),
                   (self.sample, "全部样品", [(s["name"], s["id"]) for s in samples]),
                   (self.kind, "全部用途", sorted(usages)),
                   (self.status, "全部状态", ["未分配", "已占用", "已损坏"])]
        for combo, title, values in choices:
            current = combo.currentData(); combo.blockSignals(True); combo.clear(); combo.addItem(title, "")
            for value in values:
                text, data = value if isinstance(value, tuple) else (value, value)
                combo.addItem(text, data)
            combo.setCurrentIndex(max(0, combo.findData(current))); combo.blockSignals(False)
        self.sync_grid()

    def _samples_changed(self):
        if not self._closed:
            self.refresh_filters(); self.refresh()

    def filtered_ports(self):
        needle = self.search.text().strip().casefold()
        box_cache = {}
        for p in self.store.ports():
            branches = self.store.branch_assignments(p["id"])
            if self.board.currentData() and p["board"] != self.board.currentData(): continue
            if self.sample.currentData() and p["sample_id"] != self.sample.currentData() and not any(b["sample_id"] == self.sample.currentData() for b in branches): continue
            if self.kind.currentData() and display_line_type(p["usage"] or p["kind"]) != self.kind.currentData() and not any(display_line_type(b["usage"]) == self.kind.currentData() for b in branches): continue
            marked = self.store.branch_marks(p["id"])
            state = "已损坏" if p["manual_status"] == "damaged" else "已占用" if p["manually_occupied"] or p["sample_id"] or marked else "未分配"
            if self.status.currentData() == "未分配" and p["manually_occupied"]: continue
            if self.status.currentData() and state != self.status.currentData():
                switch = self.store.circuit(p["id"]).get("switch", [])
                if not (self.status.currentData() == "未分配" and len({b["branch"] for b in branches} | marked) < len(switch)): continue
            text = " ".join(str(p[k] or "") for k in ("board", "number", "kind", "sample_name", "pin", "drawing", "devices_json"))
            text += " " + " ".join(" ".join(str(b[k] or "") for k in ("branch", "sample_name", "pin", "drawing", "devices_json")) for b in branches)
            text += " " + display_line_type(p["kind"]) + " " + display_line_type(p["usage"] or "")
            if needle:
                for row in (p, *branches):
                    if row["box_port_id"]:
                        box, status, _ = self.store.resolve_assignment(row, box_cache)
                        text += " " + (self.box_description(box) if box else status)
            if needle and needle not in text.casefold(): continue
            yield p

    def refresh(self, *_):
        if self._closed: return
        selected_ids = set(self.selected_port_ids()) if self.views.currentIndex() == 0 else set()
        self.scene.blockSignals(True)
        ports = list(self.filtered_ports()); self.scene.clear()
        colors = UI_COLORS[self.theme]; diagram = DIAGRAM_COLORS[self.theme]
        self.scene.setBackgroundBrush(QBrush(QColor(diagram["paper"])))
        swatches = [("XY/Z 线", "XY/Z"), ("直流", "直流"), ("读入", "读取输入"), ("读出", "读取输出")]
        self.legend.setText("端口类型：" + "　".join(
            f"<span style='color:{port_color(kind, self.theme).name()}'>●</span> {label}" for label, kind in swatches)
            + "　｜　<span style='color:#00B8D9'>◎</span> 亮环：已分配　｜　<span style='color:#C2410C'>●</span> 占：手动已占用　｜　<span style='color:#B91C1C'>●</span> 损：已损坏")
        self.inspector.setVisible(not self.is_overview() or self.views.currentIndex() != 0)
        self.mode_hint.setText("新增端口 · 点击格点放置，Esc 取消" if self.placing_port else
                               "总览只读 · 双击端口进入单盘 · 右键拖动平移，滚轮缩放" if self.is_overview()
                               else "默认盘位预览 · 左键框选，右键拖动平移，滚轮缩放" if self.preview
                               else f"{self.board.currentText()}盘 · 左键点选、框选或拖动端口 · 右键拖动平移，滚轮缩放")
        self.overview_button.setEnabled(not self.is_overview())
        if not self.selected:
            self.info.setText("选择端口查看详情" if not self.is_overview() else "总览只读；双击端口进入对应盘。")
            self.circuit_info.setText("尚未选择端口")
        groups = {p["board"] for p in ports}
        if not any((self.sample.currentData(), self.kind.currentData(), self.status.currentData(), self.search.text().strip())):
            groups.update(self.store.boards() if self.is_overview() else [self.board.currentData()])
        groups = sorted(groups)
        offsets = {}
        if self.is_overview():
            self.overview_columns = self.overview_column_count()
            all_ports = self.store.ports()
            for index, board in enumerate(groups):
                x, y = (index % self.overview_columns) * 650, (index // self.overview_columns) * 650
                panel = self.scene.addRect(x - 280, y - 90, 560, 590, QPen(QColor(colors["border"])), QBrush(QColor(colors["panel"])))
                panel.setZValue(-1); panel.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                board_ports = [p for p in all_ports if p["board"] == board]
                if board_ports:
                    step_y = 58
                    min_x, max_x = min(p["x"] for p in board_ports), max(p["x"] for p in board_ports)
                    min_y, max_y = min(p["y"] for p in board_ports), max(p["y"] for p in board_ports)
                    offsets[board] = (x - (min_x + max_x) * 29,
                                      y + (460 - (max_y - min_y) * step_y) / 2 - min_y * step_y)
                else:
                    offsets[board] = (x, y)
                heading = self.scene.addSimpleText(f"{board} 盘"); heading.setPos(x - 20, y - 65)
                font = heading.font(); font.setPixelSize(24); heading.setFont(font)
                heading.setBrush(QBrush(QColor(diagram["ink"]))); heading.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        else:
            self.overview_columns = None
            offsets = {board: (0, 0) for board in groups}
            for board, (x, y) in offsets.items():
                heading = self.scene.addSimpleText(f"{board} 盘"); heading.setPos(x - 20, y - 65)
                font = heading.font(); font.setPixelSize(24); heading.setFont(font)
                heading.setBrush(QBrush(QColor(diagram["ink"]))); heading.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            if not ports:
                empty = self.scene.addRect(-260, -80, 520, 520, QPen(Qt.PenStyle.NoPen), QBrush(Qt.BrushStyle.NoBrush))
                empty.setZValue(-1); empty.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        for p in ports:
            item = PortItem(p, self, self.store.board_grid(p["board"])); x, y = offsets[p["board"]]
            item.setPos(item.pos().x() + x, item.pos().y() + y)
            # Coordinates in each board are local, including after dragging.
            item.board_offset = offsets[p["board"]]
            self.scene.addItem(item)
            if p["id"] in selected_ids: item.setSelected(True)
        self.scene.blockSignals(False)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-50, -40, 50, 40))
        headers, entries = self.store.table_entries(); allowed = {p["id"] for p in ports}
        entries = [entry for entry in entries if entry[0] in allowed]
        if self.sample.currentData():
            entries = [entry for entry in entries if (entry[1] is None or any(
                b["branch"] == entry[1] and b["sample_id"] == self.sample.currentData()
                for b in self.store.branch_assignments(entry[0])))]
        if self.kind.currentData():
            entries = [entry for entry in entries if (entry[2][4] or entry[2][2]) == self.kind.currentData()]
        if self.status.currentData():
            entries = [entry for entry in entries if entry[2][5] == self.status.currentData()]
        self.table.setColumnCount(len(headers)); self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(entries))
        for row, (port_id, branch, values) in enumerate(entries):
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, port_id)
                item.setData(Qt.ItemDataRole.UserRole + 1, branch); self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        if len(self.selected_port_ids()) > 1:
            self.show_multiple_selection()
        elif self.selected:
            try: self.show_info(self.store.port(self.selected))
            except StopIteration:
                self.selected = None; self.info.setText("选择端口查看详情")
        self.update_port_actions()

    def select_port(self, port_id):
        if self.selected != port_id: self.selected_branch = None
        self.selected = port_id; self.show_info(self.store.port(port_id)); self.update_port_actions()

    def selected_port_ids(self):
        if self.views.currentIndex() != 0: return []
        items = {item.port["id"]: item for item in self.scene.selectedItems() if isinstance(item, PortItem)}
        self._selection_order = [ident for ident in self._selection_order if ident in items]
        added = set(items) - set(self._selection_order)
        self._selection_order.extend(sorted(added, key=lambda ident: (items[ident].scenePos().y(), items[ident].scenePos().x(), ident)))
        return list(self._selection_order)

    def canvas_selection_changed(self):
        if self._closed or self.views.currentIndex() != 0: return
        ids = self.selected_port_ids()
        if not ids:
            self.selected = None; self.info.setText("选择端口查看详情"); self.circuit_info.setText("尚未选择端口")
        elif len(ids) == 1:
            self.select_port(ids[0]); return
        else:
            self.selected = ids[0]; self.selected_branch = None; self.show_multiple_selection()
        self.update_port_actions()

    def show_multiple_selection(self):
        ids = self.selected_port_ids()
        self.info.setText(f"已选择 {len(ids)} 个端口；选择状态可批量设置。")
        self.circuit_info.setText("批量设置不改变已有样品分配或图纸编号；可点击解除分配批量处理。")
        self.occupied.blockSignals(True); self.occupied.setCurrentIndex(-1)
        self.occupied.setPlaceholderText("选择要批量设置的状态"); self.occupied.blockSignals(False)

    def toggle_occupied(self, *_):
        if self.selected and not self.preview and not self.is_overview():
            ids = self.selected_port_ids() or [self.selected]
            status = self.occupied.currentData()
            if status is not None:
                self.perform(lambda: self.store.set_ports_status(ids, status))

    def show_occupied_drawing(self, *_):
        if not self.selected: return
        p = self.store.port(self.selected)
        branch = self.occupied_branch.currentData() or ""
        self.branch_occupied.blockSignals(True); self.branch_occupied.setChecked(branch in self.store.branch_marks(p["id"])); self.branch_occupied.blockSignals(False)
        values = json.loads(p["occupied_drawings"])
        self.occupied_drawing.setText(values.get(branch, "")); self.update_port_actions()

    def toggle_branch_occupied(self, checked):
        if self.selected and self.occupied_branch.currentData():
            self.perform(lambda: self.store.set_branch_occupied(self.selected, self.occupied_branch.currentData(), checked))

    def save_occupied_drawing(self):
        if not self.selected or not self.occupied_drawing.isEnabled(): return
        branch = self.occupied_branch.currentData() or ""
        value = self.occupied_drawing.text().strip()
        if json.loads(self.store.port(self.selected)["occupied_drawings"]).get(branch, "") != value:
            self.perform(lambda: self.store.set_occupied_drawing(self.selected, branch, value))

    def show_info(self, p):
        state = "damaged" if p["manual_status"] == "damaged" else "occupied" if p["manually_occupied"] or p["sample_id"] else ""
        self.occupied.blockSignals(True); self.occupied.setCurrentIndex(self.occupied.findData(state)); self.occupied.blockSignals(False)
        current = self.occupied_branch.currentData()
        self.occupied_branch.blockSignals(True); self.occupied_branch.clear(); self.occupied_branch.addItem("端口整体", "")
        for branch in self.store.circuit(p["id"]).get("switch", []): self.occupied_branch.addItem(f"分支 {branch}", branch)
        self.occupied_branch.setCurrentIndex(max(0, self.occupied_branch.findData(current)))
        self.occupied_branch.blockSignals(False); self.show_occupied_drawing()
        box_cache = {}
        direct = self.store.resolve_assignment(p, box_cache) if p["sample_id"] and not p["branch_count"] else (None, "", [])
        box, status, devices = direct
        endpoint = f"  样品盒接口：{box['number']} · {status}" if box else (
            f"  {status}" if status else f"  引脚：{p['pin'] or '—'}")
        switch = self.store.circuit(p["id"]).get("switch", [])
        title_state = "开关端口" if switch else p['sample_name'] or '空闲'
        self.info.setText(f"{p['board']} 盘 {p['number']} · {display_line_type(p['kind'])} · {title_state}"
                          + ("（样品不可用）" if p['sample_id'] and not self.store.sample_path(p['sample_id']).is_file() else "")
                          + (f" · {len({r['branch'] for r in self.store.branch_assignments(p['id'])} | self.store.branch_marks(p['id']))}/{len(switch)} 已占用" if switch else
                             f"{endpoint}  器件：{', '.join(d['label'] for d in devices) or '—'}"))
        circuit = self.store.circuit(p["id"])
        components = circuit.get("components", [])
        lines = []
        if box:
            lines.append(self.box_description(box))
        for device in devices:
            if device.get("kind") == "qubit":
                lines.append(f"{device['label']} 图纸编号：{device.get('drawing_number') or '未填写'}" + (f"（{device['status']}）" if device.get("status") else ""))
        assigned = {r["branch"]: r for r in self.store.branch_assignments(p["id"])}
        marked = self.store.branch_marks(p["id"])
        branches = circuit.get("switch", [])
        if branches: lines.insert(0, f"开关分支：{len(set(assigned) | marked)}/{len(branches)} 已占用")
        for name in branches:
            branch = assigned.get(name)
            state = "整端口已损坏" if p["manual_status"] == "damaged" else "整端口已占用" if p["manually_occupied"] else "手动占用" if name in marked else "已分配" if branch else "未分配"
            drawing = json.loads(p["occupied_drawings"]).get(name, "")
            if not branch:
                lines.append(f"分支 {name} · {state}" + (f" · 图纸：{drawing}" if drawing else "")); continue
            missing = "（样品不可用）" if not self.store.sample_path(branch["sample_id"]).is_file() else ""
            lines.append(f"分支 {name} · {state} · {branch['sample_name']}{missing} · {display_line_type(branch['usage'])}" + (f" · 图纸：{drawing}" if drawing else ""))
            box, status, devices = self.store.resolve_assignment(branch, box_cache)
            if box or status:
                lines.append(f"  {self.box_description(box) + ' · ' if box else ''}{status}")
            for device in devices:
                if device.get("kind") == "qubit":
                    lines.append(f"  {device['label']} 图纸编号：{device.get('drawing_number') or '未填写'}" + (f"（{device['status']}）" if device.get("status") else ""))
        for stage in dict.fromkeys(["3 K", "STILL", "50 mK", "MC"] + [c.get("stage", "") for c in components]):
            values = [component_text(c) for c in components if c.get("stage") == stage]
            if values: lines.append(f"{stage}  {' · '.join(values)}")
        if circuit.get("switch"): lines.append("微波开关  " + " / ".join(circuit["switch"]))
        self.circuit_info.setText("\n".join(lines) if lines else "低温线路：未登记")

    @staticmethod
    def box_description(box):
        return " · ".join(filter(None, (f"样品盒接口 {box['number']}", box_number_source(box),
                                        box["signal_name"], box["device_label"],
                                        box["device_subtype"] if box["device_kind"] == "custom" else "",
                                        BOX_ROLES.get(box["role"], ""))))

    def table_selected(self, row, _col):
        item = self.table.item(row, 0); self.select_port(item.data(Qt.ItemDataRole.UserRole))
        self.selected_branch = item.data(Qt.ItemDataRole.UserRole + 1)

    def table_activated(self, row, _col):
        self.table_selected(row, _col)
        if self.is_overview(): self.open_board(self.store.port(self.selected)["board"])
        else: self.edit_assignment()

    def fit_map(self):
        if self._closed or self._fitting:
            return
        self._fitting = True
        try:
            self._fit_map()
        finally:
            self._fitting = False

    def _fit_map(self):
        if not self.scene.itemsBoundingRect().isEmpty():
            self.graphics._fit_active = True
            bounds = self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
            if self.is_overview():
                self.graphics.resetTransform()
                scale = max(0.08, min(8.0, (self.graphics.viewport().width() - 16) / bounds.width()))
                self.graphics.scale(scale, scale)
                self.graphics._extend_scene()
                visible_height = self.graphics.viewport().height() / scale
                center_y = bounds.center().y() if bounds.height() <= visible_height else bounds.top() + visible_height / 2
                self.graphics.centerOn(bounds.center().x(), center_y)
            else:
                self.graphics.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)
            self.graphics._extend_scene()

    def chosen(self):
        if not self.selected:
            QMessageBox.information(self, "选择端口", "请先选择一个端口。")
            return None
        return self.store.port(self.selected)

    def add_sample(self):
        path, _ = QFileDialog.getOpenFileName(self, "关联样品", "", "芯片文件 (*.qchip)")
        if path: self.perform(lambda: self.store.add_sample(path))

    def add_board(self):
        if self.preview: return
        existing = set(self.store.boards())
        default = next((chr(code) for code in range(ord("A"), ord("Z") + 1) if chr(code) not in existing), "")
        dialog = BoardDialog(self, default)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                name = dialog.name.text().strip()
                self.store.create_board(name, dialog.template.currentData(), dialog.with_preset.isChecked())
                for combo in (self.sample, self.kind, self.status): combo.setCurrentIndex(0)
                self.search.clear()
                self.refresh_filters(); self.open_board(name)
                return
            except Exception as exc:
                QMessageBox.warning(dialog, "创建失败", str(exc))

    def choose_sample(self, title):
        samples = self.store.samples()
        if not samples:
            QMessageBox.information(self, title, "请先关联样品。 "); return None
        labels = [f"{s['name']} · {s['path']}" + (" （文件不可用）" if not self.store.sample_path(s["id"]).is_file() else "") for s in samples]
        label, ok = QInputDialog.getItem(self, title, "选择样品", labels, 0, False)
        return samples[labels.index(label)] if ok else None

    def relocate_sample(self):
        sample = self.choose_sample("重新定位样品")
        if sample:
            path, _ = QFileDialog.getOpenFileName(self, "选择原样品文件", "", "芯片文件 (*.qchip)")
            if path: self.perform(lambda: self.store.relocate_sample(sample["id"], path))

    def remove_sample(self):
        sample = self.choose_sample("移除样品")
        if sample and QMessageBox.question(self, "移除样品", "移除该样品及其所有端口分配？样品文件不会被删除。") == QMessageBox.StandardButton.Yes:
            self.perform(lambda: self.store.remove_sample(sample["id"]))

    def edit_assignment(self):
        if self.preview or self.is_overview(): return
        p = self.chosen()
        if not p: return
        if p["manually_occupied"]:
            QMessageBox.information(self, "端口已占用", "请先取消已占用标记，再修改分配。")
            return
        if not self.store.samples():
            QMessageBox.information(self, "分配端口", "请先关联样品。"); return
        branch = self.choose_branch(p)
        if self.store.circuit(p["id"]).get("switch") and branch is None: return
        dialog = AssignmentDialog(self, self.store, p, branch)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                usage = line_type_value(dialog.usage, dialog.original_usage)
                box_port_id = dialog.box_port.currentData() if dialog.mode.currentData() == "box" else None
                if dialog.mode.currentData() == "box" and box_port_id is None:
                    raise ValueError("请选择样品盒接口")
                self.store.assign(p["id"], dialog.sample.currentData(), dialog.pin.text(), dialog.drawing.text(),
                                  dialog.device_ids(), dialog.notes.text(), usage, branch=branch, box_port_id=box_port_id)
                self.refresh_filters(); self.refresh(); return
            except Exception as exc:
                dialog.error.setText(str(exc))

    def unassign(self):
        if self.preview or self.is_overview(): return
        ids = self.selected_port_ids()
        if len(ids) > 1:
            removable, protected = self.store.unassign_preview(ids)
            if not removable:
                QMessageBox.information(self, "批量解除分配", "所选端口没有可解除的分配；手动占用或损坏的端口及分支会保留。")
                return
            def describe(row):
                branch = f" / 分支 {row['branch']}" if row['branch'] is not None else ""
                return f"{row['board']}{row['number']}{branch} · {row['sample']}"
            message = f"将解除 {len(removable)} 条样品分配：\n" + "\n".join(describe(row) for row in removable)
            if protected:
                message += f"\n\n{len(protected)} 条受手动占用或损坏标记保护，将跳过：\n" + "\n".join(describe(row) for row in protected)
            if any(row["legacy"] for row in removable):
                message += "\n\n其中包含迁移原记录，解除时会一并移除，可通过撤回恢复。"
            message += "\n\n物理低温线路和手动标记会保留。继续？"
            if QMessageBox.question(self, "批量解除分配", message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self.perform(lambda: self.store.unassign_many([(row["port_id"], row["branch"]) for row in removable]))
            return
        p = self.chosen()
        if not p or not p["sample_id"]: return
        branch = self.choose_branch(p, assigned_only=True)
        if self.store.circuit(p["id"]).get("switch") and branch is None: return
        if QMessageBox.question(self, "解除分配", "解除所选样品分配？物理低温线路会保留。") == QMessageBox.StandardButton.Yes:
            self.perform(lambda: self.store.unassign(p["id"], branch))

    def choose_branch(self, port, assigned_only=False):
        branches = self.store.circuit(port["id"]).get("switch", [])
        if not branches: return None
        assigned = {r["branch"]: r for r in self.store.branch_assignments(port["id"])}
        marked = self.store.branch_marks(port["id"])
        options = [branch for branch in branches if branch not in marked and (not assigned_only or branch in assigned)]
        if not options: return None
        if self.selected_branch in options: return self.selected_branch
        labels = [f"{branch} · {assigned[branch]['sample_name']}" if branch in assigned else f"{branch} · 空闲" for branch in options]
        label, ok = QInputDialog.getItem(self, "微波开关分支", "选择分支", labels, 0, False)
        return options[labels.index(label)] if ok else None

    def edit_circuit(self):
        if self.preview or self.is_overview(): return
        p = self.chosen()
        if p and CircuitDialog(self.store, p, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh_filters(); self.refresh()

    def apply_preset(self):
        if self.preview: return
        if PresetDialog(self.store, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh_filters(); self.refresh()

    def check_wiring_types(self):
        count, mismatches = self.store.check_wiring_types()
        title = "接线类型检查"
        if not mismatches:
            message = f"已检查 {count} 条分配，线路用途均与端口类型兼容。" if count else "当前项目没有已分配的线路。"
            QMessageBox.information(self, title, message)
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(680, 360)
        layout = QVBoxLayout(dialog)
        summary = QLabel(f"已检查 {count} 条分配，发现 {len(mismatches)} 条类型不匹配。\n"
                         "XY/Z 端口兼容 XY/Z 和 Z 线路，其余端口要求类型一致。")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        columns = ("盘号", "端口", "开关分支", "端口类型", "线路用途", "样品")
        table = QTableWidget(len(mismatches), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        for index, row in enumerate(mismatches):
            values = (row["board"], row["number"], row["branch"] or "—",
                      display_line_type(row["kind"].strip()) or "未登记",
                      display_line_type(row["usage"].strip()) or "未登记", row["sample_name"])
            for column, value in enumerate(values):
                table.setItem(index, column, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()
        dialog.deleteLater()

    def view_legacy(self):
        if self.preview or self.is_overview(): return
        p = self.chosen()
        if not p: return
        branch = self.choose_branch(p, assigned_only=True)
        if self.store.circuit(p["id"]).get("switch") and branch is None: return
        rows = self.store.branch_assignments(p["id"])
        raw = next((r["legacy_json"] for r in rows if r["branch"] == branch), "{}") if branch else p["legacy_json"] or "{}"
        legacy = json.loads(raw)
        if not legacy:
            QMessageBox.information(self, "迁移原记录", "该端口没有迁移原记录。")
            return
        dialog = QDialog(self); dialog.setWindowTitle("迁移原记录（只读）"); dialog.resize(700, 500)
        layout = QVBoxLayout(dialog); view = QPlainTextEdit(); view.setReadOnly(True)
        view.setPlainText(json.dumps(legacy, ensure_ascii=False, indent=2)); layout.addWidget(view)
        dialog.exec()

    def port_dialog(self, port=None, position=None):
        if self.preview or self.is_overview(): return
        dialog = PortDialog(self, port)
        if not port:
            board = self.board.currentData(); dialog.board.setText(board); dialog.board.setReadOnly(True)
            dialog.number.setText(self.store.next_port_number(board))
            kinds = [p["kind"] for p in self.store.ports() if p["board"] == board]
            if kinds: dialog.kind.setCurrentText(display_line_type(Counter(kinds).most_common(1)[0][0]))
            if position:
                dialog.x.setValue(position[0]); dialog.y.setValue(position[1])
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                x, y = dialog.position()
                self.selected = self.store.save_port(port["id"] if port else None, dialog.board.text(), dialog.number.text(),
                    x, y, line_type_value(dialog.kind, dialog.original_kind))
                self.refresh_filters(); self.refresh(); return
            except Exception as exc: QMessageBox.warning(dialog, "保存失败", str(exc))

    def add_port(self):
        if self.preview or self.is_overview(): return
        self.cancel_placement()
        self.views.setCurrentIndex(0)
        self.placing_port = True; self.cancel_action.setEnabled(True)
        self.graphics.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.graphics.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.graphics.placement_callback = self.place_port
        self.mode_hint.setText("新增端口 · 点击格点放置，Esc 取消")

    def cancel_placement(self):
        if not self.placing_port: return
        self.placing_port = False; self.cancel_action.setEnabled(False)
        self.graphics.placement_callback = None
        self.graphics.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.graphics.viewport().unsetCursor()
        if not self._closed and not self.is_overview():
            self.mode_hint.setText(f"{self.board.currentText()}盘 · 左键点选、框选或拖动端口 · 右键拖动平移，滚轮缩放")

    def place_port(self, point):
        board = self.board.currentData()
        grid = self.store.board_grid(board)
        x, y = snap_to_grid(point.x() / 58, point.y() / 58, grid)
        self.cancel_placement()
        QTimer.singleShot(0, lambda: self.port_dialog(position=(x, y)) if not self._closed else None)

    def edit_port(self):
        p = self.chosen()
        if p: self.port_dialog(p)

    def delete_port(self):
        if self.preview or self.is_overview(): return
        p = self.chosen()
        if p and QMessageBox.question(self, "删除端口", "删除此端口及其线路登记？") == QMessageBox.StandardButton.Yes:
            self.perform(lambda: self.store.delete_port(p["id"])); self.selected = None

    def undo(self):
        self.cancel_placement()
        if not self.is_overview(): self.perform(self.history.undo)
    def redo(self):
        self.cancel_placement()
        if not self.is_overview(): self.perform(self.history.redo)

    def migrate(self):
        sample = self.choose_sample("迁移旧接线")
        if not sample: return
        parent_store = getattr(self.host, "store", None)
        if parent_store and Path(parent_store.path).resolve() == self.store.sample_path(sample["id"]):
            QMessageBox.warning(self, "请先关闭样品", "此样品正在主窗口中打开。请先打开其他芯片，或重新启动软件后直接打开制冷机项目，再执行迁移。")
            return
        try: preview = self.store.migration_preview(sample["id"])
        except Exception as exc:
            QMessageBox.warning(self, "无法读取旧接线", str(exc)); return
        if not preview:
            QMessageBox.information(self, "迁移旧接线", "该样品没有旧接线记录。"); return
        dialog = QDialog(self); dialog.setWindowTitle("预览并选择旧接线"); dialog.resize(900, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("勾选线路并选择目标端口；同一端口最多选择一条。未选中的记录保留在样品中。请先关闭其他程序中的样品文件。"))
        table = QTableWidget(len(preview), 8); layout.addWidget(table)
        table.setHorizontalHeaderLabels(["迁移", "原盘号", "原端口", "用途", "目标端口", "说明", "关联器件", "开关分支"])
        available = [p for p in self.store.ports() if not p["manually_occupied"] and (
                     (not self.store.circuit(p["id"]).get("switch") and not p["sample_id"]) or
                     (set(self.store.circuit(p["id"]).get("switch", [])) - {b["branch"] for b in self.store.branch_assignments(p["id"])} - self.store.branch_marks(p["id"])))]
        for row, item in enumerate(preview):
            check = QTableWidgetItem(); check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if item["reason"] == "可迁移" else Qt.CheckState.Unchecked)
            table.setItem(row, 0, check)
            for col, value in ((1, item["details"].get("board", "")), (2, item["line"]["port"]),
                               (3, display_line_type(item["line"]["line_type"])), (5, item["reason"]),
                               (6, ", ".join(d["label"] for d in item["devices"]))):
                cell = QTableWidgetItem(str(value)); cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable); table.setItem(row, col, cell)
            choice = QComboBox(); choice.addItem("请选择", "")
            for p in available:
                choice.addItem(f"{p['board']} · {p['number']} · {display_line_type(p['kind'])}", p["id"])
            choice.setCurrentIndex(max(0, choice.findData(item["port_id"]))); table.setCellWidget(row, 4, choice)
            branch_choice = QComboBox(); table.setCellWidget(row, 7, branch_choice)
            def update_branches(_index=0, source=choice, target=branch_choice):
                target.clear(); target.addItem("请选择", "")
                port_id = source.currentData()
                if not port_id: return
                used = {r["branch"] for r in self.store.branch_assignments(port_id)} | self.store.branch_marks(port_id)
                for branch in self.store.circuit(port_id).get("switch", []):
                    if branch not in used: target.addItem(branch, branch)
            choice.currentIndexChanged.connect(update_branches); update_branches()
        table.resizeColumnsToContents(); buttons(dialog, layout, "迁移所选")
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        chosen = {}
        for i, p in enumerate(preview):
            if table.item(i, 0).checkState() != Qt.CheckState.Checked: continue
            port_id = table.cellWidget(i, 4).currentData(); branch = table.cellWidget(i, 7).currentData() or None
            if port_id and self.store.circuit(port_id).get("switch") and not branch:
                QMessageBox.warning(self, "迁移失败", "请为开关线路选择分支。"); return
            chosen[p["line"]["id"]] = (port_id, branch) if port_id else ""
        if not chosen: return
        if not all(chosen.values()):
            QMessageBox.warning(self, "迁移失败", "请为勾选的线路选择目标端口。"); return
        try:
            backup = self.store.migrate_lines(sample["id"], chosen); self.refresh_filters(); self.refresh()
            QMessageBox.information(self, "迁移完成", f"已迁移 {len(chosen)} 条线路。\n样品备份：{backup}\n迁移不支持撤回；恢复样品备份后，请检查并处理制冷机项目中的对应分配。")
        except Exception as exc: QMessageBox.warning(self, "迁移失败", str(exc))

    def export_table(self):
        path, selected = QFileDialog.getSaveFileName(self, "导出布线表", "wiring.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if path:
            if Path(path).suffix.lower() not in (".csv", ".xlsx"):
                path += ".csv" if "CSV" in selected else ".xlsx"
            self.perform(lambda: self.store.export_table(path))

    def export_bundle(self):
        path, _ = QFileDialog.getSaveFileName(self, "打包导出", "fridge.zip", "ZIP (*.zip)")
        if path:
            if not path.lower().endswith(".zip"): path += ".zip"
            self.perform(lambda: self.store.export_bundle(path))

    def closeEvent(self, event):
        if not self._closed:
            self.cancel_placement()
            self._closed = True
            self.graphics._resize_callback = None
            self.history.detach(); self.store.close()
        super().closeEvent(event)


# Kept as an import alias for callers that instantiate the workspace by itself.
FridgeWindow = FridgePanel

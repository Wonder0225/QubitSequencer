"""Graphic front-panel view for a chip's sample box."""

import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QFont, QFontMetrics, QImage, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen, QTransform
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGraphicsItem, QGraphicsScene,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from .device_types import DEVICE_NAMES
from .exports import (export_sample_box_wiring, export_sample_box_wiring_matrix,
                      export_sample_box_wiring_pdf, sample_box_wiring_blocks)
from .fridge_circuit_ui import component_text
from .fridge_ui import PortView
from .sample_box_store import BOX_ROLES, box_connection_label, box_number_source
from .sample_box_templates import DEFAULT_TEMPLATE, load_sample_box_templates
from .theme import DIAGRAM_COLORS, UI_COLORS, custom_device_colors
from .undo_ui import install_form_undo
from .ui_icons import icon


def sample_box_template_directory():
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    return Path(root) / "QubitSequencer" / "sample-box-templates"


def wiring_export_stem(sample_name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", sample_name).strip(" .")
    return f"接线表-{name}" if name else "接线表"


@dataclass(frozen=True)
class SampleBoxDiagram:
    name: str
    appearance: dict
    ports: dict
    connections: dict
    connection_state: str
    connections_available: bool
    rotation: int
    custom_subtypes: list


class BatchWiringExportDialog(QDialog):
    def __init__(self, fridge, parent):
        super().__init__(parent)
        self.setWindowTitle("批量导出样品盒接线表")
        self.resize(550, 390)
        layout = QVBoxLayout(self)
        title = QLabel(f"制冷机：{fridge.name()}")
        title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)
        self.samples = QListWidget()
        for sample in fridge.samples():
            ident = sample["id"]
            path = fridge.sample_path(ident)
            state = ""
            if not path.is_file():
                state = "样品文件不可用"
            elif not fridge.box_connections(ident):
                state = "没有已分配的样品盒接线"
            label = f"{sample['name']} · {path.name}"
            item = QListWidgetItem(f"{label} · {state}" if state else label, self.samples)
            item.setToolTip(str(path))
            if state:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setData(Qt.ItemDataRole.UserRole, ident)
                item.setCheckState(Qt.CheckState.Checked)
        layout.addWidget(self.samples, 1)
        selection = QHBoxLayout()
        for text, checked in (("全选", True), ("全不选", False)):
            button = QPushButton(text)
            button.clicked.connect(lambda _, enabled=checked: self.set_selected(enabled))
            selection.addWidget(button)
        selection.addStretch()
        layout.addLayout(selection)
        form = QFormLayout()
        self.formats = QComboBox()
        for label, formats in (("PDF", ("pdf",)), ("Excel", ("xlsx",)), ("Excel 和 PDF", ("xlsx", "pdf"))):
            self.formats.addItem(label, formats)
        form.addRow("导出格式", self.formats)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save.setText("选择导出目录")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.samples.itemChanged.connect(lambda _: self.save.setEnabled(bool(self.sample_ids())))
        self.save.setEnabled(bool(self.sample_ids()))
        layout.addWidget(buttons)

    def sample_ids(self):
        return [item.data(Qt.ItemDataRole.UserRole) for index in range(self.samples.count())
                if (item := self.samples.item(index)).checkState() == Qt.CheckState.Checked]

    def set_selected(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.samples.count()):
            item = self.samples.item(index)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(state)


def _fit_label(label, width, height):
    """Fit a single line inside its allotted space and return its scene size."""
    font = label.font()
    font.setPixelSize(max(1, round(height)))
    label.setFont(font)
    bounds = label.boundingRect()
    scale = min(width / max(1, bounds.width()), height / bounds.height())
    label.setScale(scale)
    return bounds.width() * scale, bounds.height() * scale


class BoxPortDialog(QDialog):
    def __init__(self, parent, port, devices):
        super().__init__(parent)
        self.setWindowTitle("编辑样品盒接口")
        layout = QFormLayout(self)
        self.number = QLineEdit(port["number"])
        self.signal = QLineEdit(port["signal_name"])
        self.verified = QCheckBox("已与实物核对"); self.verified.setChecked(bool(port["verified"]))
        self.device = QComboBox(); self.device.addItem("未关联元件", None)
        self.kinds = {row["id"]: row["kind"] for row in devices}
        for row in devices:
            kind = row["subtype"] if row["kind"] == "custom" else DEVICE_NAMES[row["kind"]]
            self.device.addItem(f"{row['label']} · {kind}", row["id"])
        self.device.setCurrentIndex(max(0, self.device.findData(port["device_id"])))
        self.role = QComboBox()
        for role in ("read_in", "read_out"):
            self.role.addItem(BOX_ROLES[role], role)
        self.role.setCurrentIndex(max(0, self.role.findData(port["role"])))
        self.device.currentIndexChanged.connect(self.update_role)
        layout.addRow("接口编号", self.number)
        layout.addRow("编号来源", QLabel(box_number_source(port)))
        layout.addRow("信号名称", self.signal)
        layout.addRow(self.verified)
        layout.addRow("关联元件", self.device)
        layout.addRow("读取线方向", self.role)
        self.error = QLabel(); self.error.setWordWrap(True); layout.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.update_role()
        install_form_undo(self)

    def update_role(self):
        self.role.setEnabled(self.kinds.get(self.device.currentData()) == "readout")


class SampleBoxPanel(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.store = None
        self.ports = {}
        self.items = {}
        self.connections = {}
        self.connection_state = "未打开制冷机项目"
        self.connections_available = False
        self.selected = None
        self.template_id = None
        self.appearance = None
        self.rotation = 0
        self.template_directory = sample_box_template_directory()
        self.templates, errors = load_sample_box_templates(self.template_directory)
        layout = QVBoxLayout(self)
        template_bar = QHBoxLayout(); layout.addLayout(template_bar)
        self.template_combo = QComboBox(); self.template_combo.setToolTip("选择样品盒模板")
        for key, template in self.templates.items():
            self.template_combo.addItem(template.name, key)
        self.template_combo.setCurrentIndex(max(0, self.template_combo.findData(DEFAULT_TEMPLATE)))
        self.template_combo.currentIndexChanged.connect(self.update_template_action)
        template_bar.addWidget(self.template_combo, 1)
        self.enable_button = QPushButton("应用模板")
        self.enable_button.clicked.connect(self.enable_box); template_bar.addWidget(self.enable_button)
        folder = QPushButton("打开模板目录"); folder.setToolTip("放入 JSON 模板后重启软件即可加载")
        folder.clicked.connect(self.open_template_directory); template_bar.addWidget(folder)
        if errors:
            report = QPushButton("模板读取提示")
            report.clicked.connect(lambda: QMessageBox.warning(self, "模板读取提示", "\n".join(errors)))
            template_bar.addWidget(report)
        bar = QHBoxLayout(); layout.addLayout(bar)
        self.search = QLineEdit(); self.search.setPlaceholderText("查找接口号或元件名")
        self.search.setClearButtonEnabled(True); bar.addWidget(self.search, 1)
        self.matches = QComboBox(); self.matches.setMinimumWidth(135); bar.addWidget(self.matches)
        self.search.textChanged.connect(self.refresh_matches)
        self.matches.currentIndexChanged.connect(self.locate_match)
        self.display_button = QPushButton("显示选项")
        display_menu = QMenu(self.display_button)
        display_menu.setToolTipsVisible(True)
        self.display_button.setMenu(display_menu)
        bar.addWidget(self.display_button)
        self.show_names = display_menu.addAction("显示元件名称")
        self.show_names.setCheckable(True)
        self.show_names.setChecked(True)
        self.show_names.toggled.connect(self.draw_panel)
        self.smart_wiring = display_menu.addAction("智能接线")
        self.smart_wiring.setCheckable(True)
        self.smart_wiring.setToolTip("显示当前制冷机项目中的端口和开关分支")
        self.smart_wiring.toggled.connect(self.change_wiring_mode)
        self.hide_unassigned = display_menu.addAction("隐去未分配")
        self.hide_unassigned.setCheckable(True)
        self.hide_unassigned.setEnabled(False)
        self.hide_unassigned.setToolTip("关联制冷机项目后，只显示已分配制冷机线路的接口")
        self.hide_unassigned.toggled.connect(self.refresh_display)
        self.rotate_left = QPushButton("逆时针 90°"); self.rotate_left.clicked.connect(lambda: self.rotate_box(-90))
        self.rotate_right = QPushButton("顺时针 90°"); self.rotate_right.clicked.connect(lambda: self.rotate_box(90))
        for button, symbol in ((self.rotate_left, "undo"), (self.rotate_right, "redo")):
            button.setIcon(icon(symbol, UI_COLORS[self.host.theme]["text"]))
            self.host._icon_bindings.append((button, symbol)); bar.addWidget(button)
        self.fit = QPushButton("适配视图"); self.fit.clicked.connect(self.fit_map); bar.addWidget(self.fit)
        self.export_button = QPushButton("导出样品盒")
        self.export_button.setIcon(icon("export", UI_COLORS[self.host.theme]["text"]))
        self.host._icon_bindings.append((self.export_button, "export"))
        export_menu = QMenu(self.export_button)
        image_action = export_menu.addAction("导出样品盒图片", self.export_dialog)
        table_action = export_menu.addAction("导出接线表", self.export_table_dialog)
        self.batch_export_action = export_menu.addAction("批量导出接线表", self.export_batch_dialog)
        bundle_action = export_menu.addAction("接线大礼包", self.export_bundle_dialog)
        self.single_export_actions = (image_action, table_action, bundle_action)
        self.export_button.setMenu(export_menu)
        bar.addWidget(self.export_button)

        splitter = QSplitter(); layout.addWidget(splitter, 1)
        self.view = PortView(); self.scene = QGraphicsScene(self); self.view.setScene(self.scene)
        self.view._resize_callback = self.fit_if_active
        self.scene.selectionChanged.connect(self.canvas_selected)
        splitter.addWidget(self.view)
        inspector = QWidget(); inspector.setMinimumWidth(245); inspector.setMaximumWidth(380)
        details = QVBoxLayout(inspector); splitter.addWidget(inspector); splitter.setStretchFactor(0, 1)
        self.title = QLabel("样品盒构型"); self.title.setProperty("role", "heading"); details.addWidget(self.title)
        self.info = QLabel(); self.info.setTextFormat(Qt.TextFormat.PlainText); self.info.setWordWrap(True)
        details.addWidget(self.info)
        self.edit_button = QPushButton("编辑接口"); self.edit_button.clicked.connect(self.edit_port)
        details.addWidget(self.edit_button)
        self.trace_button = QPushButton("定位制冷机线路"); self.trace_button.clicked.connect(self.locate_fridge)
        details.addWidget(self.trace_button)
        legend = QLabel("实线 · 照片刻字或已核对\n虚线 · 对称推断\n彩色填充 · 已关联元件")
        legend.setProperty("role", "muted"); details.addWidget(legend); details.addStretch()

    def refresh(self):
        old_store, self.store = self.store, self.host.store
        self.ports = {row["id"]: row for row in self.store.sample_box_ports()} if self.store else {}
        previous_template = self.template_id
        self.template_id = self.store.sample_box_template() if self.store else None
        self.appearance = self.store.sample_box_appearance() if self.ports else None
        if old_store is not self.store or self.selected not in self.ports:
            self.selected = None
        if old_store is not self.store or previous_template != self.template_id:
            index = self.template_combo.findData(self.template_id or DEFAULT_TEMPLATE)
            self.template_combo.setCurrentIndex(max(0, index) if not self.template_id else index)
        self.template_combo.setEnabled(self.store is not None)
        self.update_template_action()
        self.search.setEnabled(bool(self.ports)); self.fit.setEnabled(bool(self.ports))
        self.rotate_left.setEnabled(bool(self.ports)); self.rotate_right.setEnabled(bool(self.ports))
        self.display_button.setEnabled(bool(self.ports))
        self.refresh_connections()
        if old_store is not self.store or self.view._fit_active:
            self.fit_map()

    def refresh_connections(self):
        self.connections = {}
        self.connection_state = "未打开制冷机项目"
        self.connections_available = False
        fridge = self.host.fridge_panel
        batch_available = fridge is not None and not fridge.preview
        self.batch_export_action.setEnabled(batch_available)
        self.export_button.setEnabled(bool(self.ports) or batch_available)
        for action in self.single_export_actions:
            action.setEnabled(bool(self.ports))
        if self.store and batch_available:
            self.connection_state = "当前样品未关联到制冷机项目"
            path = self.store.path.resolve()
            sample = next((s for s in fridge.store.samples() if fridge.store.sample_path(s["id"]) == path), None)
            if sample:
                self.connections_available = True
                self.connections = {row["box_port_id"]: row for row in fridge.store.box_connections(sample["id"])}
                self.connection_state = f"制冷机：{fridge.store.name()} · 已连接 {len(self.connections)} 个接口"
        self.hide_unassigned.setEnabled(bool(self.ports) and self.connections_available)
        self.refresh_display()

    def refresh_display(self):
        self.draw_panel()
        self.refresh_matches()
        self.show_port()

    def change_wiring_mode(self, *_):
        self.draw_panel(); self.fit_map()

    def draw_panel(self):
        selected_ids = {item.data(0) for item in self.scene.selectedItems() if item.data(0)}
        self.scene.blockSignals(True)
        self.scene.clear()
        self.items = self._populate_scene(
            self.scene, smart_wiring=self.smart_wiring.isChecked(), show_names=self.show_names.isChecked(),
            hide_unassigned=self.connections_available and self.hide_unassigned.isChecked(),
        )
        for ident in selected_ids or {self.selected}:
            if ident in self.items:
                self.items[ident].setSelected(True)
        if self.selected not in self.items:
            self.selected = None
        self.scene.blockSignals(False)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-18, -18, 18, 18))

    def _current_diagram(self):
        return SampleBoxDiagram(self.store.meta()["chip_name"], self.appearance, self.ports, self.connections,
                                self.connection_state, self.connections_available, self.rotation,
                                [device["subtype"] for device in self.store.devices("custom")])

    def _populate_scene(self, scene, *, smart_wiring, show_names, hide_unassigned, only_connected_captions=False,
                        diagram=None):
        items = {}
        theme = self.host.theme
        colors = DIAGRAM_COLORS[theme]; ui = UI_COLORS[theme]
        scene.setBackgroundBrush(QColor(colors["paper"]))
        if diagram is None and self.ports:
            diagram = self._current_diagram()
        if diagram is not None:
            drawing = diagram.appearance["drawing"]
            vertices = drawing["outline"]
            outline = QPainterPath(QPointF(*vertices[0]))
            for x, y in vertices[1:]:
                outline.lineTo(x, y)
            outline.closeSubpath()
            center = outline.boundingRect().center()
            spread = 1.65 if smart_wiring else 1.0
            rotation = QTransform().translate(center.x(), center.y()).rotate(diagram.rotation).scale(spread, spread).translate(-center.x(), -center.y())
            outline = rotation.map(outline)
            scene.addPath(outline, QPen(QColor(ui["border"]), 2), QBrush(QColor(ui["panel"])))
            if drawing["cover"]:
                cover = QPainterPath(); cover.addRect(*drawing["cover"])
                scene.addPath(rotation.map(cover), QPen(QColor(ui["border"]), 2), QBrush(QColor(ui["toolbar"])))
            for x, y, radius in drawing["holes"]:
                point = rotation.map(QPointF(x, y)); x, y = point.x(), point.y()
                scene.addEllipse(x-radius, y-radius, radius*2, radius*2,
                                 QPen(QColor(ui["border"])), QBrush(QColor(colors["paper"])))
            origin_x, origin_y = drawing["origin"]
            pitch, radius = drawing["pitch"], drawing["radius"]
            # Rotation is in 90-degree steps, so the grid spacing applies on both axes.
            spacing = pitch * spread
            gap = min(2, spacing * .03)
            caption_width = spacing - 2 * gap
            caption_bottom = spacing - radius - gap
            fill = {"qubit": colors["x_check"], "coupler": colors["coupler"], "readout": colors["data"],
                    "filter": colors["z_check"]}
            custom_colors = custom_device_colors(diagram.custom_subtypes)
            for ident, port in diagram.ports.items():
                connection = diagram.connections.get(ident)
                if hide_unassigned and connection is None:
                    continue
                empty_port = (smart_wiring and diagram.connections_available
                              and connection is None and not port["device_id"])
                x, y = origin_x + port["x"] * pitch, origin_y + port["y"] * pitch
                point = rotation.map(QPointF(x, y)); x, y = point.x(), point.y()
                inferred = port["source"] == "inferred" and not port["verified"]
                pen = QPen(QColor(colors["muted"]), 1.6, Qt.PenStyle.DashLine if inferred else Qt.PenStyle.SolidLine)
                subtype = port["device_subtype"] if port["device_kind"] == "custom" else ""
                color = custom_colors[subtype] if subtype else fill.get(port["device_kind"], colors["paper"])
                number_color = "#15181E" if port["device_kind"] else colors["ink"]
                if only_connected_captions and connection is None:
                    color, number_color = colors["paper"], colors["ink"]
                elif empty_port:
                    color = DIAGRAM_COLORS["dark"]["paper"]
                    number_color = DIAGRAM_COLORS["dark"]["ink"]
                brush = QBrush(QColor(color))
                item = scene.addEllipse(-radius, -radius, radius*2, radius*2, pen, brush)
                item.setPos(x, y); item.setData(0, ident)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
                name = port["device_label"] or ""
                item.setToolTip(f"接口 {port['number']} · {box_number_source(port)}" + (f"\n关联元件：{name}" if name else "")
                                + (f"\n类型：{subtype}" if subtype else ""))
                label = scene.addSimpleText(port["number"]); label.setParentItem(item)
                label.setBrush(QColor(number_color))
                label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                number_bounds = label.boundingRect()
                number_width, number_height = number_bounds.width(), number_bounds.height()
                if smart_wiring:
                    number_width, number_height = _fit_label(label, 1.3 * radius, 1.3 * radius)
                label.setPos(-number_width / 2, -number_height / 2)
                caption_top = radius + gap
                if name and show_names:
                    if smart_wiring:
                        caption = scene.addSimpleText(name); caption.setParentItem(item)
                        name_height = (caption_bottom - caption_top - gap) / 2
                        width, height = _fit_label(caption, caption_width, name_height)
                        caption.setPos(-width / 2, caption_top)
                        caption_top += height + gap
                    else:
                        font = QFont(); font.setPixelSize(max(8, min(11, int(pitch - 2 * radius - 3))))
                        text = QFontMetrics(font).elidedText(name, Qt.TextElideMode.ElideRight, max(1, int(pitch - 4)))
                        caption = scene.addSimpleText(text); caption.setParentItem(item); caption.setFont(font)
                        caption.setPos(-caption.boundingRect().width() / 2, radius + 1)
                    caption.setBrush(QColor(colors["ink"])); caption.setToolTip(name)
                    caption.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                items[ident] = item
                if smart_wiring and not empty_port and (connection or not only_connected_captions):
                    if connection:
                        target = box_connection_label(connection)
                    else:
                        target = "未分配" if diagram.connections_available else "未知"
                    item.setToolTip(item.toolTip() + f"\n制冷机：{target}")
                    font = QFont(); font.setBold(bool(connection))
                    caption = scene.addSimpleText(target); caption.setParentItem(item); caption.setFont(font)
                    caption.setBrush(QColor(ui["text"] if connection else ui["muted"]))
                    caption.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    caption.setToolTip(f"制冷机端口：{target}")
                    width, _ = _fit_label(caption, caption_width, caption_bottom - caption_top)
                    caption.setPos(-width / 2, caption_top)
            if smart_wiring:
                bounds = outline.boundingRect()
                heading = scene.addSimpleText(f"智能接线 · {diagram.name} · {diagram.connection_state}")
                font = heading.font(); font.setPixelSize(16); font.setBold(True); heading.setFont(font)
                heading.setBrush(QColor(ui["text"])); heading.setPos(bounds.left(), bounds.top() - 35)
                heading.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        return items

    def _wiring_appendix(self, diagram=None):
        scene = QGraphicsScene()
        self._populate_scene(scene, smart_wiring=True, show_names=False, hide_unassigned=False,
                             only_connected_captions=True, diagram=diagram)
        return scene

    def rotate_box(self, delta):
        if self.ports:
            self.rotation = (self.rotation + delta) % 360
            self.draw_panel(); self.fit_map()

    def export_dialog(self):
        path, selected = QFileDialog.getSaveFileName(self, "导出样品盒图片", "样品盒接线.png", "PNG 图片 (*.png);;SVG 图片 (*.svg);;PDF 文档 (*.pdf)")
        if not path: return
        if not Path(path).suffix:
            path += ".pdf" if "PDF" in selected else ".svg" if "SVG" in selected else ".png"
        try:
            self.export_scene(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def wiring_export_directory(self):
        panel = self.host.fridge_panel
        if panel is None or panel.preview:
            return self.store.path.resolve().parent
        fridge = panel.store
        directories = [fridge.sample_path(sample["id"]).parent for sample in fridge.samples()]
        if directories:
            try:
                shared = Path(os.path.commonpath(directories))
            except ValueError:  # Linked samples may be on different drives.
                pass
            else:
                if shared.parent != shared and shared.is_dir():
                    return shared
        return fridge.path.resolve().parent

    def export_table_dialog(self):
        sample_name = self.store.meta()["chip_name"]
        suggested = self.wiring_export_directory() / f"{wiring_export_stem(sample_name)}.pdf"
        filters = {"PDF 文档 (*.pdf)": ".pdf", "Excel (*.xlsx)": ".xlsx", "CSV (*.csv)": ".csv"}
        path, selected = QFileDialog.getSaveFileName(self, "导出接线表", str(suggested), ";;".join(filters))
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() not in filters.values():
            path = path.with_name(path.name + filters.get(selected, ".pdf"))
        try:
            if path.suffix.lower() == ".pdf":
                connections = {ident: record for ident, record in self.connections.items() if ident in self.ports}
                blocks = sample_box_wiring_blocks(self.ports, connections, combine_readout_plates=True)
                export_sample_box_wiring_pdf(path, blocks, sample_name=sample_name, diagram_scene=self._wiring_appendix())
            elif path.suffix.lower() == ".xlsx":
                with tempfile.TemporaryDirectory() as directory:
                    diagram = Path(directory) / "diagram.png"
                    self.export_scene(diagram, scene=self._wiring_appendix())
                    export_sample_box_wiring(self.store, path, self.connections, diagram_image=diagram)
            else:
                export_sample_box_wiring(self.store, path, self.connections)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def export_batch_dialog(self):
        panel = self.host.fridge_panel
        if panel is None or panel.preview:
            return
        fridge = panel.store
        if not fridge.samples():
            QMessageBox.information(self, "批量导出接线表", "当前制冷机项目尚未关联样品。")
            return
        dialog = BatchWiringExportDialog(fridge, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择接线表导出目录", str(self.wiring_export_directory()))
        if not directory:
            return
        try:
            written, failures = self.export_batch_tables(fridge, dialog.sample_ids(), Path(directory),
                                                         dialog.formats.currentData())
        except OSError as exc:
            QMessageBox.warning(self, "批量导出失败", str(exc))
            return
        message = f"已导出 {len(written)} 个文件到\n{directory}"
        if failures:
            report = QMessageBox(QMessageBox.Icon.Warning, "批量导出结果",
                                 f"{message}\n{len(failures)} 个样品导出失败。", parent=self)
            report.setDetailedText("\n".join(failures))
            report.exec()
        else:
            QMessageBox.information(self, "批量导出完成", message)

    def export_batch_tables(self, fridge, sample_ids, directory, formats):
        occupied = {path.name.casefold() for path in directory.iterdir()}
        written, failures = [], []
        current_path = self.store.path.resolve() if self.store else None
        fridge_name = fridge.name()
        with tempfile.TemporaryDirectory(dir=directory) as scratch:
            for ident in sample_ids:
                sample_name = fridge.sample(ident)["name"]
                try:
                    sample_name, appearance, box_ports, subtypes = fridge.read_sample_box(ident)
                    ports = {port["id"]: port for port in box_ports}
                    connections = {row["box_port_id"]: row for row in fridge.box_connections(ident)
                                   if row["box_port_id"] in ports}
                    if not connections:
                        raise ValueError("没有有效的样品盒接线")
                    state = f"制冷机：{fridge_name} · 已连接 {len(connections)} 个接口"
                    rotation = self.rotation if fridge.sample_path(ident) == current_path else 0
                    diagram = SampleBoxDiagram(sample_name, appearance, ports, connections, state, True,
                                              rotation, subtypes)
                    base = wiring_export_stem(sample_name)
                    stem = base
                    index = 2
                    while any(f"{stem}.{ext}".casefold() in occupied for ext in formats):
                        stem = f"{base} ({index})"
                        index += 1
                    files = self._export_wiring_documents(Path(scratch), stem, diagram, formats)
                    for file in files:
                        target = directory / file.name
                        file.rename(target)
                        occupied.add(file.name.casefold())
                        written.append(target)
                except (OSError, sqlite3.Error, ValueError) as exc:
                    failures.append(f"{sample_name} · {exc}")
        return written, failures

    def export_bundle_dialog(self):
        if not self.connections_available or not self.connections.keys() & self.ports.keys():
            message = self.connection_state if not self.connections_available else "当前样品盒没有已分配的接线。"
            QMessageBox.information(self, "接线大礼包", message)
            return
        sample_name = self.store.meta()["chip_name"]
        file_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", sample_name).strip()
        default_name = f"{file_name}-接线大礼包.zip" if file_name else "接线大礼包.zip"
        path, _ = QFileDialog.getSaveFileName(self, "接线大礼包", default_name, "ZIP 压缩文件 (*.zip)")
        if not path:
            return
        if Path(path).suffix.lower() != ".zip":
            path += ".zip"
        try:
            self.export_bundle(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def export_bundle(self, path):
        target = Path(path)
        if target.suffix.lower() != ".zip":
            raise ValueError("请选择 ZIP 格式")
        connections = {ident: self.connections[ident] for ident in self.ports if ident in self.connections}
        if not connections:
            raise ValueError("当前样品盒没有已分配的接线。")
        with tempfile.TemporaryDirectory(dir=target.parent) as directory:
            root = Path(directory)
            staged = root / "bundle.zip"
            with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
                for filename, show_names in (("样品盒接线图-含元件名.png", True), ("样品盒接线图-仅端口.png", False)):
                    scene = QGraphicsScene()
                    self._populate_scene(scene, smart_wiring=True, show_names=show_names, hide_unassigned=True)
                    self.export_scene(root / filename, scene=scene)
                    archive.write(root / filename, filename)
                diagram = self._current_diagram()
                for file in self._export_wiring_documents(root, "接线表", diagram, ("xlsx", "pdf")):
                    archive.write(file, file.name)
            staged.replace(target)

    def _export_wiring_documents(self, directory, stem, diagram, formats):
        connections = {ident: record for ident, record in diagram.connections.items() if ident in diagram.ports}
        scene = self._wiring_appendix(diagram)
        files = []
        if "xlsx" in formats:
            image = directory / "diagram.png"
            self.export_scene(image, scene=scene)
            workbook = directory / f"{stem}.xlsx"
            blocks = list(sample_box_wiring_blocks(diagram.ports, connections))
            export_sample_box_wiring_matrix(workbook, blocks, sample_name=diagram.name, diagram_image=image)
            files.append(workbook)
        if "pdf" in formats:
            pdf = directory / f"{stem}.pdf"
            blocks = sample_box_wiring_blocks(diagram.ports, connections, combine_readout_plates=True)
            export_sample_box_wiring_pdf(pdf, blocks, sample_name=diagram.name, diagram_scene=scene)
            files.append(pdf)
        return files

    def export_scene(self, path, *, scene=None):
        scene = self.scene if scene is None else scene
        path = Path(path); suffix = path.suffix.lower()
        if suffix not in (".png", ".svg", ".pdf"): raise ValueError("请选择 PNG、SVG 或 PDF 格式")
        bounds = scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        if bounds.isEmpty(): raise ValueError("没有可导出的样品盒图面")
        width = 2400; height = max(1, round(width * bounds.height() / bounds.width()))
        if suffix == ".svg":
            device = QSvgGenerator(); device.setFileName(str(path)); device.setSize(QSize(width, height)); device.setViewBox(QRectF(0, 0, width, height))
        elif suffix == ".pdf":
            device = QPdfWriter(str(path)); device.setResolution(144)
            orientation = QPageLayout.Orientation.Landscape if bounds.width() > bounds.height() else QPageLayout.Orientation.Portrait
            device.setPageOrientation(orientation); device.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        else:
            device = QImage(width, height, QImage.Format.Format_ARGB32); device.fill(QColor(DIAGRAM_COLORS[self.host.theme]["paper"]))
        selected = scene.selectedItems(); scene.blockSignals(True)
        for item in selected: item.setSelected(False)
        try:
            painter = QPainter(device)
            if not painter.isActive(): raise OSError(f"无法写入 {path}")
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            target = QRectF(0, 0, device.width(), device.height())
            scene.render(painter, target, bounds, Qt.AspectRatioMode.KeepAspectRatio); painter.end()
            if suffix == ".png" and not device.save(str(path), "PNG"): raise OSError(f"无法写入 {path}")
        finally:
            for item in selected: item.setSelected(True)
            scene.blockSignals(False)

    def fit_if_active(self):
        if self.view._fit_active:
            self.fit_map()

    def fit_map(self):
        if self.ports:
            self.view._fit_active = True
            self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-14, -14, 14, 14),
                                Qt.AspectRatioMode.KeepAspectRatio)

    def refresh_matches(self, *_):
        query = self.search.text().strip().casefold()
        self.matches.blockSignals(True); self.matches.clear()
        self.matches.addItem("选择接口", None)
        for ident in self.items:
            port = self.ports[ident]
            name = port["device_label"] or ""
            terms = (port["number"], name, name.replace("{", "").replace("}", "").replace("_", ""), port["signal_name"])
            if not query or any(query in term.casefold() for term in terms):
                self.matches.addItem(f"{port['number']} · {name}" if name else port["number"], ident)
        index = self.matches.findData(self.selected)
        exact = next((i for i in range(1, self.matches.count())
                      if self.ports[self.matches.itemData(i)]["number"].casefold() == query), -1)
        if query and exact > 0:
            index = exact
        elif query and index <= 0 and self.matches.count() > 1:
            index = 1
        self.matches.setCurrentIndex(max(0, index)); self.matches.blockSignals(False)
        self.matches.setEnabled(self.matches.count() > 1)
        if query and index > 0:
            self.locate_match()

    def locate_match(self, *_):
        ident = self.matches.currentData()
        if ident in self.items:
            self.scene.clearSelection()
            self.items[ident].setSelected(True)
            self.view.centerOn(self.items[ident])

    def canvas_selected(self):
        selected = self.scene.selectedItems()
        self.selected = selected[0].data(0) if selected else None
        index = self.matches.findData(self.selected)
        self.matches.blockSignals(True); self.matches.setCurrentIndex(max(0, index)); self.matches.blockSignals(False)
        self.show_port()

    def show_port(self):
        port = self.ports.get(self.selected)
        connection = self.connections.get(self.selected)
        self.edit_button.setEnabled(port is not None); self.trace_button.setEnabled(connection is not None)
        title = self.appearance["name"] if self.appearance else "样品盒构型"
        self.title.setText(f"接口 {port['number']}" if port else title)
        if not port:
            self.info.setText("点击接口查看详细登记。" if self.ports else
                              "为当前芯片应用样品盒模板。" if self.store else "请先打开或新建芯片项目。")
            return
        lines = [box_number_source(port), f"信号：{port['signal_name'] or '未登记'}",
                 f"元件：{port['device_label'] or '未关联'} {BOX_ROLES.get(port['role'], '')}".strip()]
        if port["device_kind"] == "readout":
            lines.append(self.store.readout_summary(port["device_id"]))
        elif port["device_kind"] == "custom":
            lines.append(f"类型：{port['device_subtype']}")
        if connection:
            branch = f" · 分支 {connection['branch']}" if connection["branch"] is not None else ""
            lines.append(f"制冷机：{connection['board']} 盘 {connection['number']}{branch}")
            circuit = self.host.fridge_panel.store.circuit(connection["id"])
            lines.extend(f"{c.get('stage', '')} · {component_text(c)}" for c in circuit.get("components", []))
        else:
            lines.append("当前制冷机项目未分配此接口")
        self.info.setText("\n".join(lines))

    def locate_fridge(self):
        connection = self.connections.get(self.selected)
        if connection:
            fridge = self.host.fridge_panel
            self.host.tabs.setCurrentWidget(self.host.fridge_page)
            fridge.open_board(connection["board"])
            fridge.select_port(connection["id"])
            fridge.selected_branch = connection["branch"]

    def update_template_action(self):
        target = self.template_combo.currentData()
        self.enable_button.setText("更换模板" if self.template_id else "应用模板")
        self.enable_button.setEnabled(self.store is not None and bool(target) and target != self.template_id)

    def open_template_directory(self):
        try:
            self.template_directory.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.template_directory))):
                raise OSError(f"无法打开 {self.template_directory}")
        except OSError as exc:
            self.host._error("打开模板目录失败", exc)

    def enable_box(self):
        target = self.templates[self.template_combo.currentData()]
        replace = self.template_id is not None
        if replace and QMessageBox.question(self, "更换样品盒模板",
                "更换会重新生成接口并解除原有的元件关联。已登记的制冷机分配需要重新选择接口。可以撤回此操作。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            self.template_combo.setCurrentIndex(self.template_combo.findData(self.template_id))
            return
        try:
            self.store.enable_sample_box(target, replace=replace)
            self.host.refresh_content(); self.host._written("样品盒构型")
            self.fit_map()
        except Exception as exc:
            self.host._error("启用样品盒失败", exc)

    def edit_port(self):
        port = self.ports[self.selected]
        dialog = BoxPortDialog(self, port, self.store.devices())
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.save_sample_box_port(port["id"], dialog.number.text(), dialog.verified.isChecked(),
                                                dialog.device.currentData(), dialog.role.currentData() if dialog.role.isEnabled() else "single",
                                                dialog.signal.text())
                self.host.refresh_content(); self.host._written("样品盒接口")
                return
            except Exception as exc:
                dialog.error.setText(str(exc))

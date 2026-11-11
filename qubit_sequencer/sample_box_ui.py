"""Graphic front-panel view for a chip's sample box."""

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QFont, QFontMetrics, QImage, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen, QTransform
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGraphicsItem, QGraphicsScene,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .device_types import DEVICE_NAMES
from .exports import export_sample_box_wiring
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
        export_menu.addAction("导出样品盒图片", self.export_dialog)
        export_menu.addAction("导出接线表", self.export_table_dialog)
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
        self.display_button.setEnabled(bool(self.ports)); self.export_button.setEnabled(bool(self.ports))
        self.refresh_connections()
        if old_store is not self.store or self.view._fit_active:
            self.fit_map()

    def refresh_connections(self):
        self.connections = {}
        self.connection_state = "未打开制冷机项目"
        self.connections_available = False
        fridge = self.host.fridge_panel
        if self.store and fridge and not fridge.preview:
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
        self.scene.clear(); self.items.clear()
        theme = self.host.theme
        colors = DIAGRAM_COLORS[theme]; ui = UI_COLORS[theme]
        self.scene.setBackgroundBrush(QColor(colors["paper"]))
        if self.ports:
            drawing = self.appearance["drawing"]
            vertices = drawing["outline"]
            outline = QPainterPath(QPointF(*vertices[0]))
            for x, y in vertices[1:]:
                outline.lineTo(x, y)
            outline.closeSubpath()
            center = outline.boundingRect().center()
            smart_wiring = self.smart_wiring.isChecked()
            show_names = self.show_names.isChecked()
            hide_unassigned = self.connections_available and self.hide_unassigned.isChecked()
            spread = 1.65 if smart_wiring else 1.0
            rotation = QTransform().translate(center.x(), center.y()).rotate(self.rotation).scale(spread, spread).translate(-center.x(), -center.y())
            outline = rotation.map(outline)
            self.scene.addPath(outline, QPen(QColor(ui["border"]), 2), QBrush(QColor(ui["panel"])))
            if drawing["cover"]:
                cover = QPainterPath(); cover.addRect(*drawing["cover"])
                self.scene.addPath(rotation.map(cover), QPen(QColor(ui["border"]), 2), QBrush(QColor(ui["toolbar"])))
            for x, y, radius in drawing["holes"]:
                point = rotation.map(QPointF(x, y)); x, y = point.x(), point.y()
                self.scene.addEllipse(x-radius, y-radius, radius*2, radius*2,
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
            custom_colors = custom_device_colors(device["subtype"] for device in self.store.devices("custom"))
            for ident, port in self.ports.items():
                connection = self.connections.get(ident)
                if hide_unassigned and connection is None:
                    continue
                empty_port = (smart_wiring and self.connections_available
                              and connection is None and not port["device_id"])
                x, y = origin_x + port["x"] * pitch, origin_y + port["y"] * pitch
                point = rotation.map(QPointF(x, y)); x, y = point.x(), point.y()
                inferred = port["source"] == "inferred" and not port["verified"]
                pen = QPen(QColor(colors["muted"]), 1.6, Qt.PenStyle.DashLine if inferred else Qt.PenStyle.SolidLine)
                subtype = port["device_subtype"] if port["device_kind"] == "custom" else ""
                color = custom_colors[subtype] if subtype else fill.get(port["device_kind"], colors["paper"])
                number_color = "#15181E" if port["device_kind"] else colors["ink"]
                if empty_port:
                    color = DIAGRAM_COLORS["dark"]["paper"]
                    number_color = DIAGRAM_COLORS["dark"]["ink"]
                brush = QBrush(QColor(color))
                item = self.scene.addEllipse(-radius, -radius, radius*2, radius*2, pen, brush)
                item.setPos(x, y); item.setData(0, ident)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
                item.setSelected(ident in selected_ids if selected_ids else ident == self.selected)
                name = port["device_label"] or ""
                item.setToolTip(f"接口 {port['number']} · {box_number_source(port)}" + (f"\n关联元件：{name}" if name else "")
                                + (f"\n类型：{subtype}" if subtype else ""))
                label = self.scene.addSimpleText(port["number"]); label.setParentItem(item)
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
                        caption = self.scene.addSimpleText(name); caption.setParentItem(item)
                        name_height = (caption_bottom - caption_top - gap) / 2
                        width, height = _fit_label(caption, caption_width, name_height)
                        caption.setPos(-width / 2, caption_top)
                        caption_top += height + gap
                    else:
                        font = QFont(); font.setPixelSize(max(8, min(11, int(pitch - 2 * radius - 3))))
                        text = QFontMetrics(font).elidedText(name, Qt.TextElideMode.ElideRight, max(1, int(pitch - 4)))
                        caption = self.scene.addSimpleText(text); caption.setParentItem(item); caption.setFont(font)
                        caption.setPos(-caption.boundingRect().width() / 2, radius + 1)
                    caption.setBrush(QColor(colors["ink"])); caption.setToolTip(name)
                    caption.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.items[ident] = item
                if smart_wiring and not empty_port:
                    if connection:
                        target = box_connection_label(connection)
                    else:
                        target = "未分配" if self.connections_available else "未知"
                    item.setToolTip(item.toolTip() + f"\n制冷机：{target}")
                    font = QFont(); font.setBold(bool(connection))
                    caption = self.scene.addSimpleText(target); caption.setParentItem(item); caption.setFont(font)
                    caption.setBrush(QColor(ui["text"] if connection else ui["muted"]))
                    caption.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    caption.setToolTip(f"制冷机端口：{target}")
                    width, _ = _fit_label(caption, caption_width, caption_bottom - caption_top)
                    caption.setPos(-width / 2, caption_top)
            if smart_wiring:
                bounds = outline.boundingRect()
                heading = self.scene.addSimpleText(f"智能接线 · {self.store.meta()['chip_name']} · {self.connection_state}")
                font = heading.font(); font.setPixelSize(16); font.setBold(True); heading.setFont(font)
                heading.setBrush(QColor(ui["text"])); heading.setPos(bounds.left(), bounds.top() - 35)
                heading.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if self.selected not in self.items:
            self.selected = None
        self.scene.blockSignals(False)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-18, -18, 18, 18))

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

    def export_table_dialog(self):
        path, selected = QFileDialog.getSaveFileName(self, "导出接线表", "样品盒接线表.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".csv" if "CSV" in selected else ".xlsx"
        try:
            export_sample_box_wiring(self.store, path, self.connections)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def export_scene(self, path):
        path = Path(path); suffix = path.suffix.lower()
        if suffix not in (".png", ".svg", ".pdf"): raise ValueError("请选择 PNG、SVG 或 PDF 格式")
        bounds = self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
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
        selected = self.scene.selectedItems(); self.scene.blockSignals(True)
        for item in selected: item.setSelected(False)
        try:
            painter = QPainter(device)
            if not painter.isActive(): raise OSError(f"无法写入 {path}")
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            target = QRectF(0, 0, device.width(), device.height())
            self.scene.render(painter, target, bounds, Qt.AspectRatioMode.KeepAspectRatio); painter.end()
            if suffix == ".png" and not device.save(str(path), "PNG"): raise OSError(f"无法写入 {path}")
        finally:
            for item in selected: item.setSelected(True)
            self.scene.blockSignals(False)

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

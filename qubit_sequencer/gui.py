"""PySide6 desktop interface for chip topology and parameter registration."""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QDesktopServices, QFont, QGuiApplication,
                           QIcon, QPainter, QPalette, QTextDocument)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGraphicsView, QHeaderView, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QSplitter, QStyledItemDelegate,
    QStyle, QStyleOptionViewItem, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .diagram import ChipScene, cjk_font_family, export_diagram
from .exports import build_tables, export_csv, export_xlsx
from .storage import ChipStore
from .templates import rotated_surface_code, square_grid
from .tex_labels import tex_to_html
from .theme import UI_COLORS, palette, stylesheet


def _table_fill(table: QTableWidget, headers: list[str], rows: list[list[object]]) -> None:
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()
    table.verticalHeader().setDefaultSectionSize(34)
    if headers and headers[0] in ("ID", "线路 ID", "对象 ID"):
        table.setColumnWidth(0, 155)
    for index, header in enumerate(headers):
        if header in ("名称", "对象", "器件"):
            table.setColumnWidth(index, 105)
        elif header in ("类型", "线路类型"):
            table.setColumnWidth(index, 145)


def _header_html(raw: str, muted: str) -> str:
    if " (" in raw and raw.endswith(")"):
        label, key = raw.rsplit(" (", 1)
        return f"{tex_to_html(label)} <span style='color:{muted}'>({html.escape(key[:-1])})</span>"
    return tex_to_html(raw)


class TexHeaderView(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)

    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        raw = self.model().headerData(logical_index, Qt.Orientation.Horizontal,
                                      Qt.ItemDataRole.UserRole)
        if not raw:
            return
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self.font())
        color = self.palette().color(QPalette.ColorRole.WindowText).name()
        muted = UI_COLORS["dark" if self.property("darkMode") else "light"]["muted"]
        document.setHtml(f"<span style='color:{color}'>{_header_html(str(raw), muted)}</span>")
        document.setTextWidth(max(20, rect.width()-10))
        painter.save()
        painter.translate(rect.left()+5, rect.top()+max(0, (rect.height()-document.size().height())/2))
        document.drawContents(painter, QRectF(0, 0, rect.width()-10, rect.height()))
        painter.restore()


class TexTableDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.label_columns: set[int] = set()
        self.label_rows: set[int] = set()

    def paint(self, painter, option, index):
        raw = index.data(Qt.ItemDataRole.DisplayRole)
        if raw is None or (index.column() not in self.label_columns and
                           index.row() not in self.label_rows and
                           not re.search(r"\\[A-Za-z]+|[_^]\{|(?:^|\s)[A-Z][A-Za-z0-9]*[_^][A-Za-z0-9]", str(raw))):
            super().paint(painter, option, index)
            return
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.text = ""
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(option.font)
        role = (QPalette.ColorRole.HighlightedText if option.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text)
        color = option.palette.color(role).name()
        document.setHtml(f"<span style='color:{color}'>{tex_to_html(str(raw))}</span>")
        document.setTextWidth(max(20, option.rect.width()-8))
        painter.translate(option.rect.left()+4,
                          option.rect.top()+max(0, (option.rect.height()-document.size().height())/2))
        document.drawContents(painter, QRectF(0, 0, option.rect.width()-8, option.rect.height()))
        painter.restore()


class ChipView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def _extend(self):
        scene = self.scene()
        if isinstance(scene, ChipScene):
            scene.grow_to_include(self.mapToScene(self.viewport().rect()).boundingRect())

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._extend()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._extend()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.15 if delta > 0 else 1 / 1.15)
        event.accept()

    def zoom_by(self, factor: float):
        target = self.transform().m11() * factor
        if 0.08 <= target <= 8.0:
            self.scale(factor, factor)
        self._extend()


class NewChipDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建芯片")
        form = QFormLayout(self)
        self.name = QLineEdit("新芯片")
        self.template = QComboBox()
        self.template.addItems(["自定义格点", "正方格子", "旋转表面码"])
        self.rows = QSpinBox(); self.rows.setRange(1, 9999); self.rows.setValue(5)
        self.columns = QSpinBox(); self.columns.setRange(1, 9999); self.columns.setValue(5)
        self.distance = QSpinBox(); self.distance.setRange(3, 31); self.distance.setSingleStep(2); self.distance.setValue(3)
        self.mode = QComboBox(); self.mode.addItem("经耦合器", "coupler")
        self.mode.addItem("直接电容", "direct"); self.mode.addItem("不建立连接", "none")
        form.addRow("芯片名称", self.name)
        form.addRow("模板", self.template)
        form.addRow("初始行数", self.rows)
        form.addRow("初始列数", self.columns)
        form.addRow("码距（奇数）", self.distance)
        form.addRow("模板连接", self.mode)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class ParameterDialog(QDialog):
    def __init__(self, definition, point: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"登记参数：{definition['label']}")
        layout = QFormLayout(self)
        self.value = QLineEdit()
        self.value.setPlaceholderText("留空表示未填写")
        self.point = QLineEdit(point)
        self.bias = QLineEdit()
        self.bias_unit = QLineEdit()
        self.source = QLineEdit()
        self.notes = QLineEdit()
        layout.addRow(f"值 ({definition['unit'] or '无单位'})", self.value)
        layout.addRow("工作点", self.point)
        layout.addRow("偏置值", self.bias)
        layout.addRow("偏置单位", self.bias_unit)
        layout.addRow("来源/实验", self.source)
        layout.addRow("备注", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class DefinitionDialog(QDialog):
    def __init__(self, scope: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加自定义参数")
        layout = QFormLayout(self)
        self.key = QLineEdit(); self.key.setPlaceholderText(f"{scope}.my_parameter")
        self.label = QLineEdit()
        self.value_type = QComboBox(); self.value_type.addItem("数值", "number"); self.value_type.addItem("文本", "text")
        self.unit = QLineEdit()
        self.category = QComboBox(); self.category.addItem("自定义", "custom")
        self.category.addItem("设计", "design"); self.category.addItem("测量", "measured")
        layout.addRow("参数键（唯一）", self.key)
        layout.addRow("显示名称", self.label)
        layout.addRow("类型", self.value_type)
        layout.addRow("单位", self.unit)
        layout.addRow("分类", self.category)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class LineDialog(QDialog):
    def __init__(self, parent=None, line=None):
        super().__init__(parent)
        self.setWindowTitle("登记制冷机线路")
        layout = QFormLayout(self)
        self.line_type = QComboBox(); self.line_type.setEditable(True)
        self.line_type.addItems(["XY", "Z", "读出", "耦合器磁通", "其他"])
        self.cryostat = QLineEdit(); self.port = QLineEdit(); self.channel = QLineEdit(); self.notes = QLineEdit()
        if line is not None:
            self.line_type.setCurrentText(line["line_type"])
            self.cryostat.setText(line["cryostat"])
            self.port.setText(line["port"])
            self.channel.setText(line["channel"])
            self.notes.setText(line["notes"])
        layout.addRow("线路类型", self.line_type)
        layout.addRow("制冷机", self.cryostat)
        layout.addRow("端口", self.port)
        layout.addRow("通道", self.channel)
        layout.addRow("备注", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class DevicePopup(QDialog):
    """Compact editor positioned next to the device that was clicked."""

    def __init__(self, host):
        super().__init__(host, Qt.WindowType.Popup)
        self.host = host
        self.device_id: str | None = None
        self.setWindowTitle("编辑芯片元件")
        self.resize(390, 510)
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setObjectName("popupTitle")
        layout.addWidget(self.title)
        form = QFormLayout()
        self.label = QLineEdit()
        self.label.setPlaceholderText("如 Q_{1}、\\omega_{01}")
        self.subtype = QComboBox()
        self.role = QComboBox(); self.role.setEditable(True)
        self.role.addItems(["", "data", "measure_x", "measure_z"])
        self.x = QDoubleSpinBox(); self.y = QDoubleSpinBox()
        for spin in (self.x, self.y):
            spin.setRange(-100000000, 100000000)
            spin.setDecimals(2)
        self.notes = QLineEdit()
        for caption, widget in (("名称", self.label), ("类型", self.subtype),
                                ("角色", self.role), ("格点 X", self.x),
                                ("格点 Y", self.y), ("备注", self.notes)):
            form.addRow(caption, widget)
        layout.addLayout(form)
        layout.addWidget(QLabel("参数 · 双击一行登记新值"))
        self.parameters = QTableWidget(0, 2)
        self.parameters.setAlternatingRowColors(True)
        self.parameters.cellDoubleClicked.connect(self.edit_parameter)
        layout.addWidget(self.parameters, 1)
        buttons = QHBoxLayout()
        save = QPushButton("保存更改"); save.clicked.connect(self.save)
        close = QPushButton("关闭"); close.clicked.connect(self.close)
        buttons.addWidget(save); buttons.addWidget(close)
        layout.addLayout(buttons)

    def show_device(self, ident: str, anchor: QPoint) -> None:
        self.device_id = ident
        self.reload()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            x = max(area.left(), min(anchor.x()+12, area.right()-self.width()))
            y = max(area.top(), min(anchor.y()+12, area.bottom()-self.height()))
            self.move(x, y)
        self.show()
        self.raise_()

    def reload(self) -> None:
        if not self.host.store or not self.device_id:
            self.close()
            return
        try:
            device = self.host.store.device(self.device_id)
        except KeyError:
            self.close()
            return
        self.title.setText(f"<span>{tex_to_html(device['label'])}  ·  "
                           f"{'比特' if device['kind'] == 'qubit' else '耦合器'}</span>")
        self.label.setText(device["label"])
        self.subtype.clear()
        self.subtype.addItems(["fixed_transmon", "tunable_transmon"] if device["kind"] == "qubit"
                              else ["fixed_coupler", "tunable_coupler"])
        self.subtype.setCurrentText(device["subtype"])
        self.role.setCurrentText(device["role"])
        self.x.setValue(device["x"]); self.y.setValue(device["y"])
        self.notes.setText(device["notes"])
        values = self.host.store.current_values(self.host.point_combo.currentText() or "default")
        self.definitions = self.host.store.definitions(device["kind"])
        rows = [[f"{d['label']} [{d['unit']}]" if d["unit"] else d["label"],
                 "未填写" if values.get((self.device_id, d["key"])) is None
                 else values[(self.device_id, d["key"])] ] for d in self.definitions]
        _table_fill(self.parameters, ["参数", "当前值"], rows)
        self.parameters.setColumnWidth(0, 220)
        self.parameters.setColumnWidth(1, 100)

    def save(self) -> None:
        if not self.host.store or not self.device_id:
            return
        try:
            self.host.store.update_device(
                self.device_id, label=self.label.text().strip(), subtype=self.subtype.currentText(),
                role=self.role.currentText(), x=self.x.value(), y=self.y.value(), notes=self.notes.text())
            self.host.selected = ("device", self.device_id)
            self.host.refresh_content()
            self.close()
        except Exception as exc:
            self.host._error("保存元件失败", exc)

    def edit_parameter(self, row: int, _column: int) -> None:
        if not self.device_id:
            return
        self.host.selected = ("device", self.device_id)
        self.host.refresh_detail()
        self.host.edit_parameter(row, 0, self.pos()+QPoint(22, 22))
        self.reload()


class MainWindow(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        app = QApplication.instance()
        if app:
            ui_font = QFont(cjk_font_family(), 11)
            ui_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
            app.setFont(ui_font)
            icon = QIcon(str(Path(__file__).resolve().parent / "assets" / "app-icon.png"))
            app.setWindowIcon(icon)
            self.setWindowIcon(icon)
        self.store: ChipStore | None = None
        self.scene: ChipScene | None = None
        self._initial_fit_pending = False
        self.selected: tuple[str, str] | None = None
        self.first_endpoint: str | None = None
        self.visible_definitions = []
        self.settings = QSettings("QubitSequencer", "QubitSequencer")
        self.theme = "dark" if self.settings.value("theme", "light") == "dark" else "light"
        self.setWindowTitle("Qubit Sequencer 芯片登记")
        self.resize(1450, 900)
        self._build_ui()
        self.dark_action.blockSignals(True)
        self.dark_action.setChecked(self.theme == "dark")
        self.dark_action.blockSignals(False)
        self.set_theme(self.theme, persist=False)
        self.device_popup = DevicePopup(self)
        if path:
            self._open_path(path)

    def _build_ui(self):
        file_menu = self.menuBar().addMenu("文件")
        for title, method in [
            ("新建芯片", self.new_chip), ("打开 .qchip", self.open_chip),
            ("另存备份", self.backup), ("导出 XLSX", self.save_xlsx),
            ("导出 CSV", self.save_csv), ("导出当前图片", self.save_image),
            ("批量导出参数图", self.save_all_images),
        ]:
            action = QAction(title, self); action.triggered.connect(method); file_menu.addAction(action)
        view_menu = self.menuBar().addMenu("视图")
        self.dark_action = QAction("深色模式", self)
        self.dark_action.setCheckable(True)
        self.dark_action.toggled.connect(
            lambda checked: self.set_theme("dark" if checked else "light"))
        view_menu.addAction(self.dark_action)
        help_menu = self.menuBar().addMenu("帮助")
        manual = QAction("使用说明 PDF", self); manual.triggered.connect(self.open_manual); help_menu.addAction(manual)

        toolbar = self.addToolBar("拓扑编辑")
        self.place_action = QAction("点击格点放置比特", self); self.place_action.setCheckable(True)
        toolbar.addAction(self.place_action)
        self.place_coupler_action = QAction("放置耦合器", self); self.place_coupler_action.setCheckable(True)
        toolbar.addAction(self.place_coupler_action)
        self.connect_action = QAction("点击两个器件连接", self); self.connect_action.setCheckable(True)
        toolbar.addAction(self.connect_action)
        for action in (self.place_action, self.place_coupler_action, self.connect_action):
            action.toggled.connect(lambda checked, chosen=action: self._sync_mode(chosen, checked))
        self.connection_mode = QComboBox(); self.connection_mode.addItem("直接电容", "direct")
        self.connection_mode.addItem("经耦合器", "coupler")
        toolbar.addWidget(self.connection_mode)
        delete_action = QAction("删除所选", self); delete_action.triggered.connect(self.delete_selected)
        toolbar.addAction(delete_action)
        toolbar.addSeparator()
        fit_action = QAction("适配芯片", self)
        fit_action.triggered.connect(self.fit_diagram)
        toolbar.addAction(fit_action)

        self.tabs = QTabWidget(); self.setCentralWidget(self.tabs)
        diagram_page = QWidget(); diagram_layout = QVBoxLayout(diagram_page)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("工作点"))
        self.point_combo = QComboBox(); self.point_combo.setEditable(True)
        self.point_combo.addItems(["default", "idle", "readout", "max"])
        controls.addWidget(self.point_combo)
        controls.addWidget(QLabel("图层"))
        self.field_combo = QComboBox(); controls.addWidget(self.field_combo, 1)
        diagram_layout.addLayout(controls)
        split = QSplitter()
        self.view = ChipView(); split.addWidget(self.view)
        detail = QWidget(); detail.setMinimumWidth(340); detail.setMaximumWidth(460)
        detail_layout = QVBoxLayout(detail)
        self.selected_title = QLabel("点击图上的器件或连接查看详情")
        self.selected_title.setTextFormat(Qt.TextFormat.RichText)
        detail_layout.addWidget(self.selected_title)
        self.detail_form = QFormLayout()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("支持 Q_{1}、\\omega_{01} 等简易 TeX 标签")
        self.subtype_edit = QComboBox()
        self.role_edit = QComboBox(); self.role_edit.setEditable(True)
        self.role_edit.addItems(["", "data", "measure_x", "measure_z"])
        self.x_edit = QDoubleSpinBox(); self.y_edit = QDoubleSpinBox()
        for spin in (self.x_edit, self.y_edit):
            spin.setRange(-100000000, 100000000); spin.setDecimals(2)
        self.notes_edit = QLineEdit()
        for name, widget in (("名称", self.label_edit), ("类型", self.subtype_edit),
                             ("角色", self.role_edit), ("格点 X", self.x_edit),
                             ("格点 Y", self.y_edit), ("备注", self.notes_edit)):
            self.detail_form.addRow(name, widget)
        detail_layout.addLayout(self.detail_form)
        self.save_button = QPushButton("保存器件信息"); self.save_button.clicked.connect(self.save_device)
        detail_layout.addWidget(self.save_button)
        detail_layout.addWidget(QLabel("参数（双击一行登记新值）"))
        self.parameter_table = QTableWidget(0, 3)
        self.parameter_table.setHorizontalHeaderLabels(["参数", "当前值", "单位"])
        self.parameter_table.cellDoubleClicked.connect(self.edit_parameter)
        detail_layout.addWidget(self.parameter_table, 1)
        custom_button = QPushButton("添加自定义参数"); custom_button.clicked.connect(self.add_definition)
        detail_layout.addWidget(custom_button)
        split.addWidget(detail); split.setStretchFactor(0, 4)
        diagram_layout.addWidget(split)
        self.tabs.addTab(diagram_page, "芯片构型")

        data_page = QWidget(); data_layout = QVBoxLayout(data_page)
        row = QHBoxLayout(); row.addWidget(QLabel("数据表"))
        self.table_kind = QComboBox(); self.table_kind.addItems(["比特", "耦合器", "比特-耦合器连接", "耦合", "接线"])
        self.table_kind.currentTextChanged.connect(self.refresh_tables)
        row.addWidget(self.table_kind)
        self.table_orientation = QComboBox()
        self.table_orientation.addItem("横向 · 器件为行", "horizontal")
        self.table_orientation.addItem("纵向 · 器件为列", "vertical")
        self.table_orientation.currentIndexChanged.connect(self.refresh_tables)
        row.addWidget(self.table_orientation)
        row.addWidget(QLabel("筛选"))
        self.filter_edit = QLineEdit(); self.filter_edit.setPlaceholderText("输入名称、类型、端口或任意参数")
        self.filter_edit.textChanged.connect(self.filter_table)
        row.addWidget(self.filter_edit, 1)
        data_layout.addLayout(row)
        self.data_table = QTableWidget(); self.data_table.setAlternatingRowColors(True)
        self.data_table.setHorizontalHeader(TexHeaderView(self.data_table))
        self.tex_delegate = TexTableDelegate(self.data_table)
        self.data_table.setItemDelegate(self.tex_delegate)
        self.data_table.cellDoubleClicked.connect(self.on_data_row_double_clicked)
        data_layout.addWidget(self.data_table)
        self.tabs.addTab(data_page, "参数数据表")

        wiring_page = QWidget(); wiring_layout = QVBoxLayout(wiring_page)
        buttons = QHBoxLayout()
        for title, callback in (("新增线路", self.add_line), ("编辑线路", self.edit_line),
                                ("关联器件", self.assign_line), ("解除关联", self.unassign_line),
                                ("删除线路", self.delete_line)):
            button = QPushButton(title); button.clicked.connect(callback); buttons.addWidget(button)
        buttons.addStretch(); wiring_layout.addLayout(buttons)
        self.wiring_table = QTableWidget(); wiring_layout.addWidget(self.wiring_table)
        self.tabs.addTab(wiring_page, "制冷机接线")

        history_page = QWidget(); history_layout = QVBoxLayout(history_page)
        self.history_table = QTableWidget(); history_layout.addWidget(self.history_table)
        self.tabs.addTab(history_page, "参数历史")

        self.field_combo.currentIndexChanged.connect(self.refresh_scene)
        self.point_combo.currentTextChanged.connect(self.refresh_content)
        self.statusBar().showMessage("新建或打开 .qchip 文件开始")

    def set_theme(self, mode: str, persist: bool = True) -> None:
        self.theme = "dark" if mode == "dark" else "light"
        app = QApplication.instance()
        if app:
            app.setPalette(palette(self.theme))
        self.setStyleSheet(stylesheet(self.theme))
        self.data_table.horizontalHeader().setProperty("darkMode", self.theme == "dark")
        self.data_table.horizontalHeader().viewport().update()
        if self.dark_action.isChecked() != (self.theme == "dark"):
            self.dark_action.blockSignals(True)
            self.dark_action.setChecked(self.theme == "dark")
            self.dark_action.blockSignals(False)
        if persist:
            self.settings.setValue("theme", self.theme)
        if self.store:
            self.refresh_scene()

    def _sync_mode(self, chosen: QAction, checked: bool):
        actions = (self.place_action, self.place_coupler_action, self.connect_action)
        if checked:
            for action in actions:
                if action is not chosen:
                    action.setChecked(False)
            if chosen is not self.connect_action:
                self.first_endpoint = None
        elif chosen is self.connect_action:
            self.first_endpoint = None

    def fit_diagram(self):
        if self.scene:
            self.view.fitInView(self.scene.content_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self.view._extend()

    def _error(self, title: str, exc: Exception | str):
        QMessageBox.critical(self, title, str(exc))

    def _open_path(self, path: str):
        try:
            incoming = ChipStore.open(path)
            if hasattr(self, "device_popup"):
                self.device_popup.close()
            if self.store:
                self.store.close()
            self.store = incoming
            self.scene = None
            self.selected = None
            saved_points = [row[0] for row in self.store.db.execute(
                "SELECT DISTINCT point FROM parameter_records ORDER BY point"
            )]
            self.point_combo.blockSignals(True)
            self.point_combo.clear()
            self.point_combo.addItems(list(dict.fromkeys(["default", "idle", "readout", "max"] + saved_points)))
            self.point_combo.setCurrentText("default")
            self.point_combo.blockSignals(False)
            self._refresh_field_options()
            self.refresh_content()
            self.setWindowTitle(f"{self.store.meta()['chip_name']} - Qubit Sequencer")
            self.statusBar().showMessage(str(path))
        except Exception as exc:
            self._error("无法打开文件", exc)

    def new_chip(self):
        dialog = NewChipDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name.text().strip()
        if not name:
            self._error("名称不能为空", "请填写芯片名称")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存芯片文件", f"{name}.qchip", "芯片文件 (*.qchip)")
        if not path:
            return
        if not path.lower().endswith(".qchip"):
            path += ".qchip"
        kind = ("custom", "square", "surface")[dialog.template.currentIndex()]
        if kind == "surface" and dialog.distance.value() % 2 == 0:
            self._error("码距无效", "旋转表面码的码距必须是奇数")
            return
        store = None
        created = False
        try:
            store = ChipStore.create(path, name, kind)
            created = True
            if kind == "surface":
                rotated_surface_code(store, dialog.distance.value(), dialog.mode.currentData())
            elif kind == "square":
                square_grid(store, dialog.rows.value(), dialog.columns.value(), dialog.mode.currentData())
            else:
                store.set_meta("grid_rows", str(dialog.rows.value()))
                store.set_meta("grid_columns", str(dialog.columns.value()))
            store.close()
            self._open_path(path)
        except Exception as exc:
            if store:
                store.close()
            if created:
                Path(path).unlink(missing_ok=True)
            self._error("创建芯片失败", exc)

    def open_chip(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开芯片文件", "", "芯片文件 (*.qchip)")
        if path:
            self._open_path(path)

    def _refresh_field_options(self):
        if not self.store:
            return
        old = self.field_combo.currentData()
        self.field_combo.blockSignals(True)
        self.field_combo.clear(); self.field_combo.addItem("原始构型", "")
        for definition in self.store.definitions():
            self.field_combo.addItem(f"{definition['scope']} · {definition['label']}", definition["key"])
        for label, key in (("器件类型", "device.subtype"), ("表面码角色", "device.role"),
                           ("耦合方式", "coupling.mode"), ("接线类型", "wiring.line_type"),
                           ("制冷机", "wiring.cryostat"), ("制冷机端口", "wiring.port"),
                           ("线路通道", "wiring.channel")):
            self.field_combo.addItem(label, key)
        index = self.field_combo.findData(old)
        self.field_combo.setCurrentIndex(max(0, index))
        self.field_combo.blockSignals(False)

    def refresh_content(self, *_):
        self.refresh_scene()
        self.refresh_tables()
        self.refresh_history()
        self.refresh_wiring()
        self.refresh_detail()
        if hasattr(self, "device_popup") and self.device_popup.isVisible():
            self.device_popup.reload()

    def refresh_scene(self, *_):
        if not self.store:
            return
        field = self.field_combo.currentData() or ""
        old_center = None
        old_transform = None
        if self.scene is not None and self.scene.store is self.store and self.scene.field == field:
            old_center = self.view.mapToScene(self.view.viewport().rect().center())
            old_transform = self.view.transform()
        self.scene = ChipScene(self.store, field,
                               self.point_combo.currentText() or "default", self.theme)
        self.scene.clicked.connect(self.on_scene_clicked)
        self.scene.blank_clicked.connect(self.on_blank_clicked)
        self.view.setScene(self.scene)
        if old_center is None:
            self.view.fitInView(self.scene.content_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._initial_fit_pending = not self.isVisible()
        else:
            self.view.setTransform(old_transform)
            self.scene.grow_to_include(self.view.mapToScene(self.view.viewport().rect()).boundingRect())
            self.view.centerOn(old_center)

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_fit_pending and self.scene:
            self.view.fitInView(self.scene.content_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._initial_fit_pending = False

    def on_data_row_double_clicked(self, row: int, _column: int):
        if not self.store:
            return
        title = self.table_kind.currentText()
        kind = {"比特": "device", "耦合器": "device", "比特-耦合器连接": "link",
                "耦合": "coupling"}.get(title)
        if self.table_orientation.currentData() == "vertical":
            ident = self._table_object_ids[_column-1] if 0 < _column <= len(self._table_object_ids) else None
        else:
            item = self.data_table.item(row, 0)
            ident = item.text() if item else None
        if kind and ident:
            self.selected = (kind, ident)
            self.tabs.setCurrentIndex(0)
            self.refresh_detail()

    def on_scene_clicked(self, kind: str, ident: str):
        anchor = self.scene.last_click_screen_pos if self.scene else None
        if self.scene:
            self.scene.last_click_screen_pos = None
        if self.connect_action.isChecked() and kind == "device" and self.store:
            if self.first_endpoint is None:
                self.first_endpoint = ident
                self.statusBar().showMessage("已选择第一个器件，请点击比特或耦合器")
                return
            first = self.first_endpoint; self.first_endpoint = None
            if first == ident:
                return
            self._connect(first, ident)
            return
        self.selected = (kind, ident)
        self.refresh_detail()
        if kind == "device" and anchor is not None:
            QTimer.singleShot(0, lambda: self.device_popup.show_device(ident, anchor))

    def on_blank_clicked(self, x: float, y: float):
        if not (self.place_action.isChecked() or self.place_coupler_action.isChecked()) or not self.store:
            return
        meta = self.store.meta()
        if meta.get("template") == "surface":
            self._error("无法放置", "旋转表面码模板暂不支持通过方格底板放置；请新建自定义芯片")
            return
        is_coupler = self.place_coupler_action.isChecked()
        c, r = (round(x * 2) / 2, round(y * 2) / 2) if is_coupler else (round(x), round(y))
        if any(d["x"] == c and d["y"] == r for d in self.store.devices()):
            self._error("位置已占用", f"({c:g}, {r:g}) 已有器件")
            return
        default_label = f"C_{r:g}_{c:g}" if is_coupler else f"Q_{r:g}_{c:g}"
        label, ok = QInputDialog.getText(self, "放置耦合器" if is_coupler else "放置比特",
                                         "器件名称（支持 Q_{1}、\\omega_{01}）", text=default_label)
        if not ok or not label.strip():
            return
        choices = ["可调耦合器", "固定耦合器"] if is_coupler else ["可调 Transmon", "不可调 Transmon"]
        subtype, ok = QInputDialog.getItem(self, "器件类型", "类型", choices, 0, False)
        if not ok:
            return
        try:
            kind = "coupler" if is_coupler else "qubit"
            device_type = ("tunable_coupler" if subtype.startswith("可调") else "fixed_coupler") if is_coupler else (
                "tunable_transmon" if subtype.startswith("可调") else "fixed_transmon")
            ident = self.store.add_device(kind, device_type, label, c, r)
            self.selected = ("device", ident)
            self.refresh_content()
        except Exception as exc:
            self._error("放置失败", exc)

    def _connect(self, first: str, second: str):
        if not self.store:
            return
        mode = self.connection_mode.currentData()
        mediator = None
        try:
            kind_a, kind_b = self.store.device(first)["kind"], self.store.device(second)["kind"]
            if {kind_a, kind_b} == {"qubit", "coupler"}:
                qid = first if kind_a == "qubit" else second
                cid = first if kind_a == "coupler" else second
                ident = self.store.add_link(qid, cid)
                self.selected = ("link", ident)
                self.refresh_content()
                return
            if kind_a != "qubit" or kind_b != "qubit":
                raise ValueError("请选择两个比特，或一个比特与一个耦合器")
            if mode == "coupler":
                a, b = self.store.device(first), self.store.device(second)
                labels = {d["label"] for d in self.store.devices()}
                n = 1
                while f"C_{n}" in labels:
                    n += 1
                mediator = self.store.add_device("coupler", "tunable_coupler", f"C_{n}",
                                                  (a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2)
            ident = self.store.add_coupling(first, second, mode, mediator)
            self.selected = ("coupling", ident)
            self.refresh_content()
        except Exception as exc:
            if mediator:
                self.store.delete_device(mediator)
            self._error("连接失败", exc)

    def refresh_detail(self):
        if not self.store or not self.selected:
            self.selected_title.setText("点击图上的器件或连接查看详情")
            self.save_button.setEnabled(False)
            for field in (self.label_edit, self.subtype_edit, self.role_edit,
                          self.x_edit, self.y_edit, self.notes_edit):
                field.setEnabled(False)
            _table_fill(self.parameter_table, ["参数", "当前值", "单位"], [])
            return
        kind, ident = self.selected
        try:
            if kind == "device":
                self.save_button.setEnabled(True)
                self.save_button.setText("保存器件信息")
                for field in (self.label_edit, self.subtype_edit, self.role_edit,
                              self.x_edit, self.y_edit, self.notes_edit):
                    field.setEnabled(True)
                obj = self.store.device(ident)
                scope = obj["kind"]
                self.selected_title.setText(f"<span>{tex_to_html(obj['label'])} · {scope} · {ident[:8]}</span>")
                self.label_edit.setText(obj["label"])
                self.subtype_edit.clear()
                options = (["fixed_transmon", "tunable_transmon"] if scope == "qubit"
                           else ["fixed_coupler", "tunable_coupler"])
                self.subtype_edit.addItems(options)
                self.subtype_edit.setCurrentText(obj["subtype"])
                self.role_edit.setCurrentText(obj["role"])
                self.x_edit.setValue(obj["x"]); self.y_edit.setValue(obj["y"])
                self.notes_edit.setText(obj["notes"])
            elif kind == "coupling":
                self.save_button.setEnabled(True)
                self.save_button.setText("保存连接备注")
                for field in (self.label_edit, self.subtype_edit, self.role_edit,
                              self.x_edit, self.y_edit):
                    field.setEnabled(False)
                self.notes_edit.setEnabled(True)
                obj = self.store.coupling(ident)
                scope = "coupling"
                a = self.store.device(obj["qubit_a"])["label"]
                b = self.store.device(obj["qubit_b"])["label"]
                self.selected_title.setText(f"<span>{tex_to_html(a)} ↔ {tex_to_html(b)} · "
                                            f"{obj['mode']} · {ident[:8]}</span>")
                self.label_edit.setText(f"{a} ↔ {b}")
                self.subtype_edit.clear(); self.subtype_edit.addItem(obj["mode"])
                self.role_edit.setCurrentText("")
                self.x_edit.setValue(0); self.y_edit.setValue(0)
                self.notes_edit.setText(obj["notes"])
            else:
                self.save_button.setEnabled(True)
                self.save_button.setText("保存连接备注")
                for field in (self.label_edit, self.subtype_edit, self.role_edit,
                              self.x_edit, self.y_edit):
                    field.setEnabled(False)
                self.notes_edit.setEnabled(True)
                obj = self.store.link(ident)
                scope = "link"
                q = self.store.device(obj["qubit_id"])["label"]
                c = self.store.device(obj["coupler_id"])["label"]
                self.selected_title.setText(f"<span>{tex_to_html(q)} — {tex_to_html(c)} · "
                                            f"物理连接 · {ident[:8]}</span>")
                self.label_edit.setText(f"{q} — {c}")
                self.subtype_edit.clear(); self.subtype_edit.addItem("比特-耦合器")
                self.role_edit.setCurrentText("")
                self.x_edit.setValue(0); self.y_edit.setValue(0)
                self.notes_edit.setText(obj["notes"])
            self.visible_definitions = self.store.definitions(scope)
            values = self.store.current_values(self.point_combo.currentText() or "default")
            rows = [[d["label"], "未填写" if values.get((ident, d["key"])) is None else values[(ident, d["key"])],
                     d["unit"]] for d in self.visible_definitions]
            _table_fill(self.parameter_table, ["参数", "当前值", "单位"], rows)
        except KeyError:
            self.selected = None
            self.refresh_detail()

    def save_device(self):
        if not self.store or not self.selected:
            return
        try:
            if self.selected[0] == "coupling":
                self.store.update_coupling_notes(self.selected[1], self.notes_edit.text())
                self.refresh_content()
                return
            if self.selected[0] == "link":
                self.store.update_link_notes(self.selected[1], self.notes_edit.text())
                self.refresh_content()
                return
            self.store.update_device(self.selected[1], label=self.label_edit.text().strip(),
                                     subtype=self.subtype_edit.currentText(), role=self.role_edit.currentText(),
                                     x=self.x_edit.value(), y=self.y_edit.value(), notes=self.notes_edit.text())
            self.refresh_content()
        except Exception as exc:
            self._error("保存失败", exc)

    def edit_parameter(self, row: int, _column: int, anchor: QPoint | None = None):
        if not self.store or not self.selected or row >= len(self.visible_definitions):
            return
        definition = self.visible_definitions[row]
        dialog = ParameterDialog(definition, self.point_combo.currentText() or "default", self)
        if anchor is not None:
            dialog.move(anchor)
        old_value = self.store.current_values(self.point_combo.currentText() or "default").get(
            (self.selected[1], definition["key"])
        )
        if old_value is not None:
            dialog.value.setText(str(old_value))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            raw = dialog.value.text().strip()
            value = None if not raw else float(raw) if definition["value_type"] == "number" else raw
            bias = float(dialog.bias.text()) if dialog.bias.text().strip() else None
            point = dialog.point.text().strip()
            self.store.set_parameter(self.selected[1], definition["key"], value,
                                     point=point, bias_value=bias,
                                     bias_unit=dialog.bias_unit.text().strip(), source=dialog.source.text().strip(),
                                     notes=dialog.notes.text().strip())
            if self.point_combo.currentText() != point:
                self.point_combo.setCurrentText(point)
            self.refresh_content()
        except Exception as exc:
            self._error("参数登记失败", exc)

    def add_definition(self):
        if not self.store or not self.selected:
            return
        scope = self.store.device(self.selected[1])["kind"] if self.selected[0] == "device" else self.selected[0]
        dialog = DefinitionDialog(scope, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.store.add_definition(dialog.key.text(), dialog.label.text(), scope,
                                      dialog.value_type.currentData(), dialog.unit.text(),
                                      dialog.category.currentData())
            self._refresh_field_options()
            self.refresh_content()
        except Exception as exc:
            self._error("新增字段失败", exc)

    def delete_selected(self):
        if not self.store or not self.selected:
            return
        if QMessageBox.question(self, "确认删除", "删除所选对象及其关联记录？") != QMessageBox.StandardButton.Yes:
            return
        kind, ident = self.selected
        try:
            if kind == "device":
                self.store.delete_device(ident)
            elif kind == "coupling":
                coupling = self.store.coupling(ident)
                self.store.delete_coupling(ident)
            else:
                self.store.delete_link(ident)
            self.selected = None
            self.refresh_content()
        except Exception as exc:
            self._error("删除失败", exc)

    def refresh_tables(self, *_):
        if not self.store:
            return
        title = self.table_kind.currentText()
        headers, rows = build_tables(self.store, self.point_combo.currentText() or "default")[title]
        self._table_object_ids = [str(row[0]) for row in rows]
        if self.table_orientation.currentData() == "vertical":
            field_indices = list(range(1, len(headers))) if headers and headers[0] in ("ID", "线路 ID") else list(range(len(headers)))
            name_index = {"比特": 1, "耦合器": 1, "比特-耦合器连接": 1,
                          "耦合": 1, "接线": 5}.get(title, 1)
            object_headers = [str(row[name_index]) if name_index < len(row) else str(i+1)
                              for i, row in enumerate(rows)]
            if title == "比特-耦合器连接":
                object_headers = [f"{row[1]} — {row[2]}" for row in rows]
            elif title == "耦合":
                object_headers = [f"{row[1]} — {row[2]}" for row in rows]
            elif title == "接线":
                object_headers = [f"{row[1]} · {row[3]} · {row[5]}" for row in rows]
            field_labels = [header.rsplit(" (", 1)[0] if " (" in header and header.endswith(")")
                            else header for header in (headers[index] for index in field_indices)]
            transposed = [[label] + [row[index] for row in rows]
                          for label, index in zip(field_labels, field_indices)]
            _table_fill(self.data_table, ["参数 / 字段"] + object_headers, transposed)
            for row_index, index in enumerate(field_indices):
                self.data_table.item(row_index, 0).setToolTip(headers[index])
            self.data_table.setColumnWidth(0, 220)
            for col in range(1, self.data_table.columnCount()):
                self.data_table.setColumnWidth(col, 145)
            self.tex_delegate.label_columns = {0}
            self.tex_delegate.label_rows = {
                row_index for row_index, index in enumerate(field_indices)
                if headers[index] in ("名称", "比特", "耦合器", "比特 A", "比特 B", "器件", "对象")
            }
        else:
            _table_fill(self.data_table, headers, rows)
            self.tex_delegate.label_columns = {
                index for index, header in enumerate(headers)
                if header in ("名称", "比特", "耦合器", "比特 A", "比特 B", "器件", "对象")
            }
            self.tex_delegate.label_rows = set()
        for index, header in enumerate(["参数 / 字段"] + object_headers if self.table_orientation.currentData() == "vertical" else headers):
            item = self.data_table.horizontalHeaderItem(index)
            if item:
                item.setData(Qt.ItemDataRole.UserRole, header)
                item.setText("")
        self.data_table.setColumnHidden(0, self.table_orientation.currentData() == "horizontal" and
                                        bool(headers) and headers[0] in ("ID", "线路 ID"))
        self.filter_table()

    def filter_table(self, *_):
        needle = self.filter_edit.text().strip().casefold()
        if self.table_orientation.currentData() == "vertical":
            field_matches = [needle in (self.data_table.item(r, 0).text().casefold()
                                         if self.data_table.item(r, 0) else "")
                             for r in range(self.data_table.rowCount())]
            by_field = bool(needle) and any(field_matches)
            for r in range(self.data_table.rowCount()):
                self.data_table.setRowHidden(r, by_field and not field_matches[r])
            for c in range(1, self.data_table.columnCount()):
                heading = self.data_table.horizontalHeaderItem(c)
                haystack = " ".join([str(heading.data(Qt.ItemDataRole.UserRole) or "") if heading else ""] +
                                    [self.data_table.item(r, c).text() for r in range(self.data_table.rowCount())
                                     if self.data_table.item(r, c)])
                self.data_table.setColumnHidden(c, not by_field and needle not in haystack.casefold())
            return
        for r in range(self.data_table.rowCount()):
            haystack = " ".join(self.data_table.item(r, c).text() for c in range(self.data_table.columnCount())
                                if self.data_table.item(r, c))
            self.data_table.setRowHidden(r, needle not in haystack.casefold())

    def refresh_history(self):
        if self.store:
            headers, rows = build_tables(self.store, include_history=True)["参数历史"]
            _table_fill(self.history_table, headers, rows)

    def refresh_wiring(self):
        if self.store:
            headers, rows = build_tables(self.store)["接线"]
            _table_fill(self.wiring_table, headers, rows)

    def add_line(self):
        if not self.store:
            return
        dialog = LineDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_device = (self.selected[1] if self.selected and self.selected[0] == "device" else None)
        try:
            self.store.add_line(dialog.line_type.currentText(), dialog.cryostat.text(), dialog.port.text(),
                                dialog.channel.text(), dialog.notes.text(),
                                [selected_device] if selected_device else [])
            self.refresh_content()
        except Exception as exc:
            self._error("线路登记失败", exc)

    def _chosen_line_id(self):
        row = self.wiring_table.currentRow()
        return self.wiring_table.item(row, 0).text() if row >= 0 and self.wiring_table.item(row, 0) else None

    def edit_line(self):
        if not self.store or not (line_id := self._chosen_line_id()):
            return
        dialog = LineDialog(self, self.store.line(line_id))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.store.update_line(line_id, dialog.line_type.currentText(), dialog.cryostat.text(),
                                   dialog.port.text(), dialog.channel.text(), dialog.notes.text())
            self.refresh_content()
        except Exception as exc:
            self._error("编辑线路失败", exc)

    def assign_line(self):
        if not self.store or not (line_id := self._chosen_line_id()):
            return
        devices = self.store.devices()
        labels = [d["label"] for d in devices]
        label, ok = QInputDialog.getItem(self, "关联线路", "选择器件", labels, 0, False)
        if ok:
            try:
                ident = next(d["id"] for d in devices if d["label"] == label)
                self.store.assign_line(line_id, ident)
                self.refresh_content()
            except Exception as exc:
                self._error("关联失败", exc)

    def delete_line(self):
        if not self.store or not (line_id := self._chosen_line_id()):
            return
        if QMessageBox.question(self, "确认删除", "删除这条线路及器件关联？") == QMessageBox.StandardButton.Yes:
            self.store.delete_line(line_id)
            self.refresh_content()

    def unassign_line(self):
        if not self.store or not (line_id := self._chosen_line_id()):
            return
        row = self.wiring_table.currentRow()
        label_item = self.wiring_table.item(row, 5)
        if not label_item or not label_item.text():
            return
        device = next((d for d in self.store.devices() if d["label"] == label_item.text()), None)
        if device and QMessageBox.question(self, "解除关联", f"解除 {device['label']} 与该线路的关联？") == QMessageBox.StandardButton.Yes:
            self.store.unassign_line(line_id, device["id"])
            self.refresh_content()

    def backup(self):
        if not self.store:
            return
        path, _ = QFileDialog.getSaveFileName(self, "另存备份", "backup.qchip", "芯片文件 (*.qchip)")
        if path:
            try:
                self.store.backup_to(path)
                self.statusBar().showMessage(f"备份已保存：{path}")
            except Exception as exc:
                self._error("备份失败", exc)

    def save_xlsx(self):
        if not self.store:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 XLSX", "chip_parameters.xlsx", "Excel 工作簿 (*.xlsx)")
        if path:
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            history = QMessageBox.question(self, "历史记录", "同时导出参数历史明细？") == QMessageBox.StandardButton.Yes
            try:
                export_xlsx(self.store, path, self.point_combo.currentText() or "default", history)
                self.statusBar().showMessage(f"已导出：{path}")
            except Exception as exc:
                self._error("XLSX 导出失败", exc)

    def save_csv(self):
        if not self.store:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择 CSV 导出目录")
        if directory:
            if any(Path(directory).glob("*.csv")) and QMessageBox.question(
                self, "覆盖提示", "此目录已有 CSV 文件；同名文件会被覆盖。继续？"
            ) != QMessageBox.StandardButton.Yes:
                return
            history = QMessageBox.question(self, "历史记录", "同时导出参数历史明细？") == QMessageBox.StandardButton.Yes
            try:
                export_csv(self.store, directory, self.point_combo.currentText() or "default", history)
                self.statusBar().showMessage(f"CSV 已导出：{directory}")
            except Exception as exc:
                self._error("CSV 导出失败", exc)

    def save_image(self):
        if not self.store:
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "导出构型/参数图", "chip_map.png", "PNG 图片 (*.png);;SVG 矢量图 (*.svg)")
        if not path:
            return
        if Path(path).suffix.lower() not in (".png", ".svg"):
            path += ".svg" if "SVG" in selected_filter else ".png"
        width, ok = QInputDialog.getInt(self, "图片尺寸", "宽度（像素）", 1800, 400, 10000)
        if not ok:
            return
        try:
            export_diagram(self.store, path, self.field_combo.currentData() or "",
                           self.point_combo.currentText() or "default", width,
                           theme=self.theme)
            self.statusBar().showMessage(f"图片已导出：{path}")
        except Exception as exc:
            self._error("图片导出失败", exc)

    def save_all_images(self):
        if not self.store:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择参数图导出目录")
        if not directory:
            return
        fmt, ok = QInputDialog.getItem(self, "图片格式", "批量格式", ["PNG", "SVG"], 0, False)
        if not ok:
            return
        try:
            fields = ["", "device.subtype", "device.role", "coupling.mode", "wiring.line_type",
                      "wiring.cryostat", "wiring.port", "wiring.channel"]
            fields += [d["key"] for d in self.store.definitions()]
            if any((Path(directory) / f"{field or 'topology'}.{fmt.lower()}").exists() for field in fields):
                if QMessageBox.question(
                    self, "覆盖提示", "目录中已有同名参数图。继续将覆盖这些文件，是否继续？"
                ) != QMessageBox.StandardButton.Yes:
                    return
            for field in fields:
                name = field or "topology"
                export_diagram(self.store, Path(directory) / f"{name}.{fmt.lower()}",
                               field, self.point_combo.currentText() or "default",
                               theme=self.theme)
            self.statusBar().showMessage(f"已导出 {len(fields)} 张图片到 {directory}")
        except Exception as exc:
            self._error("批量导出失败", exc)

    def open_manual(self):
        path = Path(__file__).resolve().parent.parent / "output" / "pdf" / "user-guide.pdf"
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self._error("说明文件不存在", path)

    def closeEvent(self, event):
        if hasattr(self, "device_popup"):
            self.device_popup.close()
        if self.store:
            self.store.close()
        super().closeEvent(event)


def run_gui(path: str | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(path)
    window.show()
    return app.exec()

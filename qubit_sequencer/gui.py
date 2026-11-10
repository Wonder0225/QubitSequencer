"""PySide6 desktop interface for chip topology and parameter registration."""

from __future__ import annotations

import html
import math
import re
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QGuiApplication,
                           QIcon, QPainter, QPalette, QTextDocument, QTextOption)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGraphicsView, QHeaderView, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit,
    QGridLayout, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QStyledItemDelegate, QStyle, QStyleOptionComboBox, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from .diagram import ChipScene, cjk_font_family, export_diagram
from .graphics_interaction import RightPanMixin
from .editors import DeviceEditor, DraftRegistry
from .recent_files import RecentFiles, RecentFilesDialog
from .attachment_ui import AttachmentPanel, AttachmentDialog, install_paste_shortcut
from .design_ui import IMAGE_FILTER, read_design_file
from .exports import build_tables, export_csv, export_xlsx
from . import __version__
from .storage import ChipStore, SCHEMA_VERSION
from .device_types import DEVICE_NAMES, SUBTYPES
from .readout_store import project_to_readout
from .energy_levels import qubit_levels
from .parameter_rules import (BIAS_Z_HEADERS, PARAMETER_HINTS, PI_PRODUCT, format_bias_z,
                              is_secondary_parameter, record_value, uses_bias_z, validate_bias_z)
from .templates import rotated_surface_code, square_grid
from .tex_labels import tex_to_html
from .theme import UI_COLORS, palette, stylesheet
from .ui_icons import icon
from .workbench import WorkbenchLayout
from .undo import EditHistory, undoable
from .undo_ui import UndoController, install_form_undo
from .wiring_ui import LineDialog
from .wiring_store import METADATA, rows_text


TYPE_NAMES = {"fixed_transmon": "固定 Transmon", "tunable_transmon": "可调 Transmon",
              "fixed_coupler": "固定耦合器", "tunable_coupler": "可调耦合器",
              "direct": "直接电容", "coupler": "经耦合器",
              "readout_line": "读取线", "tunable_filter": "可调 filter",
              "horizontal": "横向", "vertical": "纵向", "angled": "自定义角度", "qubit": "比特", "filter": "可调 filter"}
ROLE_NAMES = {"data": "数据比特", "measure_x": "X 测量", "measure_z": "Z 测量"}


def _visible_header(raw: str) -> str:
    return raw.rsplit(" (", 1)[0] if " (" in raw and raw.endswith(")") else raw


def _table_key(headers, row):
    if headers and headers[0] == "线路 ID":
        return (str(row[0]), str(row[5]))
    if headers and headers[0] == "对象 ID":
        return tuple(str(row[i]) for i in (0, 2, 6, 9))
    return str(row[0]) if row else ""


def _table_fill(table: QTableWidget, headers: list[str], rows: list[list[object]], *,
                default_widths: dict[str, int] | None = None) -> None:
    current = table.currentItem()
    selected_key = current.data(Qt.ItemDataRole.UserRole + 1) if current else None
    selected_header = table.horizontalHeaderItem(current.column()).toolTip() if current else None
    scroll_x, scroll_y = table.horizontalScrollBar().value(), table.verticalScrollBar().value()
    widths = {table.horizontalHeaderItem(c).toolTip(): table.columnWidth(c)
              for c in range(table.columnCount()) if table.horizontalHeaderItem(c) and not table.isColumnHidden(c)}
    defaults = {"名称": 128, "对象": 128, "器件": 128, "类型": 140, "线路类型": 140}
    if default_widths:
        defaults.update(default_widths)
    table.setUpdatesEnabled(False)
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels([_visible_header(header) for header in headers])
    table.setRowCount(len(rows))
    selected_row = None
    for r, row in enumerate(rows):
        key = _table_key(headers, row)
        if key == selected_key:
            selected_row = r
        for c, value in enumerate(row):
            displayed = value
            if headers[c] in ("类型", "方式"):
                displayed = TYPE_NAMES.get(value, value)
            elif headers[c] == "角色":
                displayed = ROLE_NAMES.get(value, value)
            item = QTableWidgetItem("" if displayed is None else str(displayed))
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setData(Qt.ItemDataRole.UserRole + 1, key)
            item.setToolTip("未填写" if value is None else str(value))
            if headers and headers[0] in ("ID", "线路 ID", "对象 ID") and headers[c] in (
                    "名称", "比特", "耦合器", "比特 A", "比特 B", "器件", "对象"):
                item.setToolTip(f"{item.toolTip()}\nID: {row[0]}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if headers[c] in ("单位", "偏置单位"):
                item.setForeground(table.palette().color(QPalette.ColorRole.PlaceholderText))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()
    table.verticalHeader().setDefaultSectionSize(34)
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)
    table.setWordWrap(False)
    table.horizontalHeader().setMinimumHeight(44)
    table.horizontalHeader().setMaximumHeight(44)
    for c, header in enumerate(headers):
        table.horizontalHeaderItem(c).setToolTip(header)
        table.horizontalHeaderItem(c).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        fallback = defaults.get(header, min(280, max(92, table.columnWidth(c) + 8)))
        table.setColumnWidth(c, widths.get(header, fallback))
    if headers and headers[0] in ("ID", "线路 ID", "对象 ID"):
        table.setColumnHidden(0, True)
    if selected_row is not None and selected_header in headers:
        table.setCurrentCell(selected_row, headers.index(selected_header))
    table.horizontalScrollBar().setValue(scroll_x)
    table.verticalScrollBar().setValue(scroll_y)
    table.setUpdatesEnabled(True)


def _header_html(raw: str, muted: str) -> str:
    visible = _visible_header(raw)
    if " [" in visible and visible.endswith("]"):
        name, unit = visible.rsplit(" [", 1)
        return f"{tex_to_html(name)}<br><span style='color:{muted};font-weight:400'>[{html.escape(unit)}</span>"
    return tex_to_html(visible)


def _dialog_buttons(layout, dialog, label="保存"):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(label)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("role", "primary")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    if isinstance(layout, QFormLayout):
        layout.addRow(buttons)
    else:
        layout.addWidget(buttons)
    install_form_undo(dialog)
    return buttons


class CanvasToolbar(QWidget):
    """Keep readable commands in one row, or two when the canvas is narrow."""

    def __init__(self, modes, layer, fit, parent=None):
        super().__init__(parent)
        self.setObjectName("canvasToolbar")
        self.modes, self.layer, self.fit = modes, layer, fit
        self.grid = QGridLayout(self)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setSpacing(8)
        self.narrow = None
        self._arrange(False)

    def minimumSizeHint(self):
        return QSize(self.modes.minimumSizeHint().width() + self.fit.minimumSizeHint().width() + 24, 48)

    def _arrange(self, narrow):
        if self.narrow == narrow:
            return
        self.narrow = narrow
        for widget in (self.modes, self.layer, self.fit):
            self.grid.removeWidget(widget)
        self.grid.setColumnStretch(1, 0 if narrow else 1)
        self.grid.addWidget(self.modes, 0, 0)
        self.grid.addWidget(self.fit, 0 if narrow else 0, 1 if narrow else 2)
        self.grid.addWidget(self.layer, 1 if narrow else 0, 0 if narrow else 1,
                            1, 2 if narrow else 1)
        self.grid.setColumnStretch(0, 1 if narrow else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        required = (self.modes.minimumSizeHint().width() + self.layer.minimumSizeHint().width()
                    + self.fit.minimumSizeHint().width() + 32)
        self._arrange(event.size().width() < max(640, required))


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

    def sizeHint(self, option, index):
        original = super().sizeHint(option, index)
        return QSize(original.width(), max(32, original.height()))

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

class TexComboBox(QComboBox):
    """Render field names with the same TeX subset as tables and diagrams."""

    def __init__(self, parent=None):
        super().__init__(parent)
        delegate = TexTableDelegate(self); delegate.label_columns = {0}
        self.setItemDelegate(delegate)
        self.view().setUniformItemSizes(True)
        self.currentIndexChanged.connect(lambda *_: self.setToolTip(self.currentText()))

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        raw = option.currentText
        option.currentText = ""
        painter = QPainter(self)
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option, painter, self)
        rect = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                          QStyle.SubControl.SC_ComboBoxEditField, self)
        document = QTextDocument()
        document.setDocumentMargin(0); document.setDefaultFont(self.font())
        text_option = QTextOption(); text_option.setWrapMode(QTextOption.WrapMode.NoWrap)
        document.setDefaultTextOption(text_option)
        color = option.palette.color(QPalette.ColorRole.Text).name()
        document.setHtml(f"<span style='color:{color}'>{tex_to_html(raw)}</span>")
        painter.setClipRect(rect)
        painter.translate(rect.left()+2, rect.top()+max(0, (rect.height()-document.size().height())/2))
        document.drawContents(painter, QRectF(0, 0, rect.width()-4, rect.height()))
        painter.end()


class ChipView(RightPanMixin, QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        # The procedural grid and animated glyphs share the canvas with footer
        # annotations. Repaint it together rather than erasing sparse dirty regions.
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._fit_active = False
        self._fit_scheduled = False
        self._fitting = False

    def fit_chip(self):
        scene = self.scene()
        if not isinstance(scene, ChipScene) or self._fitting:
            return
        self._fit_active = True
        self._fitting = True
        try:
            scene.layout_footer()
            self.fitInView(scene.content_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._extend()
            self.viewport().update()
        finally:
            self._fitting = False

    def _refit_after_resize(self):
        self._fit_scheduled = False
        if self._fit_active:
            self.fit_chip()

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
        if self._fit_active and not self._fitting and not self._fit_scheduled:
            self._fit_scheduled = True
            QTimer.singleShot(0, self._refit_after_resize)

    def mousePressEvent(self, event):
        self._drag_origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, "_drag_origin")
                and (event.position().toPoint() - self._drag_origin).manhattanLength() > 4):
            self._fit_active = False
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.15 if delta > 0 else 1 / 1.15)
        event.accept()

    def zoom_by(self, factor: float):
        target = self.transform().m11() * factor
        if 0.08 <= target <= 8.0:
            self._fit_active = False
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
        self.design_path = QLineEdit(); self.design_path.setReadOnly(True)
        self.design_path.setPlaceholderText("可选：创建时导入芯片设计图")
        design_row = QHBoxLayout()
        design_row.addWidget(self.design_path)
        choose_design = QPushButton("选择图片…")
        choose_design.clicked.connect(self.choose_design)
        design_row.addWidget(choose_design)
        clear_design = QPushButton("清除")
        clear_design.clicked.connect(self.design_path.clear)
        design_row.addWidget(clear_design)
        form.addRow("芯片设计图", design_row)
        self.error = QLabel(); self.error.setProperty("role", "danger"); self.error.setWordWrap(True)
        form.addRow(self.error)
        _dialog_buttons(form, self, "创建芯片")
        self.template.currentIndexChanged.connect(self._template_changed)
        self._template_changed()

    def choose_design(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择芯片设计图", "", IMAGE_FILTER)
        if path:
            self.design_path.setText(path)

    def _template_changed(self, *_):
        surface = self.template.currentIndex() == 2
        form = self.layout()
        for widget, visible in ((self.rows, not surface), (self.columns, not surface),
                                (self.distance, surface), (self.mode, self.template.currentIndex() != 0)):
            form.setRowVisible(widget, visible)

    def accept(self):
        if not self.name.text().strip():
            self.error.setText("请填写芯片名称")
            return
        if self.template.currentIndex() == 2 and self.distance.value() % 2 == 0:
            self.error.setText("旋转表面码的码距必须为奇数")
            return
        super().accept()


class ParameterDialog(QDialog):
    def __init__(self, definition, point: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("登记参数")
        self.definition = definition
        layout = QVBoxLayout(self)
        self.parameter_title = QLabel(tex_to_html(definition["label"]))
        self.parameter_title.setTextFormat(Qt.TextFormat.RichText)
        self.parameter_title.setProperty("role", "heading")
        self.parameter_title.setToolTip(definition["key"])
        layout.addWidget(self.parameter_title)
        self.value = QLineEdit()
        self.value.setPlaceholderText("留空表示未填写")
        self.point = QLineEdit(point)
        self.bias = QLineEdit()
        self.bias_unit = QLineEdit()
        bias_z = uses_bias_z(definition)
        if bias_z:
            self.bias.setPlaceholderText("零偏置填 0")
            self.bias_unit.setPlaceholderText("原始控制值可留空")
        self.source = QLineEdit()
        self.notes = QLineEdit()
        fields = QGridLayout()
        for row, (left_label, left, right_label, right) in enumerate((
            (f"值 ({definition['unit'] or '无单位'})", self.value, "工作点", self.point),
            ("BiasZ" if bias_z else "偏置值", self.bias, "偏置单位", self.bias_unit),
            ("来源/实验", self.source, "备注", self.notes),
        )):
            fields.addWidget(QLabel(left_label), row, 0); fields.addWidget(left, row, 1)
            fields.addWidget(QLabel(right_label), row, 2); fields.addWidget(right, row, 3)
        fields.setColumnStretch(1, 1); fields.setColumnStretch(3, 1)
        layout.addLayout(fields)
        hint = ("登记频率时请同时填写该次测量的 BiasZ；仅在已知时填写单位。"
                if bias_z else PARAMETER_HINTS.get(definition["key"]))
        if hint:
            label = QLabel(hint); label.setWordWrap(True); label.setProperty("role", "muted")
            layout.addWidget(label)
        self.attachments = AttachmentPanel(parent=self)
        self.attachments.setMinimumHeight(250)
        layout.addWidget(QLabel("附件（可选）"))
        layout.addWidget(self.attachments, 1)
        self._paste_shortcut = install_paste_shortcut(self, self.attachments)
        self.resize(960, 690)
        self.error = QLabel(); self.error.setProperty("role", "danger"); self.error.setWordWrap(True)
        layout.addWidget(self.error)
        _dialog_buttons(layout, self, "登记参数")

    def accept(self):
        try:
            for field in ([self.value] if self.definition["value_type"] == "number" else []) + [self.bias]:
                if field.text().strip() and not math.isfinite(float(field.text())):
                    raise ValueError("数值必须是有限的数字")
            if not self.point.text().strip():
                raise ValueError("请填写工作点名称")
            validate_bias_z(self.definition, self.value.text().strip() or None, self.bias.text().strip() or None)
            self.attachments.inputs()
        except ValueError as exc:
            self.error.setText("请检查数值和偏置值" if "could not convert" in str(exc) else str(exc))
            return
        self.error.clear()
        super().accept()

    def reject(self):
        if self.attachments.busy:
            self.error.setText("附件正在读取，请完成后再关闭。")
            return
        super().reject()


class CustomDeviceDialog(QDialog):
    def __init__(self, label, types, parent=None):
        super().__init__(parent)
        self.setWindowTitle("放置自定义元件")
        form = QFormLayout(self)
        self.name = QLineEdit(label)
        self.name.selectAll()
        self.subtype = QComboBox()
        self.subtype.setEditable(True)
        self.subtype.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.subtype.addItems(types)
        self.subtype.setCurrentIndex(-1)
        self.subtype.lineEdit().setPlaceholderText("例如：测试电容、直通线")
        form.addRow("名称", self.name)
        form.addRow("自定义类型", self.subtype)
        button = _dialog_buttons(form, self, "放置元件").button(QDialogButtonBox.StandardButton.Ok)

        def update_button():
            button.setEnabled(bool(self.name.text().strip() and self.subtype.currentText().strip()))

        self.name.textChanged.connect(update_button)
        self.subtype.currentTextChanged.connect(update_button)
        update_button()


class DefinitionDialog(QDialog):
    def __init__(self, scope: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加自定义参数")
        layout = QFormLayout(self)
        self.key = QLineEdit(); self.key.setPlaceholderText(f"{scope}.my_parameter")
        self.label = QLineEdit()
        self.label.setPlaceholderText(r"例如 f_{10}、T_{2}^{*}、\omega_q")
        self.preview = QLabel(); self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.label.textChanged.connect(lambda text: self.preview.setText(tex_to_html(text)))
        self.value_type = QComboBox(); self.value_type.addItem("数值", "number"); self.value_type.addItem("文本", "text")
        self.unit = QLineEdit()
        self.category = QComboBox(); self.category.addItem("自定义", "custom")
        self.category.addItem("设计", "design"); self.category.addItem("测量", "measured")
        layout.addRow("参数键（唯一）", self.key)
        layout.addRow("显示名称", self.label)
        layout.addRow("公式预览", self.preview)
        layout.addRow("类型", self.value_type)
        layout.addRow("单位", self.unit)
        layout.addRow("分类", self.category)
        _dialog_buttons(layout, self, "添加参数")


def _apply_secondary_visibility(table, definitions, checkbox):
    secondary = [is_secondary_parameter(d["key"]) for d in definitions]
    checkbox.setVisible(any(secondary))
    for index, hidden_by_default in enumerate(secondary):
        table.setRowHidden(index, hidden_by_default and not checkbox.isChecked())


def _annotate_bias_z(table, definitions, records, ident):
    for index, definition in enumerate(definitions):
        if uses_bias_z(definition):
            bias = format_bias_z(records.get((ident, definition["key"])))
            if bias is not None:
                item = table.item(index, 1)
                item.setToolTip(f"{item.text()} {definition['unit']} @ BiasZ {bias}")


class DevicePopup(QDialog):
    """Compact editor positioned next to the device that was clicked."""

    def __init__(self, host):
        super().__init__(host, Qt.WindowType.Popup)
        self.host = host
        self.device_id: str | None = None
        self.definitions = []
        self.setWindowTitle("编辑芯片元件")
        self.resize(390, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setObjectName("popupTitle")
        layout.addWidget(self.title)
        self.editor = DeviceEditor(host.drafts)
        for name in ("label", "subtype", "role", "x", "y", "notes"):
            setattr(self, name, getattr(self.editor, name))
        fields = QScrollArea(); fields.setWidgetResizable(True)
        fields.setFrameShape(QScrollArea.Shape.NoFrame)
        fields.setMinimumHeight(130); fields.setMaximumHeight(280)
        fields.setWidget(self.editor); layout.addWidget(fields)
        self.readout_info = QLabel(); self.readout_info.setWordWrap(True); self.readout_info.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.readout_info)
        layout.addWidget(QLabel("参数 · 双击一行登记新值"))
        self.secondary_parameters = QCheckBox("显示次要参数")
        layout.addWidget(self.secondary_parameters)
        self.parameters = QTableWidget(0, 2)
        delegate = TexTableDelegate(self.parameters); delegate.label_columns = {0}
        self.parameters.setItemDelegate(delegate)
        self.parameters.setMinimumHeight(130)
        self.parameters.setAlternatingRowColors(True)
        self.parameters.cellDoubleClicked.connect(self.edit_parameter)
        layout.addWidget(self.parameters, 1)
        self.secondary_parameters.toggled.connect(host.secondary_parameters.setChecked)
        host.secondary_parameters.toggled.connect(self.secondary_parameters.setChecked)
        self.secondary_parameters.toggled.connect(lambda: _apply_secondary_visibility(
            self.parameters, self.definitions, self.secondary_parameters))
        buttons = QHBoxLayout()
        self.save_button = QPushButton("保存更改"); self.save_button.clicked.connect(self.save)
        self.save_button.setProperty("role", "primary")
        self.reset_button = QPushButton("取消更改"); self.reset_button.clicked.connect(self.reset)
        close = QPushButton("关闭"); close.clicked.connect(self.close)
        buttons.addWidget(self.save_button); buttons.addWidget(self.reset_button); buttons.addWidget(close)
        layout.addLayout(buttons)
        host.drafts.changed.connect(self._update_buttons)

    def show_device(self, ident: str, anchor: QPoint) -> None:
        self.device_id = ident
        if not self.reload():
            return
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.resize(min(390, area.width()-24), min(560, area.height()-24))
            x = max(area.left(), min(anchor.x()+12, area.right()-self.width()))
            y = max(area.top(), min(anchor.y()+12, area.bottom()-self.height()))
            self.move(x, y)
        self.show()
        self.raise_()

    def reload(self) -> bool:
        if not self.host.store or not self.device_id:
            self.editor.clear(); self.device_id = None
            self.close()
            return False
        try:
            device = self.host.store.device(self.device_id)
        except KeyError:
            self.editor.clear(); self.device_id = None
            self.close()
            return False
        self.title.setText(f"<span>{tex_to_html(device['label'])}  ·  "
                           f"{DEVICE_NAMES[device['kind']]}</span>")
        self.editor.bind("device", self.device_id)
        self.readout_info.setText("\n".join(filter(None, (self.host.store.readout_summary(self.device_id),
                                                        self.host.store.sample_box_summary(self.device_id)))))
        self.readout_info.setVisible(bool(self.readout_info.text()))
        point = self.host.point_combo.currentText() or "default"
        records = self.host.store.current_records(point)
        values = {key: record_value(record) for key, record in records.items()}
        self.definitions = self.host.store.definitions(device["kind"])
        rows = [[f"{d['label']} [{d['unit']}]" if d["unit"] else d["label"],
                 "未填写" if values.get((self.device_id, d["key"])) is None
                 else values[(self.device_id, d["key"])] ] for d in self.definitions]
        _table_fill(self.parameters, ["参数", "当前值"], rows, default_widths={"参数": 220, "当前值": 100})
        _apply_secondary_visibility(self.parameters, self.definitions, self.secondary_parameters)
        _annotate_bias_z(self.parameters, self.definitions, records, self.device_id)
        self._update_buttons()
        return True

    def _update_buttons(self, *_):
        dirty = bool(self.device_id and self.host.store and self.host.drafts.dirty("device", self.device_id))
        self.save_button.setEnabled(dirty)
        self.reset_button.setEnabled(dirty)

    def reset(self):
        if self.host.store and self.device_id:
            self.host.drafts.reset("device", self.device_id)

    def save(self) -> None:
        if not self.host.store or not self.device_id:
            return
        try:
            self.host.drafts.save("device", self.device_id)
            self.host.selected = ("device", self.device_id)
            self.host.refresh_content()
            self.host._written("器件信息")
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


class MainWindow(QMainWindow, WorkbenchLayout):
    def __init__(self, path: str | None = None):
        super().__init__()
        app = QApplication.instance()
        if app:
            ui_font = QFont(cjk_font_family())
            ui_font.setPixelSize(14)
            ui_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
            app.setFont(ui_font)
            icon = QIcon(str(Path(__file__).resolve().parent / "assets" / "app-icon.png"))
            app.setWindowIcon(icon)
            self.setWindowIcon(icon)
        self.store: ChipStore | None = None
        self.fridge_panel = None
        self.scene: ChipScene | None = None
        self._initial_fit_pending = False
        self.selected: tuple[str, str] | None = None
        self.first_endpoint: str | None = None
        self.readout_start = None
        self.visible_definitions = []
        self._table_parameter_visibility = {}
        self.settings = QSettings("QubitSequencer", "QubitSequencer")
        self.recent_files = RecentFiles(self.settings)
        self.drafts = DraftRegistry(lambda: self.store, self)
        self.drafts.changed.connect(self._update_ui_state)
        self.undo_history = EditHistory(self._update_undo_action)
        self.drafts.undo_history = self.undo_history
        self.undo_history.add_state(self.drafts.undo_state, self.drafts.restore_undo_state)
        self.undo_history.add_state(lambda: self.selected, lambda selected: setattr(self, "selected", selected))
        self.theme = "dark" if self.settings.value("theme", "light") == "dark" else "light"
        self.setWindowTitle("Qubit Sequencer 芯片登记")
        self.resize(1450, 900)
        self._build_ui()
        from .fridge_store import FridgeStore
        self._show_fridge(FridgeStore.preview(), activate=False, preview=True)
        self.cancel_placement_action = QAction(self)
        self.cancel_placement_action.setShortcut("Esc")
        self.cancel_placement_action.triggered.connect(self._cancel_current_placement)
        self.addAction(self.cancel_placement_action)
        self.view.setMouseTracking(True)
        geometry = self.settings.value("layout/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        screen = QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
            self.move(max(area.left(), min(self.x(), area.right()-self.width()+1)),
                      max(area.top(), min(self.y(), area.bottom()-self.height()+1)))
        self.dark_action.blockSignals(True)
        self.dark_action.setChecked(self.theme == "dark")
        self.dark_action.blockSignals(False)
        self.set_theme(self.theme, persist=False)
        self.device_popup = DevicePopup(self)
        self.undo_controller = UndoController(self)
        page = self.settings.value("layout/lastPageKey", "", type=str)
        if not page:
            previous = self.settings.value("layout/lastPage", 0, type=int)
            keys = ("topology", "data", "fridge", "history")
            page = keys[previous] if 0 <= previous < len(keys) else "topology"
        keys = [self.navigation.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.navigation.count())]
        if page in keys:
            self.navigation.setCurrentRow(keys.index(page))
        saved_pages = self.settings.value("layout/workspacePages", [], type=list)
        saved_widths = self.settings.value("layout/workspaceWidths", [], type=list)
        self.tabs.restore_layout([keys.index(key) if key in keys else -1 for key in saved_pages], saved_widths)
        if path:
            if Path(path).suffix.lower() == ".qfridge":
                self._open_fridge_path(path)
            else:
                self._open_path(path)
        else:
            self._restore_workspace_projects()

    def _restore_workspace_projects(self):
        unavailable = []
        for key, open_project in (("chipPath", self._open_path), ("fridgePath", self._open_fridge_path)):
            filename = self.settings.value(f"workspace/{key}", "", type=str)
            if filename:
                try:
                    open_project(filename, restoring=True)
                except Exception as exc:
                    unavailable.append(f"{Path(filename).name}（{exc}）")
        if self.store:
            self.point_combo.setCurrentText(self.settings.value("workspace/workingPoint", "default", type=str))
            view = self.settings.value("workspace/chipView", 0, type=int)
            if 0 < view < self.chip_views.count():
                self._show_chip_view(view)
                self._refresh_energy_levels()
        if not self.fridge_panel.preview:
            self.fridge_panel.open_board(self.settings.value("workspace/fridgeBoard", "", type=str))
            view = self.settings.value("workspace/fridgeView", 0, type=int)
            if 0 <= view < self.fridge_panel.views.count():
                self.fridge_panel.views.setCurrentIndex(view)
        if unavailable:
            self.statusBar().showMessage("上次的项目未能恢复：" + "；".join(unavailable), 15000)

    def set_theme(self, mode: str, persist: bool = True) -> None:
        self.theme = "dark" if mode == "dark" else "light"
        app = QApplication.instance()
        if app:
            app.setPalette(palette(self.theme))
        self.setStyleSheet(stylesheet(self.theme))
        colors = UI_COLORS[self.theme]
        self.detail_menu.setStyleSheet(
            f"QMenu::item {{ color: {colors['danger']}; }}"
            f"QMenu::item:disabled {{ color: {colors['disabled_text']}; }}")
        for control, name in self._icon_bindings:
            control.setIcon(icon("sun" if control is self.dark_action and self.theme == "dark" else name,
                                 UI_COLORS[self.theme]["text"]))
        self.data_table.horizontalHeader().setProperty("darkMode", self.theme == "dark")
        self.data_table.horizontalHeader().viewport().update()
        for table in (self.parameter_table, self.data_table, self.wiring_table, self.history_table):
            for column in range(table.columnCount()):
                header = table.horizontalHeaderItem(column)
                if header and header.toolTip() in ("单位", "偏置单位"):
                    for row in range(table.rowCount()):
                        item = table.item(row, column)
                        if item:
                            item.setForeground(QColor(UI_COLORS[self.theme]["muted"]))
        if self.dark_action.isChecked() != (self.theme == "dark"):
            self.dark_action.blockSignals(True)
            self.dark_action.setChecked(self.theme == "dark")
            self.dark_action.blockSignals(False)
        if persist:
            self.settings.setValue("theme", self.theme)
        if self.store:
            self.refresh_scene()
            self._refresh_energy_levels()
        if self.fridge_panel:
            self.fridge_panel.set_theme(self.theme)
        self.sample_box_panel.refresh()

    def _sync_mode(self, chosen: QAction, checked: bool):
        actions = (self.view_action, self.place_action, self.place_coupler_action,
                   self.place_readout_action, self.place_filter_action, self.place_custom_action, self.connect_action)
        if checked:
            for action in actions:
                if action is not chosen:
                    action.blockSignals(True); action.setChecked(False); action.blockSignals(False)
        elif not any(action.isChecked() for action in actions):
            self.view_action.blockSignals(True); self.view_action.setChecked(True); self.view_action.blockSignals(False)
        self.first_endpoint = None
        self._cancel_readout_placement()
        if checked and chosen in (self.place_readout_action, self.place_filter_action):
            self.readout_visible_action.setChecked(True)
        if hasattr(self, "device_popup"):
            self.device_popup.close()
        hints = {self.view_action: "查看模式 · 左键点选或框选，右键拖动平移，滚轮缩放",
                 self.place_action: "放置比特 · 点击空格点",
                 self.place_coupler_action: "放置耦合器 · 点击空格点或半格",
                 self.place_readout_action: "放置读取线 · 依次点击起点和终点，吸附格点／半格 · Esc 取消",
                 self.place_filter_action: "放置可调 filter · 点击空格点或半格（可选元件）",
                 self.place_custom_action: "放置自定义元件 · 点击空格点或半格",
                 self.connect_action: "连接模式 · 依次点击两个器件"}
        active = next(action for action in actions if action.isChecked())
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag if active is self.view_action else QGraphicsView.DragMode.NoDrag)
        if hasattr(self, "mode_hint"):
            self.mode_hint.setText(hints[active])
        self._update_ui_state()

    def fit_diagram(self):
        if self.scene:
            self.view.fit_chip()

    def _cancel_readout_placement(self):
        self.readout_start = None
        if self.scene:
            self.scene.set_readout_start(None)
        if self.place_readout_action.isChecked():
            self.mode_hint.setText("放置读取线 · 依次点击起点和终点，吸附格点／半格 · Esc 取消")

    def _set_readout_visible(self, visible):
        self.first_endpoint = None
        self._cancel_readout_placement()
        self.design_panel.readout_visible = visible
        if not visible and self.design_panel.device_kind.currentData() in ("readout", "filter"):
            self.design_panel.device_kind.setCurrentIndex(0)
        self.design_panel.refresh_markers()
        if self.store:
            self.refresh_scene()

    def _error(self, title: str, exc: Exception | str):
        QMessageBox.critical(self, title, str(exc))

    def _update_undo_action(self):
        if hasattr(self, "undo_action"):
            panel = self._active_fridge_panel()
            history = panel.history if panel else self.undo_history
            available = not panel or (not panel.preview and not panel.is_overview())
            label = history.label if available else ""
            self.undo_action.setText(f"撤回{label}" if label else "撤回")
            self.undo_action.setEnabled(bool(label))
            label = history.redo_label if available else ""
            self.redo_action.setText(f"恢复{label}" if label else "恢复（重做）")
            self.redo_action.setEnabled(bool(label))

    def undo_edit(self):
        self._replay_edit(False)

    def redo_edit(self):
        self._replay_edit(True)

    def _replay_edit(self, redo):
        panel = self._active_fridge_panel()
        if panel:
            panel.redo() if redo else panel.undo()
            return
        self._cancel_readout_placement()
        steps = self.undo_history.redo_steps if redo else self.undo_history.steps
        if not steps:
            return
        try:
            rows = steps[-1].rows
            label = self.undo_history.redo() if redo else self.undo_history.undo()
            self.first_endpoint = None
            self._refresh_field_options()
            if any(table == "chip_design" for table, _ in rows):
                self.design_panel.reload_image()
            self.refresh_content()
            if rows:
                self._written(f"{'恢复' if redo else '撤回'}{label}，结果")
            else:
                self.statusBar().showMessage(f"已{'恢复' if redo else '撤回'}{label}", 5000)
        except Exception as exc:
            self._error("恢复失败" if redo else "撤回失败", exc)

    def _leave_file(self):
        """Resolve all metadata drafts before replacing or closing a file."""
        if not self.store or not self.drafts.dirty_keys():
            return True
        prompt = QMessageBox(self)
        prompt.setWindowTitle("尚有未保存的更改")
        prompt.setText(f"{len(self.drafts.dirty_keys())} 个对象有未保存的更改。")
        save = prompt.addButton("保存全部", QMessageBox.ButtonRole.AcceptRole)
        discard = prompt.addButton("放弃更改", QMessageBox.ButtonRole.DestructiveRole)
        prompt.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(save)
        prompt.exec()
        if prompt.clickedButton() is discard:
            self.detail_editor.clear()
            if hasattr(self, "device_popup"):
                self.device_popup.editor.clear()
            self.drafts.clear()
            self.refresh_detail()
            if hasattr(self, "device_popup") and self.device_popup.isVisible():
                self.device_popup.reload()
            return True
        if prompt.clickedButton() is not save:
            return False
        try:
            # Each committed object is removed from the dirty set. A failed
            # object and all remaining objects keep their inputs for retry.
            for key in self.drafts.dirty_keys():
                self.drafts.save(*key)
            self._written("全部更改")
            return True
        except Exception as exc:
            self._error("保存失败", exc)
            return False

    def _open_path(self, path: str, *, restoring=False):
        if not self._leave_file():
            return False
        try:
            incoming = ChipStore.open(path)
            if hasattr(self, "device_popup"):
                self.device_popup.close()
                self.device_popup.editor.clear()
                self.device_popup.device_id = None
            self.detail_editor.clear()
            self.selected = None
            self.drafts.clear()
            self.undo_history.bind(incoming.db)
            incoming.undo_history = self.undo_history
            if self.store:
                self.store.close()
            self.store = incoming
            self._cancel_readout_placement()
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
            self.project_name.setText(self.store.meta()["chip_name"])
            self.project_name.setToolTip(f"{self.store.meta()['chip_name']}\n{Path(path).resolve()}")
            self.file_status.setText(f"{Path(path).name} · 已加载")
            self.file_status.setToolTip(str(Path(path).resolve()))
            self.statusBar().showMessage(str(path))
            self._update_ui_state()
            self.recent_files.record(path)
            self._refresh_recent_menu()
            return True
        except Exception as exc:
            if restoring:
                raise
            self._error("无法打开文件", exc)

    def new_chip(self):
        if not self._leave_file():
            return
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
            design = read_design_file(dialog.design_path.text()) if dialog.design_path.text() else None
            store = ChipStore.create(path, name, kind)
            created = True
            if kind == "surface":
                rotated_surface_code(store, dialog.distance.value(), dialog.mode.currentData())
            elif kind == "square":
                square_grid(store, dialog.rows.value(), dialog.columns.value(), dialog.mode.currentData())
            else:
                store.set_meta("grid_rows", str(dialog.rows.value()))
                store.set_meta("grid_columns", str(dialog.columns.value()))
            if design:
                store.set_chip_design(*design)
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

    def new_fridge(self):
        from .fridge_store import FridgeStore
        name, ok = QInputDialog.getText(self, "新建制冷机项目", "制冷机名称")
        if not ok or not name.strip():
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存制冷机项目", f"{name}.qfridge", "制冷机项目 (*.qfridge)")
        if not path:
            return
        if not path.lower().endswith(".qfridge"):
            path += ".qfridge"
        try:
            use_preset = QMessageBox.question(self, "低温线路预设", "为这台制冷机应用内置低温线路预设？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
            self._show_fridge(FridgeStore.create(path, name.strip(), with_preset=use_preset))
        except Exception as exc:
            self._error("创建制冷机项目失败", exc)

    def open_fridge(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开制冷机项目", "", "制冷机项目 (*.qfridge)")
        if path:
            self._open_fridge_path(path)

    def _open_fridge_path(self, path, *, restoring=False):
        from .fridge_store import FridgeStore
        try:
            self._show_fridge(FridgeStore.open(path), activate=not restoring)
        except Exception as exc:
            if restoring:
                raise
            self._error("打开制冷机项目失败", exc)

    def _show_fridge(self, store, *, activate=True, preview=False):
        from .fridge_ui import FridgePanel
        try:
            panel = FridgePanel(store, self.fridge_workspace, host=self, preview=preview)
        except Exception:
            store.close()
            raise
        old = self.fridge_panel
        self.fridge_panel = panel
        self.fridge_workspace.addWidget(panel)
        self.fridge_workspace.setCurrentWidget(panel)
        if old:
            self.fridge_workspace.removeWidget(old)
            old.close(); old.deleteLater()
        self.fridge_title.setText("默认盘位预览 · 请新建或打开项目" if preview else store.name())
        self.fridge_title.setToolTip("" if preview else str(store.path))
        if activate:
            self.tabs.setCurrentWidget(self.fridge_page)
        if self.tabs.is_page_visible(self.sample_box_panel):
            self.sample_box_panel.refresh_connections()
        self._update_undo_action()

    def _active_fridge_panel(self):
        if (self.fridge_panel and hasattr(self, "tabs") and self.tabs.currentWidget() is self.fridge_page
                and self.fridge_workspace.currentWidget() is self.fridge_panel):
            return self.fridge_panel
        return None

    def _cancel_current_placement(self):
        panel = self._active_fridge_panel()
        if panel and panel.placing_port:
            panel.cancel_placement()
        else:
            self._cancel_readout_placement()

    def _refresh_recent_menu(self):
        self.recent_menu.clear()
        paths = self.recent_files.paths()
        if not paths:
            self.recent_menu.addAction("暂无最近打开的文件").setEnabled(False)
        for number, raw in enumerate(paths[:10], 1):
            path = Path(raw)
            directory = self.fontMetrics().elidedText(str(path.parent), Qt.TextElideMode.ElideMiddle, 300)
            text = f"{path.name}  —  {directory}".replace("&", "&&")
            action = self.recent_menu.addAction(f"&{number} {text}" if number < 10 else text)
            action.setToolTip(raw); action.setStatusTip(raw)
            action.setEnabled(path.is_file())
            action.triggered.connect(lambda checked=False, chosen=raw: self._open_path(chosen))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction(self.recent_action)
        clear = self.recent_menu.addAction("清除最近打开的记录", self.clear_recent_files)
        clear.setEnabled(bool(paths))

    def clear_recent_files(self):
        self.recent_files.clear()
        self._refresh_recent_menu()
        self.statusBar().showMessage("最近文件记录已清除", 4000)

    def open_recent(self):
        dialog = RecentFilesDialog(self.recent_files, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.other_requested:
                self.open_chip()
            elif dialog.selected_path():
                self._open_path(dialog.selected_path())
        self._refresh_recent_menu()

    def _refresh_field_options(self):
        if not self.store:
            return
        old = self.field_combo.currentData()
        self.field_combo.blockSignals(True)
        self.field_combo.clear(); self.field_combo.addItem("原始构型", "")
        scope_order = {"qubit": 0, "coupling": 1, "link": 2, "coupler": 3, "readout": 4, "filter": 5, "custom": 6}
        for definition in sorted(self.store.definitions(), key=lambda d: scope_order[d["scope"]]):
            scope = {**DEVICE_NAMES, "link": "连接", "coupling": "两比特耦合"}[definition["scope"]]
            suffix = f" [{definition['unit']}]" if definition["unit"] else ""
            self.field_combo.addItem(f"{scope} · {definition['label']}{suffix}", definition["key"])
            self.field_combo.setItemData(self.field_combo.count()-1, definition["key"], Qt.ItemDataRole.ToolTipRole)
        for label, key in (("器件类型", "device.subtype"), ("表面码角色", "device.role"),
                           ("耦合方式", "coupling.mode"), ("接线类型", "wiring.line_type"),
                           ("制冷机", "wiring.cryostat"), ("制冷机端口", "wiring.port"),
                           ("线路通道", "wiring.channel")):
            self.field_combo.addItem(label, key)
        index = self.field_combo.findData(old)
        self.field_combo.setCurrentIndex(max(0, index))
        self.field_combo.blockSignals(False)

    def refresh_content(self, *_):
        if self.store:
            self.drafts.discard_missing()
        self.refresh_scene()
        self.refresh_tables()
        self.refresh_history()
        self.refresh_wiring()
        self.refresh_detail()
        self.design_panel.refresh(self.store)
        if self.tabs.is_page_visible(self.sample_box_panel):
            self.sample_box_panel.refresh()
        self._refresh_energy_levels()
        if hasattr(self, "device_popup") and self.device_popup.isVisible():
            self.device_popup.reload()
        self._update_ui_state()

    def refresh_parameter_views(self, *_):
        """Refresh workpoint-dependent values without rebuilding static wiring."""
        self.refresh_scene()
        self.refresh_tables()
        self.refresh_detail()
        self._refresh_energy_levels()
        if hasattr(self, "device_popup") and self.device_popup.isVisible():
            self.device_popup.reload()
        self._update_ui_state()

    def on_design_device_clicked(self, ident: str, anchor: QPoint):
        self.selected = ("device", ident)
        self.refresh_detail()
        self.device_popup.show_device(ident, anchor)

    def show_energy_levels(self):
        if not self.store:
            return
        visible = self.chip_views.currentWidget() is not self.energy_panel
        if visible:
            self._refresh_energy_levels(force=True)
        self._show_chip_view(2 if visible else 0)

    def _refresh_energy_levels(self, *, force=False):
        if self.store and (force or self.chip_views.currentWidget() is self.energy_panel):
            point = self.point_combo.currentText() or "default"
            frequency_key = self.energy_panel.frequency_source.currentData()
            self.energy_panel.refresh(qubit_levels(self.store, point, frequency_key),
                                      self.store.meta()["chip_name"], point, self.theme)

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
                               self.point_combo.currentText() or "default", self.theme,
                               readout_visible=self.readout_visible_action.isChecked())
        self.scene.clicked.connect(self.on_scene_clicked)
        self.scene.blank_clicked.connect(self.on_blank_clicked)
        self.scene.device_context_requested.connect(self.show_device_context_menu)
        self.scene.set_readout_start(self.readout_start)
        self.view.setScene(self.scene)
        if old_center is None:
            self.view.fit_chip()
            self._initial_fit_pending = not self.isVisible()
        else:
            if self.view._fit_active:
                self.view.fit_chip()
            else:
                self.view.setTransform(old_transform)
                self.scene.grow_to_include(self.view.mapToScene(self.view.viewport().rect()).boundingRect())
                self.view.centerOn(old_center)

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_fit_pending and self.scene:
            self.view.fit_chip()
            self._initial_fit_pending = False

    def on_data_row_double_clicked(self, row: int, _column: int):
        if not self.store:
            return
        title = self.table_kind.currentText()
        kind = {**dict.fromkeys(DEVICE_NAMES.values(), "device"),
                "读取线连接": "readout_link", "比特-耦合器连接": "link",
                "耦合": "coupling"}.get(title)
        if self.table_orientation.currentData() == "vertical":
            ident = self._table_object_ids[_column-1] if 0 < _column <= len(self._table_object_ids) else None
        else:
            item = self.data_table.item(row, 0)
            ident = item.text() if item else None
        if kind and ident:
            self.selected = (kind, ident)
            self.tabs.setCurrentWidget(self.topology_page)
            self.refresh_detail()

    def show_device_context_menu(self, ident: str, anchor: QPoint):
        self.device_popup.close()
        self.selected = ("device", ident)
        self.refresh_detail()
        self.detail_menu.popup(anchor)

    def on_scene_clicked(self, kind: str, ident: str):
        anchor = self.scene.last_click_screen_pos if self.scene else None
        scene_pos = self.scene.last_click_scene_pos if self.scene else None
        if self.store and self.place_readout_action.isChecked() and (scene_pos is not None or kind == "device"):
            x, y = self.scene.logical_at(scene_pos) if scene_pos is not None else (self.store.device(ident)["x"], self.store.device(ident)["y"])
            self.scene.last_click_screen_pos = None; self.scene.last_click_scene_pos = None
            self.on_blank_clicked(x, y)
            return
        if (self.store and kind == "device" and self.place_filter_action.isChecked()
                and self.store.device(ident)["kind"] == "readout"):
            x, y = self.scene.logical_at(scene_pos) if scene_pos is not None else (self.store.device(ident)["x"], self.store.device(ident)["y"])
            self.scene.last_click_screen_pos = None; self.scene.last_click_scene_pos = None
            self.on_blank_clicked(x, y, readout_id=ident)
            return
        if self.scene:
            self.scene.last_click_screen_pos = None
            self.scene.last_click_scene_pos = None
        if self.connect_action.isChecked() and kind == "device" and self.store:
            if self.first_endpoint is None:
                self.first_endpoint = ident
                self.statusBar().showMessage("已选择第一个器件，请点击目标比特、耦合器、读取线或 filter")
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

    @undoable("放置器件")
    def on_blank_clicked(self, x: float, y: float, readout_id=None):
        if not self.store:
            return
        kind = next((kind for action, kind in (
            (self.place_action, "qubit"), (self.place_coupler_action, "coupler"),
            (self.place_readout_action, "readout"), (self.place_filter_action, "filter"),
            (self.place_custom_action, "custom"),
        ) if action.isChecked()), None)
        if kind is None:
            return
        meta = self.store.meta()
        if meta.get("template") == "surface" and kind in ("qubit", "coupler"):
            self._error("无法放置", "旋转表面码模板暂不支持通过方格底板放置；请新建自定义芯片")
            return
        c, r = (round(x), round(y)) if kind == "qubit" else (round(x * 2) / 2, round(y * 2) / 2)
        endpoints = None
        if kind == "readout":
            if self.readout_start is None:
                self.readout_start = (c, r)
                self.scene.set_readout_start(self.readout_start)
                self.mode_hint.setText(f"读取线起点 ({c:g}, {r:g}) · 请选择终点 · Esc 取消")
                return
            if self.readout_start == (c, r):
                self.mode_hint.setText("终点与起点重合，请选择其他格点／半格点 · Esc 取消")
                return
            endpoints = (self.readout_start, (c, r))
            c, r = (endpoints[0][0]+c)/2, (endpoints[0][1]+r)/2
            self._cancel_readout_placement()
        filter_position = None
        if kind == "filter":
            obstacles = self.store.devices()
            candidates = []
            for readout in self.store.devices("readout"):
                position, (sx, sy) = project_to_readout(readout, x, y, obstacles)
                distance = math.hypot(sx-x, sy-y)
                if readout_id == readout["id"] or (readout_id is None and distance <= .25):
                    candidates.append((distance, readout["id"], position, sx, sy))
            if candidates:
                _, readout_id, filter_position, c, r = min(candidates)
        if kind != "readout" and any(d["x"] == c and d["y"] == r and d["id"] != readout_id for d in self.store.devices()):
            self._error("位置已占用", f"({c:g}, {r:g}) 已有器件")
            return
        default_label = self.store.next_device_label(kind)
        if kind == "custom":
            types = sorted({device["subtype"] for device in self.store.devices("custom")})
            dialog = CustomDeviceDialog(default_label, types, self)
            result = dialog.exec()
            label, device_type = dialog.name.text(), dialog.subtype.currentText()
            dialog.deleteLater()
            if result != QDialog.DialogCode.Accepted:
                return
        else:
            name_prompt = ("器件名称（保留默认值时，连接两个比特后自动命名）" if kind == "coupler" else
                           "器件名称（支持 q_{1}、\\omega_{01}）")
            label, ok = QInputDialog.getText(self, f"放置{DEVICE_NAMES[kind]}", name_prompt, text=default_label)
            if not ok or not label.strip():
                return
            choices = SUBTYPES[kind]
            device_type = choices[-1][1]
            if len(choices) > 1:
                subtype, ok = QInputDialog.getItem(self, "器件类型", "类型",
                                                 [caption for caption, _ in reversed(choices)], 0, False)
                if not ok:
                    return
                device_type = next(value for caption, value in choices if caption == subtype)
        try:
            ident = self.store.add_device(kind, device_type, None if label.strip() == default_label else label, c, r,
                                          readout_id=readout_id, readout_position=filter_position,
                                          readout_endpoints=endpoints)
            self.selected = ("device", ident)
            self.refresh_content()
            self._written("器件")
        except Exception as exc:
            self._error("放置失败", exc)

    @undoable("建立器件连接")
    def _connect(self, first: str, second: str):
        if not self.store:
            return
        mode = self.connection_mode.currentData()
        mediator = None
        try:
            kind_a, kind_b = self.store.device(first)["kind"], self.store.device(second)["kind"]
            endpoints = {kind_a, kind_b}
            readout_cavity = endpoints == {"readout", "qubit"}
            if readout_cavity or endpoints == {"readout", "filter"}:
                readout_id, device_id = (first, second) if kind_a == "readout" else (second, first)
                link = self.store.add_readout_link(readout_id, device_id)
                self.selected = ("readout_link", link)
                self.refresh_content(); self._written("读取腔耦合" if readout_cavity else "读取线关联")
                return
            if mode == "readout_cavity":
                raise ValueError("读取腔耦合需要选择一个比特和一条读取线")
            if endpoints == {"qubit", "coupler"}:
                qid = first if kind_a == "qubit" else second
                cid = first if kind_a == "coupler" else second
                ident = self.store.add_link(qid, cid)
                self.selected = ("link", ident)
                self.refresh_content()
                self._written("连接")
                return
            if kind_a != "qubit" or kind_b != "qubit":
                raise ValueError("请选择两比特、比特与耦合器，或读取线与比特／filter")
            if mode == "coupler":
                a, b = self.store.device(first), self.store.device(second)
                mediator = self.store.add_device("coupler", "tunable_coupler", None,
                                                  (a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2)
            ident = self.store.add_coupling(first, second, mode, mediator)
            self.selected = ("coupling", ident)
            self.refresh_content()
            self._written("耦合关系")
        except Exception as exc:
            if mediator:
                self.store.delete_device(mediator)
            self._error("连接失败", exc)

    def refresh_detail(self):
        headers = ["参数", "当前值", "单位"]
        default_widths = {"参数": 128, "当前值": 80, "单位": 52}
        if not self.store or not self.selected:
            self.readout_info.clear()
            self.selected_title.setText("点击图上的器件或连接查看详情")
            self.detail_editor.clear()
            self.visible_definitions = []
            _table_fill(self.parameter_table, headers, [], default_widths=default_widths)
            self.secondary_parameters.hide()
            self._update_ui_state()
            return
        kind, ident = self.selected
        self.readout_info.clear()
        try:
            self.detail_editor.bind(kind, ident)
            self.save_button.setText("保存更改")
            if kind == "device":
                obj = self.store.device(ident)
                scope = obj["kind"]
                self.selected_title.setText(f"<b>{tex_to_html(obj['label'])}</b> · {DEVICE_NAMES[scope]}")
                name = obj["label"]
                self.readout_info.setText("\n".join(filter(None, (self.store.readout_summary(ident),
                                                                self.store.sample_box_summary(ident)))))
            elif kind == "readout_link":
                obj = self.store.readout_link(ident); scope = "readout_link"
                a = self.store.device(obj["readout_id"])["label"]
                b = self.store.device(obj["device_id"])["label"]
                name = f"{a} — {b}"
                caption = "读取腔耦合" if obj["position"] is None else "读取线关联"
                self.selected_title.setText(f"<b>{tex_to_html(name)}</b><br>{caption}")
            elif kind == "coupling":
                obj = self.store.coupling(ident)
                scope = "coupling"
                a = self.store.device(obj["qubit_a"])["label"]
                b = self.store.device(obj["qubit_b"])["label"]
                self.selected_title.setText(f"<b>{tex_to_html(a)} ↔ {tex_to_html(b)}</b><br>"
                                            f"{'经耦合器' if obj['mode'] == 'coupler' else '直接电容耦合'}")
                name = f"{a} ↔ {b}"
            else:
                obj = self.store.link(ident)
                scope = "link"
                q = self.store.device(obj["qubit_id"])["label"]
                c = self.store.device(obj["coupler_id"])["label"]
                self.selected_title.setText(f"<b>{tex_to_html(q)} — {tex_to_html(c)}</b><br>比特—耦合器物理连接")
                name = f"{q} — {c}"
            self.selected_title.setToolTip(ident)
            self.delete_action.setText(f"删除 {name}")
            self.visible_definitions = self.store.definitions(scope)
            records = self.store.current_records(self.point_combo.currentText() or "default")
            values = {key: record_value(record) for key, record in records.items()}
            rows = [[d["label"], "未填写" if values.get((ident, d["key"])) is None else values[(ident, d["key"])],
                     d["unit"]] for d in self.visible_definitions]
            _table_fill(self.parameter_table, headers, rows, default_widths=default_widths)
            self._refresh_secondary_parameters()
            _annotate_bias_z(self.parameter_table, self.visible_definitions, records, ident)
            for row, definition in enumerate(self.visible_definitions):
                self.parameter_table.item(row, 0).setToolTip(f"{definition['label']}\n{definition['key']}")
            self._update_ui_state()
        except KeyError:
            self.selected = None
            self.refresh_detail()

    def _refresh_secondary_parameters(self):
        _apply_secondary_visibility(self.parameter_table, self.visible_definitions, self.secondary_parameters)

    def save_device(self):
        if not self.store or not self.selected:
            return
        try:
            self.drafts.save(*self.selected)
            self.refresh_content()
            self._written("更改")
        except Exception as exc:
            self._error("保存失败", exc)

    def reset_selected(self):
        if self.store and self.selected:
            self.drafts.reset(*self.selected)
            self.refresh_detail()

    def edit_parameter(self, row: int, _column: int, anchor: QPoint | None = None):
        if not self.store or not self.selected or row >= len(self.visible_definitions):
            return
        definition = self.visible_definitions[row]
        dialog = ParameterDialog(definition, self.point_combo.currentText() or "default", self)
        if anchor is not None:
            dialog.move(anchor)
        old_record = self.store.current_records(self.point_combo.currentText() or "default").get(
            (self.selected[1], definition["key"]))
        old_value = record_value(old_record)
        if old_value is not None:
            dialog.value.setText(str(old_value))
        if old_record is not None and uses_bias_z(definition) and old_record["bias_value"] is not None:
            dialog.bias.setText(str(old_record["bias_value"]))
            dialog.bias_unit.setText(old_record["bias_unit"])
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                raw = dialog.value.text().strip()
                value = None if not raw else float(raw) if definition["value_type"] == "number" else raw
                bias = float(dialog.bias.text()) if dialog.bias.text().strip() else None
                point = dialog.point.text().strip()
                self.store.set_parameter(self.selected[1], definition["key"], value,
                                         point=point, bias_value=bias,
                                         bias_unit=dialog.bias_unit.text().strip(), source=dialog.source.text().strip(),
                                         notes=dialog.notes.text().strip(),
                                         attachments=dialog.attachments.inputs())
                if self.point_combo.currentText() != point:
                    self.point_combo.blockSignals(True)
                    self.point_combo.setCurrentText(point)
                    self.point_combo.blockSignals(False)
                self.refresh_parameter_views()
                self.refresh_history()
                self._written("参数")
                dialog.deleteLater()
                return
            except Exception as exc:
                dialog.error.setText(f"登记失败：{exc}。输入已保留，可修改后重试。")
        dialog.deleteLater()

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
            if is_secondary_parameter(dialog.key.text().strip()):
                self.secondary_parameters.setChecked(True)
        except Exception as exc:
            self._error("新增字段失败", exc)

    @undoable("删除所选对象")
    def delete_selected(self):
        if (not self.store or not self.selected or self.tabs.currentWidget() is not self.topology_page
                or self.chip_views.currentIndex() != 0):
            return
        name = self.delete_action.text().removeprefix("删除 ")
        if QMessageBox.question(self, "确认删除", f"删除 {name} 及其关联记录？") != QMessageBox.StandardButton.Yes:
            return
        kind, ident = self.selected
        try:
            if kind == "device":
                self.store.delete_device(ident)
                if self.first_endpoint == ident:
                    self.first_endpoint = None
            elif kind == "coupling":
                coupling = self.store.coupling(ident)
                self.store.delete_coupling(ident)
            elif kind == "readout_link":
                self.store.delete_readout_link(ident)
            else:
                self.store.delete_link(ident)
            self.selected = None
            self.refresh_content()
            self._written("删除操作")
        except Exception as exc:
            self._error("删除失败", exc)

    def refresh_tables(self, *_):
        if not self.store:
            return
        title = self.table_kind.currentText()
        headers, rows = build_tables(self.store, self.point_combo.currentText() or "default")[title]
        if "备注" in headers:
            index = headers.index("备注")
            headers = headers[:index] + headers[index + 1:]
            rows = [row[:index] + row[index + 1:] for row in rows]
        definitions = {d['key']: d for d in self.store.definitions()}
        self._table_parameter_fields = []
        parameter_positions = {}
        bias_keys = {header: key for key, header in BIAS_Z_HEADERS.items()}
        for index, header in enumerate(headers):
            key = header.rsplit(" (", 1)[-1][:-1] if " (" in header and header.endswith(")") else ""
            if key in definitions or key == PI_PRODUCT:
                category = ("secondary" if is_secondary_parameter(key) else
                            definitions[key]["category"] if key in definitions else "measured")
                self._table_parameter_fields.append((index, key, header, category))
                parameter_positions[index] = key
            elif title == "比特" and header in bias_keys:
                parameter_positions[index] = bias_keys[header]
        self._table_object_ids = [str(row[0]) for row in rows]
        if self.table_orientation.currentData() == "vertical":
            field_indices = list(range(1, len(headers))) if headers and headers[0] in ("ID", "线路 ID") else list(range(len(headers)))
            name_index = {"比特": 1, "耦合器": 1, "比特-耦合器连接": 1,
                          "耦合": 1, "接线": 5}.get(title, 1)
            object_headers = [str(row[name_index]) if name_index < len(row) else str(i+1)
                              for i, row in enumerate(rows)]
            if title in ("比特-耦合器连接", "读取线连接"):
                object_headers = [f"{row[1]} — {row[2]}" for row in rows]
            elif title == "耦合":
                object_headers = [f"{row[1]} — {row[2]}" for row in rows]
            elif title == "接线":
                object_headers = [f"{row[1]} · {row[3]} · {row[5]}" for row in rows]
            field_labels = [header.rsplit(" (", 1)[0] if " (" in header and header.endswith(")")
                            else header for header in (headers[index] for index in field_indices)]
            transposed = [[label] + [row[index] for row in rows]
                          for label, index in zip(field_labels, field_indices)]
            _table_fill(self.data_table, ["参数 / 字段"] + object_headers, transposed,
                        default_widths=dict.fromkeys(object_headers, 145) | {"参数 / 字段": 220})
            for row_index, index in enumerate(field_indices):
                self.data_table.item(row_index, 0).setToolTip(headers[index])
                if headers[index] in ("类型", "方式", "角色", "方向", "元件类别"):
                    mapping = ROLE_NAMES if headers[index] == "角色" else TYPE_NAMES
                    for column in range(1, self.data_table.columnCount()):
                        item = self.data_table.item(row_index, column)
                        item.setText(str(mapping.get(item.data(Qt.ItemDataRole.UserRole), item.text())))
            self.tex_delegate.label_columns = {0}
            self.tex_delegate.label_rows = {
                row_index for row_index, index in enumerate(field_indices)
                if headers[index] in ("名称", "比特", "耦合器", "比特 A", "比特 B", "器件", "对象")
            }
            self._table_parameter_positions = {row: parameter_positions[index]
                                               for row, index in enumerate(field_indices) if index in parameter_positions}
        else:
            _table_fill(self.data_table, headers, rows)
            self.tex_delegate.label_columns = {
                index for index, header in enumerate(headers)
                if header in ("名称", "比特", "耦合器", "比特 A", "比特 B", "器件", "对象")
            }
            self.tex_delegate.label_rows = set()
            self._table_parameter_positions = parameter_positions
        for index, header in enumerate(["参数 / 字段"] + object_headers if self.table_orientation.currentData() == "vertical" else headers):
            item = self.data_table.horizontalHeaderItem(index)
            if item:
                item.setData(Qt.ItemDataRole.UserRole, header)
                item.setText("")
        self.data_table.setColumnHidden(0, self.table_orientation.currentData() == "horizontal" and
                                        bool(headers) and headers[0] in ("ID", "线路 ID"))
        self.parameter_visibility_button.setEnabled(bool(self._table_parameter_fields))
        self._build_parameter_menu()
        self.filter_table()

    def _build_parameter_menu(self):
        self.parameter_visibility_menu.clear()
        self._parameter_visibility_actions = {}
        show_all = self.parameter_visibility_menu.addAction("全部显示")
        show_all.triggered.connect(lambda: self._show_all_table_parameters(True))
        hide_all = self.parameter_visibility_menu.addAction("全部隐藏")
        hide_all.triggered.connect(lambda: self._show_all_table_parameters(False))
        self.parameter_visibility_menu.addSeparator()
        for category, title in [("measured", "测量参数"), ("secondary", "次要参数"),
                                ("design", "设计参数"), ("custom", "自定义参数")]:
            fields = [f for f in self._table_parameter_fields if f[3] == category]
            if not fields:
                continue
            group = self.parameter_visibility_menu.addMenu(title)
            for _, key, header, _ in fields:
                document = QTextDocument(); document.setHtml(tex_to_html(_visible_header(header)))
                action = group.addAction(document.toPlainText())
                action.setToolTip(header); action.setData(key); action.setCheckable(True)
                action.setChecked(self._is_table_parameter_visible(key))
                action.toggled.connect(lambda checked, k=key: self._set_table_parameter_visible(k, checked))
                self._parameter_visibility_actions[key] = action

    def _set_table_parameter_visible(self, key, visible):
        self._table_parameter_visibility[key] = visible
        self.filter_table()

    def _is_table_parameter_visible(self, key):
        return self._table_parameter_visibility.get(key, not is_secondary_parameter(key))

    def _show_all_table_parameters(self, visible):
        for key, action in self._parameter_visibility_actions.items():
            self._table_parameter_visibility[key] = visible
            action.blockSignals(True); action.setChecked(visible); action.blockSignals(False)
        self.filter_table()

    def filter_table(self, *_):
        needle = self.filter_edit.text().strip().casefold()
        if self.table_orientation.currentData() == "vertical":
            field_matches = [needle in (self.data_table.item(r, 0).text().casefold()
                                         if self.data_table.item(r, 0) else "")
                             for r in range(self.data_table.rowCount())]
            by_field = bool(needle) and any(field_matches)
            for r in range(self.data_table.rowCount()):
                self.data_table.setRowHidden(r, (by_field and not field_matches[r]) or
                    not self._is_table_parameter_visible(self._table_parameter_positions.get(r)))
            for c in range(1, self.data_table.columnCount()):
                heading = self.data_table.horizontalHeaderItem(c)
                haystack = " ".join([str(heading.data(Qt.ItemDataRole.UserRole) or "") if heading else ""] +
                                    [self.data_table.item(r, c).text() for r in range(self.data_table.rowCount())
                                     if self.data_table.item(r, c)])
                self.data_table.setColumnHidden(c, not by_field and needle not in haystack.casefold())
            visible = sum(not self.data_table.isColumnHidden(c) for c in range(1, self.data_table.columnCount()))
            self.table_summary.setText(f"{visible} / {max(0, self.data_table.columnCount()-1)} 个对象 · 工作点 {self.point_combo.currentText()} · 双击对象数据查看详情")
            return
        for column in range(1, self.data_table.columnCount()):
            self.data_table.setColumnHidden(column, not self._is_table_parameter_visible(self._table_parameter_positions.get(column)))
        for r in range(self.data_table.rowCount()):
            haystack = " ".join(self.data_table.item(r, c).text() for c in range(self.data_table.columnCount())
                                if self.data_table.item(r, c))
            self.data_table.setRowHidden(r, needle not in haystack.casefold())
        visible = sum(not self.data_table.isRowHidden(r) for r in range(self.data_table.rowCount()))
        self.table_summary.setText(f"{visible} / {self.data_table.rowCount()} 个对象 · 工作点 {self.point_combo.currentText()} · 双击一行查看详情")

    def refresh_history(self):
        if self.store:
            selected_id = self._chosen_history_id()
            sorting = self.history_table.isSortingEnabled()
            sort_column = self.history_table.horizontalHeader().sortIndicatorSection()
            sort_order = self.history_table.horizontalHeader().sortIndicatorOrder()
            if self.history_table.columnCount() == 0:
                sort_column, sort_order = 9, Qt.SortOrder.DescendingOrder
            self.history_table.blockSignals(True)
            self.history_table.setSortingEnabled(False)
            headers, rows = build_tables(self.store, include_history=True)["参数历史"]
            records = self.store.records()
            counts = self.store.attachment_counts()
            headers = headers + ["附件数量"]
            rows = [row + [counts.get(record["id"], 0)] for row, record in zip(rows, records)]
            _table_fill(self.history_table, headers, rows)
            self.history_table.setColumnHidden(2, True)
            for row, values in enumerate(rows):
                self.history_table.item(row, 3).setToolTip(str(values[2]))
                self.history_table.item(row, 0).setData(Qt.ItemDataRole.UserRole+2, records[row]["id"])
                self.history_table.item(row, len(headers)-1).setToolTip("双击记录或点击管理附件")
            self.history_table.setSortingEnabled(sorting)
            if sorting and 0 <= sort_column < len(headers):
                self.history_table.sortItems(sort_column, sort_order)
            if selected_id:
                for row in range(self.history_table.rowCount()):
                    if self.history_table.item(row, 0).data(Qt.ItemDataRole.UserRole+2) == selected_id:
                        self.history_table.setCurrentCell(row, 1)
                        break
            self.history_table.blockSignals(False)
            self._update_ui_state()

    def _chosen_history_id(self):
        if not hasattr(self, "history_table"):
            return None
        row = self.history_table.currentRow()
        item = self.history_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole+2) if item else None

    def manage_history_attachments(self, *_):
        record_id = self._chosen_history_id()
        if self.store and record_id:
            try:
                dialog = AttachmentDialog(self.store, record_id, self)
                dialog.saved.connect(self.refresh_history)
                dialog.saved.connect(lambda: self._written("附件"))
                dialog.exec()
                dialog.deleteLater()
            except Exception as exc:
                self._error("无法打开附件", exc)

    def refresh_wiring(self):
        if self.store:
            headers, rows = build_tables(self.store)["接线"]
            _table_fill(self.wiring_table, headers, rows)
            for column in range(12, len(headers)):
                self.wiring_table.setColumnHidden(column, True)
            lines = self.store.lines()
            for combo, title, options in [
                (self.wiring_board, "全部盘号", sorted({self.store.line_details(line['id']).get('board', '') for line in lines} - {''})),
                (self.wiring_usage, "全部用途", sorted({line['line_type'] for line in lines})),
            ]:
                selected = combo.currentText()
                combo.blockSignals(True); combo.clear(); combo.addItem(title); combo.addItems(options)
                combo.setCurrentText(selected if combo.findText(selected) >= 0 else title)
                combo.blockSignals(False)
        self.filter_wiring()
        self.refresh_wiring_detail()

    def filter_wiring(self, *_):
        needle = self.wiring_search.text().casefold().strip()
        board = self.wiring_board.currentText() if self.wiring_board.currentIndex() > 0 else ""
        usage = self.wiring_usage.currentText() if self.wiring_usage.currentIndex() > 0 else ""
        for row in range(self.wiring_table.rowCount()):
            values = [self.wiring_table.item(row, col).text() for col in range(self.wiring_table.columnCount())]
            hidden = bool((board and values[7] != board) or (usage and values[1] != usage)
                          or (needle and needle not in ' '.join(values).casefold()))
            self.wiring_table.setRowHidden(row, hidden)
        current = self.wiring_table.currentRow()
        if current >= 0 and self.wiring_table.isRowHidden(current):
            self.wiring_table.clearSelection()
        self._update_ui_state()

    def refresh_wiring_detail(self):
        ident = self._chosen_line_id()
        if not self.store or not ident:
            self.wiring_detail.setText("选择线路查看温级配置、放大链与检线记录；双击线路可编辑。")
            return
        line = self.store.line(ident); details = self.store.line_details(ident)
        parts = [f"{line['cryostat']} · {details.get('board', '')}盘 · {line['port']} · {line['line_type']}"]
        mapping = "；".join(f"{label}：{details[key]}" for key, label in METADATA if details.get(key))
        if mapping:
            parts.append(mapping)
        for key, title in [("components", "温级元件"), ("amplifiers", "放大链"),
                           ("biases", "放大器工作参数"), ("checks", "检线记录")]:
            text = rows_text(details, key)
            if text:
                parts.append(f"{title}\n{text}")
        self.wiring_detail.setText('\n\n'.join(parts))

    @undoable("添加制冷机线路")
    def add_line(self):
        if not self.store:
            return
        dialog = LineDialog(self)
        selected_device = (self.selected[1] if self.selected and self.selected[0] == "device" else None)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.add_line(dialog.line_type.currentText(), dialog.cryostat.text(), dialog.port.text(),
                                    dialog.channel.text(), dialog.notes.text(),
                                    [selected_device] if selected_device else [], details=dialog.details())
                self.refresh_content()
                self._written("线路")
                return
            except Exception as exc:
                dialog.error.setText(f"线路登记失败：{exc}")

    def _chosen_line_id(self):
        if not self.wiring_table.selectionModel().hasSelection():
            return None
        row = self.wiring_table.currentRow()
        return self.wiring_table.item(row, 0).text() if row >= 0 and self.wiring_table.item(row, 0) else None

    @undoable("修改制冷机线路")
    def edit_line(self):
        if not self.store or not (line_id := self._chosen_line_id()):
            return
        dialog = LineDialog(self, self.store.line(line_id), self.store.line_details(line_id))
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.update_line(line_id, dialog.line_type.currentText(), dialog.cryostat.text(),
                                       dialog.port.text(), dialog.channel.text(), dialog.notes.text(), details=dialog.details())
                self.refresh_content()
                self._written("线路")
                return
            except Exception as exc:
                dialog.error.setText(f"编辑线路失败：{exc}")

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
                self._written("线路关联")
            except Exception as exc:
                self._error("关联失败", exc)

    def delete_line(self):
        if not self.store or not (line_id := self._chosen_line_id()):
            return
        if QMessageBox.question(self, "确认删除", "删除这条线路及器件关联？") == QMessageBox.StandardButton.Yes:
            self.store.delete_line(line_id)
            self.refresh_content()
            self._written("线路删除")

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
            self._written("解除关联")

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
                           theme=self.theme, readout_visible=self.readout_visible_action.isChecked())
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
                               theme=self.theme, readout_visible=self.readout_visible_action.isChecked())
            self.statusBar().showMessage(f"已导出 {len(fields)} 张图片到 {directory}")
        except Exception as exc:
            self._error("批量导出失败", exc)

    def open_manual(self):
        path = Path(__file__).resolve().parent.parent / "output" / "pdf" / "user-guide.pdf"
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self._error("说明文件不存在", path)

    def show_version_info(self):
        from .migrations import MIGRATIONS
        from .fridge_store import FRIDGE_SCHEMA_VERSION
        supported = "、".join(f"v{version}" for version in sorted((*MIGRATIONS, SCHEMA_VERSION)))
        QMessageBox.information(self, "版本信息",
                                f"Qubit Sequencer\n软件版本：{__version__}\n\n"
                                f"当前 .qchip 文件格式：v{SCHEMA_VERSION}\n"
                                f"支持打开的 .qchip 文件版本：{supported}\n\n"
                                f"当前 .qfridge 文件格式：v{FRIDGE_SCHEMA_VERSION}（支持 v1–v{FRIDGE_SCHEMA_VERSION}）\n\n"
                                "旧版本项目打开时会先自动备份，再升级到当前格式。\n"
                                f"高于 v{SCHEMA_VERSION} 的项目暂不支持。")

    def closeEvent(self, event):
        if not self._leave_file():
            event.ignore()
            return
        self.settings.setValue("layout/lastPageKey", self.navigation.currentItem().data(Qt.ItemDataRole.UserRole))
        pages, widths = self.tabs.layout_state()
        self.settings.setValue("layout/workspacePages", [self.navigation.item(i).data(Qt.ItemDataRole.UserRole) for i in pages])
        self.settings.setValue("layout/workspaceWidths", widths)
        fridge = self.fridge_panel if self.fridge_panel and not self.fridge_panel.preview else None
        self.settings.setValue("workspace/chipPath", str(self.store.path.resolve()) if self.store else "")
        self.settings.setValue("workspace/fridgePath", str(fridge.store.path) if fridge else "")
        self.settings.setValue("workspace/workingPoint", self.point_combo.currentText())
        self.settings.setValue("workspace/chipView", self.chip_views.currentIndex())
        self.settings.setValue("workspace/fridgeBoard", (fridge.board.currentData() or "") if fridge else "")
        self.settings.setValue("workspace/fridgeView", fridge.views.currentIndex() if fridge else 0)
        if self.fridge_panel:
            self.fridge_panel.close()
            self.fridge_panel = None
        self.settings.setValue("layout/geometry", self.saveGeometry())
        self._remember_split()
        self.settings.sync()
        if hasattr(self, "device_popup"):
            self.device_popup.close()
            self.device_popup.editor.clear()
        self.detail_editor.clear()
        self.selected = None
        self.drafts.clear()
        self.undo_history.detach()
        self.undo_history.clear()
        self.undo_controller.dispose()
        if self.store:
            self.store.close()
            self.store = None
        super().closeEvent(event)


def run_gui(path: str | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(path)
    window.show()
    return app.exec()

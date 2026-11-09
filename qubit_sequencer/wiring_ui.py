"""A compact line editor mirroring the cryostat wiring registration tables."""

from contextlib import nullcontext
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .storage import beijing_now
from .undo_ui import install_form_undo
from .wiring_store import (
    COMPONENT_TYPES, LINE_TYPES, METADATA, STAGES, TABLE_FIELDS,
    normalise_details, text_value,
)


class ChoiceDelegate(QStyledItemDelegate):
    def __init__(self, choices, parent):
        super().__init__(parent)
        self.choices = choices

    def createEditor(self, parent, option, index):
        control = QComboBox(parent)
        control.setEditable(True)
        control.addItems(self.choices() if callable(self.choices) else self.choices)
        return control

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


class WiringRows(QTableWidget):
    """Committed cells and whole row actions share the dialog's undo history."""

    def __init__(self, fields, rows, parent=None):
        super().__init__(0, len(fields), parent)
        self.fields = fields
        self.history = None
        self._rows = []
        self.setHorizontalHeaderLabels([label for _, label in fields])
        self.horizontalHeader().setDefaultSectionSize(136)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.restore([[text_value(row.get(key)) for key, _ in fields] for row in rows])
        self.itemChanged.connect(self.edited)

    def attach_undo(self, history):
        self.history = history
        history.add_state(lambda: deepcopy(self._rows), self.restore)

    def choices(self, column, values):
        self.setItemDelegateForColumn(column, ChoiceDelegate(values, self))

    def restore(self, rows):
        self.blockSignals(True)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem(value); item.setToolTip(value)
                self.setItem(r, c, item)
        self._rows = deepcopy(rows)
        self.blockSignals(False)

    def edited(self, item):
        context = self.history.action("编辑接线表格", (id(self), item.row(), item.column())) if self.history else nullcontext()
        with context:
            self._rows[item.row()][item.column()] = item.text()
        self.blockSignals(True); item.setToolTip(item.text()); self.blockSignals(False)

    def replace_rows(self, label, rows, selected):
        with self.history.action(label) if self.history else nullcontext():
            self.restore(rows)
            if 0 <= selected < self.rowCount():
                self.setCurrentCell(selected, 0)

    def add(self, defaults=None):
        defaults = defaults or {}
        self.replace_rows("新增接线表格行", self._rows + [[text_value(defaults.get(k)) for k, _ in self.fields]], self.rowCount())

    def remove(self):
        r = self.currentRow()
        if r >= 0:
            self.replace_rows("删除接线表格行", self._rows[:r] + self._rows[r+1:], min(r, self.rowCount()-2))

    def move(self, offset):
        r = self.currentRow()
        if 0 <= r + offset < self.rowCount() and r >= 0:
            rows = deepcopy(self._rows)
            rows[r], rows[r+offset] = rows[r+offset], rows[r]
            self.replace_rows("调整接线元件顺序", rows, r + offset)

    def records(self):
        return [dict(zip((k for k, _ in self.fields), row)) for row in self._rows if any(row)]


def table_section(layout, title, table, defaults=None, order=False):
    actions = QHBoxLayout()
    actions.addWidget(QLabel(title), 1)
    for label, callback in [("新增行", lambda: table.add(defaults() if callable(defaults) else defaults)), ("删除行", table.remove)] + (
            [("上移", lambda: table.move(-1)), ("下移", lambda: table.move(1))] if order else []):
        button = QPushButton(label)
        button.clicked.connect(callback)
        actions.addWidget(button)
    layout.addLayout(actions)
    layout.addWidget(table, 1)


class LineDialog(QDialog):
    def __init__(self, parent=None, line=None, details=None):
        super().__init__(parent)
        details = details or {}
        self.setWindowTitle("登记制冷机线路")
        self.resize(1040, 700)
        layout = QVBoxLayout(self)
        hint = QLabel("按线路登记盘位、样品映射、温级配置、放大链和检线结果。未登记项留空，0 表示已登记的零值。")
        hint.setWordWrap(True); layout.addWidget(hint)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        basic = QWidget(); form = QFormLayout(basic)
        self.line_type = QComboBox(); self.line_type.setEditable(True); self.line_type.addItems(LINE_TYPES)
        self.cryostat = QLineEdit(); self.port = QLineEdit(); self.channel = QLineEdit(); self.notes = QLineEdit()
        form.addRow("线路用途", self.line_type); form.addRow("制冷机", self.cryostat)
        self.metadata = {}
        for key, label in METADATA:
            control = QComboBox() if key == "board" else QLineEdit()
            if isinstance(control, QComboBox):
                control.setEditable(True); control.addItems(["", "A", "B", "C", "D", "E", "F"])
                control.setCurrentText(details.get(key, ""))
            else:
                control.setText(details.get(key, ""))
            self.metadata[key] = control
            form.addRow(label, control)
            if key == "board":
                form.addRow("端口", self.port); form.addRow("仪器通道", self.channel)
        form.addRow("备注", self.notes)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(basic)
        self.tabs.addTab(scroll, "盘位与样品")
        if line is not None:
            self.line_type.setCurrentText(line["line_type"])
            for key in ("cryostat", "port", "channel", "notes"):
                getattr(self, key).setText(line[key])

        self.components = WiringRows(TABLE_FIELDS["components"], details.get("components", []))
        self.components.choices(0, STAGES); self.components.choices(1, COMPONENT_TYPES)
        self.components.choices(4, ["", "dB", "MHz", "GHz"])
        page = QWidget(); page_layout = QVBoxLayout(page)
        label = QLabel("依实际信号顺序填写，可在同一温级登记多行。衰减器用 dB，滤波器截止频率用 MHz / GHz；IR 等型号填入名称／编号。")
        label.setWordWrap(True); page_layout.addWidget(label)
        table_section(page_layout, "衰减、滤波、隔离、合路及开关", self.components, {"stage": "MC", "kind": "衰减器"}, True)
        self.tabs.addTab(page, "温级元件")

        self.amplifiers = WiringRows(TABLE_FIELDS["amplifiers"], details.get("amplifiers", []))
        self.amplifiers.choices(0, STAGES)
        self.biases = WiringRows(TABLE_FIELDS["biases"], details.get("biases", []))
        self.biases.choices(0, lambda: [""] + [r.get("serial", "") for r in self.amplifiers.records() if r.get("serial")])
        page = QWidget(); page_layout = QVBoxLayout(page)
        label = QLabel("低温、室温放大器分别登记。工作参数按放大器编号和温度记录；Vd / Vg 单位为 V，Id 单位为 mA。电源引脚可填写 GND、Vd、Vg 对应的接头与针脚。")
        label.setWordWrap(True); page_layout.addWidget(label)
        table_section(page_layout, "放大器", self.amplifiers)
        table_section(page_layout, "不同温度下的工作参数", self.biases)
        self.tabs.addTab(page, "放大链")

        self.checks = WiringRows(TABLE_FIELDS["checks"], details.get("checks", []))
        self.checks.choices(1, ["", "装样前", "带样品", "装样后"])
        self.checks.choices(5, ["", "合格", "待复核", "异常", "未检查"])
        page = QWidget(); page_layout = QVBoxLayout(page)
        label = QLabel("配置衰减与实测传输分别登记。记录装样状态、测试功率、频段、结果和结论；测试方法、临时改线及异常说明填写在条件／备注。")
        label.setWordWrap(True); page_layout.addWidget(label)
        table_section(page_layout, "检线记录（时间可自行修改）", self.checks, lambda: {"time": beijing_now()})
        self.tabs.addTab(page, "检线记录")
        self.error = QLabel(); self.error.setProperty("role", "danger"); self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存线路")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        install_form_undo(self)

    def details(self):
        result = {key: control.currentText() if isinstance(control, QComboBox) else control.text()
                  for key, control in self.metadata.items()}
        result.update({key: getattr(self, key).records() for key in TABLE_FIELDS})
        return normalise_details(result)

    def accept(self):
        try:
            if not self.line_type.currentText().strip():
                raise ValueError("请填写线路用途")
            self.details()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()

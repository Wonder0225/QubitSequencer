"""Editors and previews for physical cryogenic circuits."""

import json
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout)

from .fridge_presets import STAGES
from .theme import UI_COLORS
from .ui_icons import icon


def component_text(component):
    value = component.get("value")
    parts = [component.get("kind", ""), "" if value is None else f"{value:g}",
             component.get("unit", ""), component.get("mark", ""), component.get("terminal", ""), component.get("notes", "")]
    return " ".join(str(p) for p in parts if p not in (None, ""))


class CircuitDialog(QDialog):
    def __init__(self, store, port, parent=None):
        super().__init__(parent); self.store = store; self.port = port
        self.setWindowTitle(f"{port['board']} 盘 {port['number']} · 低温线路")
        self.resize(850, 620)
        layout = QVBoxLayout(self)
        diagram = QHBoxLayout(); layout.addLayout(diagram)
        self.stage_labels = {}
        for stage in STAGES:
            frame = QFrame(); frame.setFrameShape(QFrame.Shape.StyledPanel)
            column = QVBoxLayout(frame); title = QLabel(stage); title.setProperty("role", "heading")
            column.addWidget(title); label = QLabel("未登记"); label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            column.addWidget(label, 1); diagram.addWidget(frame, 1); self.stage_labels[stage] = label
        self.preview = QLabel(); self.preview.setWordWrap(True); layout.addWidget(self.preview)
        layout.addWidget(QLabel("按温级登记；行顺序仅表示登记顺序，不推断信号方向。"))
        self.rows = QTableWidget(0, 7); self.rows.setHorizontalHeaderLabels(["温级", "类别", "数值", "单位", "标记 / 编号", "端点", "备注"])
        self.rows.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.rows, 1)
        actions = QHBoxLayout(); layout.addLayout(actions)
        color = UI_COLORS[getattr(parent, "theme", "light")]["text"]
        for label, callback, symbol in (("新增行", self.add_row, "new"), ("删除行", self.remove_row, "delete"),
                                ("上移", lambda: self.move_row(-1), "up"), ("下移", lambda: self.move_row(1), "down"),
                                ("从旧记录导入", self.import_legacy, "open"), ("查看导入旧资料", self.view_imported, "history")):
            button = QPushButton(label); button.setIcon(icon(symbol, color)); button.clicked.connect(callback); actions.addWidget(button)
        actions.addStretch()
        self.enable_switch = QCheckBox("启用微波开关")
        self.branches = QLineEdit(); self.branches.setPlaceholderText("分支编号，以逗号分隔，例如 1,2,3,4,5,6")
        self.transfer = QComboBox(); self.transfer.addItem("请选择已有分配的归属分支", "")
        form = QFormLayout(); form.addRow(self.enable_switch); form.addRow("开关分支", self.branches)
        if port["direct_sample_id"]: form.addRow("已有样品转入", self.transfer)
        layout.addLayout(form)
        self.error = QLabel(); self.error.setWordWrap(True); self.error.setProperty("role", "danger"); layout.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText("保存线路")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject); layout.addWidget(box)
        circuit = store.circuit(port["id"])
        self.legacy_details = circuit.get("legacy_details")
        self.set_rows(circuit.get("components", []))
        self.branches.setText(",".join(circuit.get("switch", [])))
        self.enable_switch.setChecked(bool(circuit.get("switch")))
        self.enable_switch.toggled.connect(self.switch_changed)
        self.branches.textChanged.connect(self.refresh_preview)
        self.rows.itemChanged.connect(self.refresh_preview)
        self.switch_changed(self.enable_switch.isChecked())

    def set_rows(self, rows):
        self.rows.setRowCount(0)
        for row in rows:
            index = self.rows.rowCount(); self.rows.insertRow(index)
            for col, key in enumerate(("stage", "kind", "value", "unit", "mark", "terminal", "notes")):
                value = row.get(key)
                self.rows.setItem(index, col, QTableWidgetItem("" if value is None else str(value)))

    def add_row(self):
        self.rows.insertRow(self.rows.rowCount())
        for col in range(7): self.rows.setItem(self.rows.rowCount()-1, col, QTableWidgetItem(""))
        self.refresh_preview()

    def remove_row(self):
        if self.rows.currentRow() >= 0: self.rows.removeRow(self.rows.currentRow())
        self.refresh_preview()

    def move_row(self, offset):
        index = self.rows.currentRow(); other = index + offset
        if index < 0 or other < 0 or other >= self.rows.rowCount(): return
        for col in range(7):
            a, b = self.rows.takeItem(index, col), self.rows.takeItem(other, col)
            self.rows.setItem(index, col, b); self.rows.setItem(other, col, a)
        self.rows.setCurrentCell(other, 0); self.refresh_preview()

    def branch_names(self):
        return [v.strip() for v in self.branches.text().replace("，", ",").split(",") if v.strip()]

    def switch_changed(self, checked):
        if checked and not self.branches.text().strip(): self.branches.setText("1,2,3,4,5,6")
        self.branches.setEnabled(checked); self.transfer.setEnabled(checked)
        self.refresh_preview()

    def components(self):
        rows = []
        for r in range(self.rows.rowCount()):
            values = [self.rows.item(r, c).text().strip() if self.rows.item(r, c) else "" for c in range(7)]
            if not any(values): continue
            if not values[0] or not values[1]: raise ValueError("请填写温级和类别")
            try: number = float(values[2]) if values[2] else None
            except ValueError: raise ValueError("数值必须为数字，未登记时留空") from None
            if number is not None and not math.isfinite(number): raise ValueError("数值必须为有限数字")
            rows.append(dict(zip(("stage", "kind", "value", "unit", "mark", "terminal", "notes"), (values[0], values[1], number, *values[3:]))))
        return rows

    def refresh_preview(self, *_):
        current = self.transfer.currentData(); self.transfer.blockSignals(True); self.transfer.clear()
        self.transfer.addItem("请选择已有分配的归属分支", "")
        for name in self.branch_names(): self.transfer.addItem(name, name)
        self.transfer.setCurrentIndex(max(0, self.transfer.findData(current))); self.transfer.blockSignals(False)
        try: rows = self.components()
        except ValueError:
            self.preview.setText("填写完整后显示线路预览"); return
        for stage, label in self.stage_labels.items():
            label.setText("\n".join(component_text(r) for r in rows if r["stage"] == stage) or "未登记")
        stages = list(STAGES) + [r["stage"] for r in rows if r["stage"] not in STAGES]
        lines = [f"【{stage}】 " + "  ·  ".join(component_text(r) for r in rows if r["stage"] == stage)
                 for stage in dict.fromkeys(stages) if stage not in STAGES and any(r["stage"] == stage for r in rows)]
        if self.enable_switch.isChecked(): lines.append("【微波开关】 " + " / ".join(self.branch_names()))
        self.preview.setText("\n".join(lines) if lines else ("按温级展示已登记元件" if rows else "尚未登记低温线路"))

    def import_legacy(self):
        records = []
        if self.port["legacy_json"]: records.append(("原端口记录", self.port["legacy_json"]))
        records += [(f"分支 {r['branch']}", r["legacy_json"]) for r in self.store.branch_assignments(self.port["id"]) if r["legacy_json"] != "{}"]
        if not records:
            QMessageBox.information(self, "旧记录", "此端口没有可导入的旧记录。")
            return
        label, ok = QInputDialog.getItem(self, "旧记录", "选择要导入的记录", [r[0] for r in records], 0, False)
        if not ok: return
        if self.rows.rowCount() and QMessageBox.question(self, "导入旧记录", "用旧记录的温级元件替换当前表格？原始旧记录仍会保留。") != QMessageBox.StandardButton.Yes: return
        self.legacy_details = json.loads(dict(records)[label]).get("details", {})
        raw = self.legacy_details.get("components", [])
        self.set_rows([{"stage": r.get("stage", ""), "kind": r.get("kind", ""), "value": r.get("value"),
                        "unit": r.get("unit", ""), "mark": r.get("name", ""),
                        "terminal": r.get("terminal", ""), "notes": r.get("notes", "")} for r in raw])
        self.refresh_preview()

    def view_imported(self):
        if not self.legacy_details:
            QMessageBox.information(self, "导入旧资料", "尚未导入旧资料。")
            return
        dialog = QDialog(self); dialog.setWindowTitle("导入的旧资料（只读）"); dialog.resize(700, 500)
        layout = QVBoxLayout(dialog); view = QPlainTextEdit(); view.setReadOnly(True)
        view.setPlainText(json.dumps(self.legacy_details, ensure_ascii=False, indent=2)); layout.addWidget(view)
        dialog.exec()

    def result_data(self):
        result = {"components": self.components(), "switch": self.branch_names() if self.enable_switch.isChecked() else []}
        if self.legacy_details is not None: result["legacy_details"] = self.legacy_details
        return result

    def accept(self):
        try:
            data = self.result_data()
            self.store.save_circuit(self.port["id"], data, self.transfer.currentData() or None)
        except Exception as exc:
            self.error.setText(str(exc)); return
        super().accept()


class PresetDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent); self.store = store; self.setWindowTitle("预览低温线路预设"); self.resize(850, 560)
        layout = QVBoxLayout(self); layout.addWidget(QLabel("默认只选择空白线路；勾选冲突项将覆盖其物理线路，样品分配保持不变。"))
        self.preview = store.preset_preview()
        self.table = QTableWidget(len(self.preview), 5); self.table.setHorizontalHeaderLabels(["应用", "盘", "端口", "现有线路", "预设内容"])
        self.table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.table)
        for i, item in enumerate(self.preview):
            check = QTableWidgetItem(); check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked if item["conflict"] else Qt.CheckState.Checked); self.table.setItem(i, 0, check)
            description = "；".join(f"{r['stage']} {component_text(r)}" for r in item["preset"]["components"])
            if item["preset"].get("switch"): description += "；微波开关 1–6"
            existing = "；".join(f"{r['stage']} {component_text(r)}" for r in item["existing"].get("components", []))
            if item["existing"].get("switch"): existing += "；开关 " + "/".join(item["existing"]["switch"])
            if item["existing"].get("legacy_details"): existing += "；含导入旧资料"
            for c, value in enumerate((item["port"]["board"], item["port"]["number"], existing or "空白", description), 1):
                cell = QTableWidgetItem(value); cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable); self.table.setItem(i, c, cell)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("应用所选")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject); layout.addWidget(box)

    def accept(self):
        selected = {item["port"]["id"]: item["preset"] for i, item in enumerate(self.preview)
                    if self.table.item(i, 0).checkState() == Qt.CheckState.Checked}
        transfers = {}
        for item in self.preview:
            p = item["port"]
            if p["id"] in selected and item["preset"].get("switch") and p["direct_sample_id"]:
                branch, ok = QInputDialog.getItem(self, "已有样品", f"{p['board']} {p['number']} 已分配样品，请选择归属分支", item["preset"]["switch"], 0, False)
                if not ok: return
                transfers[p["id"]] = branch
        try: self.store.apply_circuits(selected, transfers)
        except Exception as exc:
            QMessageBox.warning(self, "预设应用失败", str(exc)); return
        super().accept()

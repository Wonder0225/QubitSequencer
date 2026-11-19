"""Cryogenic circuit and component library dialogs."""

from copy import deepcopy
import json
import math
import sqlite3

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .fridge_circuit_diagram import CircuitLane, CircuitView
from .fridge_components import (BIAS_TEE_INPUTS, BIAS_TEE_KIND, BIAS_TEE_TERMINALS, LIBRARY_DEFAULTS, STAGES,
    SWITCH_DIRECTIONS, SWITCH_KIND, bias_tee_input_labels, classify_infrared, component_text, route_parents,
    switch_on, terminal_branches, transfer_destinations)
from .fridge_store import display_line_type, stored_line_type
from .sample_box_store import port_number_key
from .theme import UI_COLORS
from .ui_icons import icon


def dialog_controls(dialog, layout, save=None):
    standards = QDialogButtonBox.StandardButton
    box = QDialogButtonBox(standards.Save | standards.Cancel if save else standards.Close)
    if save:
        box.button(standards.Save).setText(save)
        box.button(standards.Cancel).setText("取消")
    else:
        box.button(standards.Close).setText("关闭")
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    return box


def tool_button(layout, owner, title, action, symbol):
    button = QPushButton(title)
    color = UI_COLORS[getattr(owner, "theme", "light")]["text"]
    button.setIcon(icon(symbol, color))
    button.clicked.connect(action)
    layout.addWidget(button)
    return button


class ComponentDialog(QDialog):
    def __init__(self, parent, component, *, library_name=None, branches=None, stage="", merge=None, input_ports=None):
        super().__init__(parent)
        self.original = component
        self.result_component = None
        self.setWindowTitle("元件库条目" if library_name is not None else "线路元件")
        self.resize(460, 420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.name = None
        if library_name is not None:
            self.name = QLineEdit(library_name)
            form.addRow("库条目名称", self.name)
        self.kind = QComboBox()
        self.kind.setEditable(True)
        self.kind.addItems([entry["kind"] for entry in LIBRARY_DEFAULTS
                            if library_name is not None or entry["kind"] != SWITCH_KIND])
        self.kind.setCurrentText(component.get("kind", ""))
        self.kind.setEnabled(branches is None and merge is None)
        form.addRow("类型", self.kind)
        self.stage = None
        if library_name is None:
            self.stage = QComboBox()
            self.stage.setEditable(True)
            self.stage.addItems(["", *STAGES])
            self.stage.setCurrentText(component.get("stage", stage) or "")
            self.stage.lineEdit().setPlaceholderText("留空为温级未登记")
            form.addRow("温级", self.stage)
        self.branches = None
        self.original_branches = set(branches or ())
        self.direction = None
        self.merge_list = None
        self.original_output = None
        if branches is not None:
            self.direction = QComboBox()
            self.direction.addItem("未标注", "")
            for value, label in SWITCH_DIRECTIONS.items():
                self.direction.addItem(label, value)
            self.direction.setCurrentIndex(max(0, self.direction.findData(component.get("direction", ""))))
            self.direction.setToolTip("多路到一路表示各分支可分别经开关接入共用端。")
            form.addRow("接线方向", self.direction)
            self.branches = QLineEdit(", ".join(branches))
            self.branches.setPlaceholderText("用逗号分隔，例如 1, 2, 3")
            form.addRow("分支编号", self.branches)
        if merge is not None:
            choices, inputs, output = merge
            self.original_output = output
            self.setWindowTitle("配置合路开关")
            self.merge_list = QListWidget()
            self.merge_list.setMaximumHeight(125)
            for route in dict.fromkeys([*inputs, *choices]):
                item = QListWidgetItem(f"分支 {route}", self.merge_list)
                item.setData(Qt.ItemDataRole.UserRole, route)
                item.setCheckState(Qt.CheckState.Checked if route in inputs else Qt.CheckState.Unchecked)
            form.addRow("输入支路", self.merge_list)
            self.output = QLineEdit(output)
            form.addRow("公共输出编号", self.output)
        self.fields = {}
        for key, label in (("model", "型号"), ("mark", "标记／编号"), ("value", "增益／衰减或数值"),
                           ("unit", "单位"), ("frequency", "频率／频段"), ("terminal", "端子"),
                           ("notes", "备注")):
            value = component.get(key)
            editor = QLineEdit("" if value is None else str(value))
            self.fields[key] = editor
            form.addRow(label, editor)
        self.input_form = form
        self.input_ports = {}
        if input_ports is not None:
            for (field, _), label, kinds in zip(BIAS_TEE_INPUTS, ("信号输入端口（RF）", "直流输入端口（DC）"),
                                                      ({"XY/Z", "读取输入"}, {"直流"})):
                selector = QComboBox()
                selector.addItem("未关联")
                for port in input_ports.values():
                    if stored_line_type(port["kind"]) in kinds:
                        selector.addItem(f"{port['board']}{port['number']} · {display_line_type(port['kind'])}", port["id"])
                saved = component.get(field)
                if saved and selector.findData(saved) < 0:
                    port = input_ports.get(saved)
                    label_text = f"{port['board']}{port['number']} · 类型已变更" if port else "端口已删除"
                    selector.addItem(label_text, saved)
                selector.setCurrentIndex(max(0, selector.findData(saved)))
                self.input_ports[field] = selector
                form.addRow(label, selector)
        self.value_label = form.labelForField(self.fields["value"])
        self.wire_number = QLineEdit(component.get("wire_number", ""))
        form.addRow("电线编号", self.wire_number)
        self.has_reset = QCheckBox("有 Reset 控制端")
        self.has_reset.setChecked(bool(component.get("has_reset")))
        form.addRow(self.has_reset)
        self.control_wires = QTableWidget(0, 2)
        self.control_wires.setHorizontalHeaderLabels(("控制端（对应射频分支）", "电线编号"))
        self.control_wires.horizontalHeader().setStretchLastSection(True)
        self.control_wires.setMaximumHeight(180)
        self.control_wires.verticalHeader().hide()
        form.addRow("开关控制电线", self.control_wires)
        self.wire_values = dict(component.get("control_wires", {}))
        self.wire_editors = {}
        self.has_reset.toggled.connect(self.refresh_control_wires)
        if self.branches is not None:
            self.branches.textChanged.connect(self.refresh_control_wires)
        if self.merge_list is not None:
            self.merge_list.itemChanged.connect(self.refresh_control_wires)
        self.refresh_control_wires()
        self.kind.currentTextChanged.connect(self.update_kind_fields)
        self.update_kind_fields()
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setProperty("role", "danger")
        layout.addWidget(self.error)
        dialog_controls(self, layout, "确定")

    def update_kind_fields(self):
        kind = self.kind.currentText().strip()
        bias_tee = kind == BIAS_TEE_KIND
        self.value_label.setText("RF 插入损耗" if bias_tee else "增益／衰减或数值")
        self.fields["unit"].setPlaceholderText("dB" if bias_tee else "")
        self.fields["terminal"].setPlaceholderText(BIAS_TEE_TERMINALS if bias_tee else "")
        self.fields["notes"].setPlaceholderText("可记录直流额定电流、电压、RF–DC 隔离和测试温度" if bias_tee else "")
        for selector in self.input_ports.values():
            self.input_form.setRowVisible(selector, bias_tee)
        switch = kind == SWITCH_KIND
        self.input_form.setRowVisible(self.wire_number, not switch)
        self.input_form.setRowVisible(self.has_reset, switch)
        self.input_form.setRowVisible(self.control_wires, switch)

    def refresh_control_wires(self, *_):
        self.wire_values.update((key, editor.text()) for key, editor in self.wire_editors.items())
        routes = self.branch_names() if self.branches is not None else self.merge_inputs() if self.merge_list is not None else []
        terminals = [("C", "C · 公共端")]
        for index, route in enumerate(routes, 1):
            label = str(index)
            terminals.append((label, label if label == route else f"{label} · 分支 {route}"))
        if self.has_reset.isChecked():
            terminals.append(("Reset", "Reset · 复位控制端"))
        self.wire_editors.clear()
        self.control_wires.setRowCount(len(terminals))
        for row, (key, label) in enumerate(terminals):
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.control_wires.setItem(row, 0, item)
            editor = QLineEdit(self.wire_values.get(key, ""))
            self.control_wires.setCellWidget(row, 1, editor)
            self.wire_editors[key] = editor
        self.control_wires.resizeColumnToContents(0)

    def branch_names(self):
        return [text.strip() for text in self.branches.text().replace("，", ",").split(",") if text.strip()]

    def merge_inputs(self):
        items = (self.merge_list.item(index) for index in range(self.merge_list.count()))
        return [item.data(Qt.ItemDataRole.UserRole) for item in items if item.checkState() == Qt.CheckState.Checked]

    def accept(self):
        kind = self.kind.currentText().strip()
        if not kind or (self.name is not None and not self.name.text().strip()):
            self.error.setText("请输入元件类型和所需名称。")
            return
        if self.branches is not None:
            branches = self.branch_names()
            if not branches or len(set(branches)) != len(branches):
                self.error.setText("请填写至少一个分支编号，编号不能重复。")
                return
            if any(name.casefold() == "reset" for name in set(branches) - self.original_branches):
                self.error.setText("Reset 是复位控制端，请通过“有 Reset 控制端”登记。")
                return
        elif self.merge_list is not None:
            output = self.output.text().strip()
            if len(self.merge_inputs()) < 2 or not output:
                self.error.setText("请选择至少两条输入支路，并填写公共输出编号。")
                return
            if output != self.original_output and output.casefold() == "reset":
                self.error.setText("Reset 是复位控制端，请使用其他公共输出编号。")
                return
        elif self.name is None and kind == SWITCH_KIND:
            self.error.setText("请通过“配置微波开关”添加开关。")
            return
        fields = {key: editor.text().strip() for key, editor in self.fields.items()}
        if kind == BIAS_TEE_KIND:
            fields["unit"] = fields["unit"] or "dB"
            fields["terminal"] = fields["terminal"] or BIAS_TEE_TERMINALS
        try:
            value = float(fields["value"]) if fields["value"] else None
            if value is not None and not math.isfinite(value):
                raise ValueError
        except ValueError:
            self.error.setText("数值应为有限数字，也可留空。")
            return
        self.result_component = classify_infrared({**self.original, **fields, "kind": kind, "value": value})
        if kind == SWITCH_KIND:
            self.result_component.pop("wire_number", None)
            if self.has_reset.isChecked():
                self.result_component["has_reset"] = True
            else:
                self.result_component.pop("has_reset", None)
            wires = {terminal: value for terminal, editor in self.wire_editors.items() if (value := editor.text().strip())}
            if wires:
                self.result_component["control_wires"] = wires
            else:
                self.result_component.pop("control_wires", None)
        else:
            for key in ("has_reset", "control_wires"):
                self.result_component.pop(key, None)
            if wire := self.wire_number.text().strip():
                self.result_component["wire_number"] = wire
            else:
                self.result_component.pop("wire_number", None)
        for field, selector in self.input_ports.items():
            port_id = selector.currentData() if kind == BIAS_TEE_KIND else None
            if port_id is None:
                self.result_component.pop(field, None)
            else:
                self.result_component[field] = port_id
        if self.stage is not None:
            self.result_component["stage"] = self.stage.currentText().strip()
        if self.direction is not None:
            self.result_component["direction"] = self.direction.currentData()
        if self.merge_list is not None:
            self.result_component["direction"] = "fan_in"
        super().accept()


class ComponentLibraryDialog(QDialog):
    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.theme = parent.theme
        self.setWindowTitle("制冷机元件库")
        self.resize(720, 440)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("库条目", "类型", "默认参数"))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        tool_button(actions, self, "新增条目", lambda: self.edit(new=True), "new")
        tool_button(actions, self, "编辑条目", self.edit, "edit")
        tool_button(actions, self, "删除条目", self.remove, "delete")
        actions.addStretch()
        dialog_controls(self, layout)
        self.reload()

    def reload(self):
        self.entries = self.store.component_library()
        self.table.setRowCount(len(self.entries))
        for row, (_, name, component) in enumerate(self.entries):
            for col, value in enumerate((name, component["kind"], component_text(component))):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def edit(self, *, new=False):
        row = self.table.currentRow()
        if row < 0 and not new:
            return
        key, name, component = (None, "", {"kind": "衰减器", "unit": "dB"}) if new else self.entries[row]
        editor = ComponentDialog(self, component, library_name=name)
        if editor.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.save_library_component(key, editor.name.text().strip(), editor.result_component)
            except sqlite3.Error as exc:
                QMessageBox.warning(self, "保存元件失败", str(exc))
            self.reload()

    def remove(self):
        row = self.table.currentRow()
        if row >= 0:
            try:
                self.store.delete_library_component(self.entries[row][0])
            except sqlite3.Error as exc:
                QMessageBox.warning(self, "删除元件失败", str(exc))
            self.reload()


class CircuitDialog(QDialog):
    def __init__(self, store, port, parent=None, *, others=(), read_only=False, initial_branch=None,
                 branch_options=()):
        super().__init__(parent)
        self.store = store
        self.port = port
        self.ports = [port, *others]
        self.input_ports = {p["id"]: p for p in sorted(store.ports(), key=lambda p: (
            port_number_key(p["board"]), port_number_key(p["number"])))}
        self.bias_tee_endpoints = {}
        for connection in store.bias_tee_assignments():
            for key in ((connection.rf_port_id, connection.branch), (connection.dc_port_id, None)):
                self.bias_tee_endpoints.setdefault(key, []).append(connection.description(key[0], self.input_ports))
        self.theme = getattr(parent, "theme", "light")
        self.circuits = {p["id"]: store.circuit(p["id"]) for p in self.ports}
        self.draft = deepcopy(self.circuits[port["id"]])
        self.merge_origins = {name: name for name in self.draft.get("merge_switches", {})}
        self.branch_options = set(branch_options)
        self.chosen_branch = initial_branch if initial_branch in self.branch_options else None
        self.editable = not (others or read_only or self.branch_options) and store.circuit_editable(port, self.draft)
        self.expanded = {port["id"]} if not others else set()
        self.selected = None
        self.endpoints = {}
        cache = {}
        for p in self.ports:
            assignments = {r["branch"]: r for r in store.branch_assignments(p["id"])}
            marked = store.branch_marks(p["id"])
            self.endpoints[p["id"]] = (
                self.endpoint(p, p, p["manual_status"], cache) if p["direct_sample_id"] else
                self.endpoint(p, None, p["manual_status"], cache),
                {name: self.endpoint(p, assignments.get(name), "occupied" if name in marked else "", cache)
                 for name in terminal_branches(self.circuits[p["id"]])},
                set(assignments) | marked | set(json.loads(p["occupied_drawings"])),
            )
        self.setWindowTitle(f"{port['board']}{port['number']} · 低温线路图" if not others else
                            f"低温线路对照 · {len(self.ports)} 个端口")
        if self.branch_options:
            self.setWindowTitle(f"{port['board']}{port['number']} · 选择接线端")
        self.resize(1180, 650)
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.view = CircuitView(self.theme)
        self.view.reorderable = self.editable
        self.view.clicked.connect(self.select_symbol)
        tool_button(actions, self, "适配视图", self.view.fit, "fit")
        if self.editable:
            self.route = QComboBox()
            self.route.setToolTip("选择要登记元件或添加开关的线路段")
            self.route.currentIndexChanged.connect(self.route_changed)
            actions.addWidget(self.route)
            tool_button(actions, self, "新增元件", self.add_component, "new")
            self.edit_button = tool_button(actions, self, "编辑", self.edit_component, "edit")
            self.delete_button = tool_button(actions, self, "删除", self.delete_component, "delete")
            self.up_button = tool_button(actions, self, "前移", lambda: self.move_component(-1), "up")
            self.down_button = tool_button(actions, self, "后移", lambda: self.move_component(1), "down")
            tool_button(actions, self, "配置微波开关", self.configure_switch, "wiring")
            tool_button(actions, self, "配置合路开关", self.configure_merge, "connect")
            self.view.edit_requested.connect(self.edit_clicked)
            self.view.reorder_requested.connect(self.reorder_components)
        actions.addStretch()
        legend = QHBoxLayout()
        layout.addLayout(legend)
        for kind, name in (("衰减器", "衰减／通用元件"), ("放大器", "放大器"), ("滤波器", "滤波器"),
                           ("隔离器", "隔离器／环行器"), (BIAS_TEE_KIND, BIAS_TEE_KIND), (SWITCH_KIND, SWITCH_KIND)):
            glyph = QLabel()
            glyph.setPixmap(self.view.legend_icon(kind))
            legend.addWidget(glyph)
            label = QLabel(name)
            label.setProperty("role", "muted")
            legend.addWidget(label)
        legend.addStretch()
        layout.addWidget(self.view, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("拖动同温级元件可调换次序。右键拖动平移，滚轮缩放。" if self.editable else
                                        "点击图中元件查看参数。右键拖动平移，滚轮缩放。")
        self.details.setMaximumHeight(98)
        layout.addWidget(self.details)
        if self.editable:
            legacy = QHBoxLayout()
            layout.addLayout(legacy)
            tool_button(legacy, self, "从旧记录导入", self.import_legacy, "open")
            tool_button(legacy, self, "查看导入旧资料", self.view_imported, "history")
            legacy.addStretch()
            self.error = QLabel()
            self.error.setWordWrap(True)
            self.error.setProperty("role", "danger")
            layout.addWidget(self.error)
            self.update_routes(initial_branch)
        elif self.branch_options:
            layout.addWidget(QLabel("点击线路图上的末端分支或公共输出编号，再点“确定”。"))
        elif not (others or read_only):
            layout.addWidget(QLabel("端口已手动标记为占用或损坏。取消标记后可编辑线路。"))
        self.choose_button = None
        if self.branch_options:
            box = dialog_controls(self, layout, "确定")
            self.choose_button = box.button(QDialogButtonBox.StandardButton.Save)
        else:
            dialog_controls(self, layout, "保存线路" if self.editable else None)
        self.refresh()
        QTimer.singleShot(0, self.view.fit)

    def endpoint(self, port, row, mark, cache):
        state = {"occupied": "手动占用", "damaged": "已损坏"}
        status = state.get(port["manual_status"] or mark)
        if not row:
            return (status or "未分配",)
        box, connection_state, devices = self.store.resolve_assignment(row, cache)
        missing = "" if self.store.sample_path(row["sample_id"]).is_file() else "（样品不可用）"
        sample = f"{row['sample_name']}{missing} · {display_line_type(row['usage'] or port['kind'])}"
        target = f"样品盒接口 {box['number']}" if box else row["pin"] or "、".join(d["label"] for d in devices)
        if connection_state and row["box_port_id"] and not box:
            target = connection_state
        return (sample + (f" · {status}" if status else ""), target or "已分配")

    def update_routes(self, selected=None):
        with QSignalBlocker(self.route):
            self.route.clear()
            self.route.addItem("共用段" if self.draft.get("switch") else "端口线路", None)
            for branch in route_parents(self.draft):
                self.route.addItem(f"分支 {branch}", branch)
            for branch in self.draft.get("merge_switches", {}):
                self.route.addItem(f"公共输出 {branch}", branch)
            self.route.setCurrentIndex(max(0, self.route.findData(selected)))

    def route_changed(self):
        if self.selected and self.selected[1] != self.route.currentData():
            self.selected = None
        self.expanded.add(self.port["id"])
        self.refresh()

    def rows(self, branch):
        if branch is None:
            return self.draft.setdefault("components", [])
        return self.draft.setdefault("branch_components", {}).setdefault(branch, [])

    def lanes(self, port):
        key = port["id"]
        circuit = self.draft if key == self.port["id"] else self.circuits[key]
        direct, endpoints, occupied = self.endpoints[key]
        parents = route_parents(circuit)
        default_endpoint = self.endpoint(port, None, "", {})
        paths = circuit.get("branch_components", {})
        merges = circuit.get("merge_switches", {})
        joined = {route: output for output, node in merges.items() for route in node["inputs"]}
        lanes = []
        depths = {}
        for route, parent in [(None, None), *parents.items()]:
            depth = 0 if route is None else depths[parent] + 1
            depths[route] = depth
            symbols = list(enumerate(circuit.get("components", []) if route is None else paths.get(route, [])))
            endpoint = direct if route is None else endpoints.get(route, default_endpoint)
            if route in joined:
                endpoint = (f"接入公共输出 {joined[route]}",)
            switch = switch_on(circuit, route)
            if switch is not None:
                symbols.append(("switch", dict(switch, kind=SWITCH_KIND)))
                direction = SWITCH_DIRECTIONS.get(switch.get("direction"))
                if (route is None and port["direct_sample_id"]) or route in occupied:
                    endpoint = ("保存时可选择原连接的归属分支", endpoint[0])
                else:
                    endpoint = ("开关共用段" + (f" · {direction}" if direction else ""),)
            endpoint = self.with_bias_tee_endpoint(key, route, endpoint)
            lanes.append(CircuitLane(route, parent, depth, endpoint, symbols))
        first_merge_depth = max(depths.values()) + 1
        for index, (output, node) in enumerate(merges.items()):
            inputs = tuple(node["inputs"])
            position = max(i for i, lane in enumerate(lanes) if lane.route in inputs)
            depth = first_merge_depth + index
            symbols = [("merge", dict(node["component"], kind=SWITCH_KIND))]
            symbols.extend(enumerate(paths.get(output, [])))
            original = self.merge_origins.get(output) if key == self.port["id"] else output
            endpoint = self.with_bias_tee_endpoint(key, original, endpoints.get(original, default_endpoint))
            lanes.insert(position + 1, CircuitLane(output, None, depth, endpoint,
                                                  symbols, inputs))
        return lanes

    def with_bias_tee_endpoint(self, port_id, branch, endpoint):
        associated = self.bias_tee_endpoints.get((port_id, branch))
        if not associated:
            return endpoint
        label = "、".join(associated)
        return (label,) if endpoint == ("未分配",) else (*endpoint, label)

    def refresh(self):
        routes = [(port, self.lanes(port)) for port in self.ports]
        self.view.render(routes, self.expanded, self.selected, branch_options=self.branch_options,
                         chosen_branch=self.chosen_branch, input_ports=self.input_ports)
        self.update_details()

    def select_symbol(self, key):
        port, branch, index = key
        if index == "toggle":
            self.expanded.symmetric_difference_update({port})
            if self.selected and self.selected[0] == port and self.selected[1] is not None:
                self.selected = None
        elif index == "endpoint":
            self.selected = None
            self.chosen_branch = branch
        else:
            self.selected = key
            if index == "merge" and branch in self.branch_options:
                self.chosen_branch = branch
            if self.editable:
                with QSignalBlocker(self.route):
                    self.route.setCurrentIndex(max(0, self.route.findData(branch)))
        self.refresh()

    def update_details(self):
        selected = self.selected
        is_component = bool(selected and isinstance(selected[2], int))
        if selected:
            port_id, branch, index = selected
            port = next(p for p in self.ports if p["id"] == port_id)
            lane = next(lane for lane in self.lanes(port) if lane.route == branch)
            component = dict(lane.symbols)[index]
            title = f"{port['board']}{port['number']}" + (f" · 分支 {branch}" if branch is not None else "")
            stage = component.get("stage") or "温级未登记"
            self.details.setPlainText(f"{title} · {stage}\n{component_text(component)}")
            if inputs := bias_tee_input_labels(component, self.input_ports):
                self.details.appendPlainText(" · ".join(inputs))
            if index == "merge":
                circuit = self.draft if port_id == self.port["id"] else self.circuits[port_id]
                inputs = "、".join(circuit["merge_switches"][branch]["inputs"])
                self.details.appendPlainText(f"输入支路 {inputs} → 公共输出 {branch}")
                labels = (f"{index}={route}" for index, route in enumerate(circuit["merge_switches"][branch]["inputs"], 1))
                self.details.appendPlainText("端口对应 " + "，".join(labels))
        elif self.chosen_branch is not None:
            prefix = "公共输出" if self.chosen_branch in self.draft.get("merge_switches", {}) else "末端分支"
            self.details.setPlainText(f"已选择 {prefix} {self.chosen_branch}")
        else:
            self.details.clear()
        if self.choose_button is not None:
            self.choose_button.setEnabled(self.chosen_branch is not None)
        if self.editable:
            self.edit_button.setEnabled(bool(selected))
            self.delete_button.setEnabled(is_component or bool(selected and (
                selected[2] == "merge" or not self.port["manually_occupied"])))
            self.up_button.setEnabled(is_component and self.neighbor(-1) is not None)
            self.down_button.setEnabled(is_component and self.neighbor(1) is not None)

    def add_component(self):
        custom = self.store.component_library()
        options = [f"{name}（项目条目 {i}）" for i, (_, name, _) in enumerate(custom, 1)]
        options += [f"{entry['kind']}（内置类型）" for entry in LIBRARY_DEFAULTS]
        templates = [component for _, _, component in custom] + list(LIBRARY_DEFAULTS)
        options.append("自定义类型")
        templates.append({})
        choice, ok = QInputDialog.getItem(self, "新增线路元件", "选择元件", options, 0, False)
        if not ok:
            return
        component = templates[options.index(choice)]
        if component.get("kind") == SWITCH_KIND:
            if switch_on(self.draft, self.route.currentData()) is not None:
                self.error.setText("当前段已在微波开关处结束。选择下游分支可继续添加开关。")
                return
            self.configure_switch(component)
            return
        branch = self.route.currentData()
        stage = ""
        if self.selected and self.selected[1] == branch and isinstance(self.selected[2], int):
            stage = self.rows(branch)[self.selected[2]].get("stage", "")
        dialog = ComponentDialog(self, component, stage=stage, input_ports=self.input_ports)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rows = self.rows(branch)
            rows.append(dialog.result_component)
            if branch is not None:
                self.expanded.add(self.port["id"])
            self.selected = (self.port["id"], branch, len(rows) - 1)
            self.refresh()

    def edit_clicked(self, key):
        if key[2] != "toggle":
            self.select_symbol(key)
            self.edit_component()

    def edit_component(self):
        if not self.selected:
            return
        _, branch, index = self.selected
        if index == "switch":
            self.configure_switch()
            return
        if index == "merge":
            self.configure_merge()
            return
        rows = self.rows(branch)
        dialog = ComponentDialog(self, rows[index], input_ports=self.input_ports)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rows[index] = dialog.result_component
            self.refresh()

    def neighbor(self, offset):
        _, branch, index = self.selected
        rows = self.rows(branch)
        stage = rows[index].get("stage") or ""
        end = -1 if offset < 0 else len(rows)
        return next((i for i in range(index + offset, end, offset) if (rows[i].get("stage") or "") == stage), None)

    def move_component(self, offset):
        key, branch, index = self.selected
        other = self.neighbor(offset)
        if other is not None:
            self.reorder_components(self.selected, (key, branch, other))

    def reorder_components(self, source, target):
        _, branch, index = source
        rows = self.rows(branch)
        other = target[2]
        rows[index], rows[other] = rows[other], rows[index]
        self.selected = target
        self.refresh()

    def delete_component(self):
        _, branch, index = self.selected
        if index == "switch":
            self.change_branches(branch, [])
        elif index == "merge":
            paths = self.draft.get("branch_components", {})
            if paths.get(branch) and QMessageBox.question(self, "移除合路开关", "同时移除公共输出上的元件？") != QMessageBox.StandardButton.Yes:
                return
            paths.pop(branch, None)
            del self.draft["merge_switches"][branch]
            self.merge_origins.pop(branch, None)
            self.selected = None
            self.update_routes()
            self.refresh()
        else:
            self.rows(branch).pop(index)
            self.selected = None
            self.refresh()

    def configure_switch(self, template=None):
        route = self.route.currentData()
        merges = self.draft.get("merge_switches", {})
        if route in merges:
            self.configure_merge()
            return
        if any(route in node["inputs"] for node in merges.values()):
            self.error.setText("此段已连接合路开关。请选择其他分支配置分路。")
            return
        existing = switch_on(self.draft, route)
        if existing is None and self.port["manually_occupied"]:
            self.error.setText("取消整端口的手动占用标记后可新增开关。")
            return
        names = (self.draft.get("switch", []) if route is None else
                 self.draft.get("branch_switches", {}).get(route, {}).get("branches", []))
        component = existing if existing is not None else {"direction": "fan_out", **(template or {})}
        editor = ComponentDialog(self, dict(component, kind=SWITCH_KIND),
                                 branches=names or ["1", "2"])
        if self.port["manually_occupied"]:
            editor.branches.setReadOnly(True)
            editor.branches.setToolTip("取消整端口的手动占用标记后可修改分支编号")
        if editor.exec() == QDialog.DialogCode.Accepted:
            self.change_branches(route, editor.branch_names(), editor.result_component)

    def configure_merge(self):
        route = self.route.currentData()
        merges = self.draft.get("merge_switches", {})
        occupied_inputs = {branch for output, node in merges.items() if output != route for branch in node["inputs"]}
        parents = route_parents(self.draft)
        switches = self.draft.get("branch_switches", {})
        choices = [branch for branch in parents if branch not in switches and branch not in occupied_inputs]
        if len(choices) < 2:
            self.error.setText("合路需要至少两条未接入其他合路的末端分支。")
            return
        existing = merges.get(route)
        name = route
        if existing is None:
            i = 1
            while f"合路{i}" in parents or f"合路{i}" in merges:
                i += 1
            name = f"合路{i}"
        component = existing["component"] if existing is not None else {}
        inputs = existing["inputs"] if existing is not None else ([route] if route in choices else [])
        editor = ComponentDialog(self, dict(component, kind=SWITCH_KIND), merge=(choices, inputs, name))
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        name = editor.output.text().strip()
        if name in parents or (name in merges and name != route):
            self.error.setText("公共输出编号已存在，请使用其他编号。")
            return
        updated = {"inputs": editor.merge_inputs(), "component": editor.result_component}
        if existing is None:
            self.draft.setdefault("merge_switches", {})[name] = updated
        else:
            self.draft["merge_switches"] = {
                (name if output == route else output): (updated if output == route else node)
                for output, node in merges.items()
            }
            if name != route:
                origin = self.merge_origins.pop(route, None)
                if origin is not None:
                    self.merge_origins[name] = origin
                paths = self.draft.get("branch_components", {})
                if route in paths:
                    paths[name] = paths.pop(route)
        key = self.port["id"]
        self.selected = (key, name, "merge")
        self.expanded.add(key)
        self.update_routes(name)
        self.error.clear()
        self.refresh()

    def change_branches(self, route, branches, component=None):
        key = self.port["id"]
        candidate = deepcopy(self.draft)
        switches = candidate.get("branch_switches", {})
        if route is None:
            candidate["switch"] = branches
            if branches:
                candidate["switch_component"] = component
            else:
                candidate.pop("switch_component", None)
        elif branches:
            switches[route] = {"branches": branches, "component": component}
        else:
            switches.pop(route, None)
        if switches:
            candidate["branch_switches"] = switches
        try:
            parents = route_parents(candidate)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        removed = set(route_parents(self.draft)) - set(parents)
        if set(parents) & set(candidate.get("merge_switches", {})):
            self.error.setText("分支编号不能与公共输出编号重复。")
            return
        if any(removed.intersection(node["inputs"]) for node in candidate.get("merge_switches", {}).values()):
            self.error.setText("这些分支已接入合路开关，请先编辑或移除对应的合路。")
            return
        used = {row["branch"] for row in self.store.branch_assignments(key)} | self.store.branch_marks(key)
        if removed & used:
            self.error.setText("请先解除被移除分支的样品分配或手动占用。")
            return
        paths = candidate.get("branch_components", {})
        removed_with_components = sorted(name for name in removed if paths.get(name) or name in switches)
        if removed_with_components:
            names = "、".join(removed_with_components)
            if QMessageBox.question(self, "移除分支元件", f"移除分支 {names} 及其线路元件？") != QMessageBox.StandardButton.Yes:
                return
        for name in removed:
            paths.pop(name, None)
            switches.pop(name, None)
        if not switches:
            candidate.pop("branch_switches", None)
        self.draft = candidate
        self.selected = (key, route, "switch") if branches else None
        self.expanded.add(key)
        self.update_routes(route)
        self.error.clear()
        self.refresh()

    def import_legacy(self):
        records = []
        if self.port["legacy_json"] and self.port["legacy_json"] != "{}":
            records.append(("原端口记录", self.port["legacy_json"]))
        records += [(f"分支 {r['branch']}", r["legacy_json"]) for r in self.store.branch_assignments(self.port["id"])
                    if r["legacy_json"] and r["legacy_json"] != "{}"]
        if not records:
            QMessageBox.information(self, "旧记录", "此端口没有可导入的旧记录。")
            return
        label, ok = QInputDialog.getItem(self, "旧记录", "选择要导入的记录", [r[0] for r in records], 0, False)
        if not ok:
            return
        if self.draft.get("components") and QMessageBox.question(
                self, "导入旧记录", "用旧记录的温级元件替换端口共用段？原始旧记录仍会保留。") != QMessageBox.StandardButton.Yes:
            return
        details = json.loads(dict(records)[label]).get("details", {})
        self.draft["legacy_details"] = details
        self.draft["components"] = [
            classify_infrared(dict(row, mark=row.get("name", ""))) for row in details.get("components", [])
        ]
        self.selected = None
        self.refresh()

    def view_imported(self):
        details = self.draft.get("legacy_details")
        if not details:
            QMessageBox.information(self, "导入旧资料", "尚未导入旧资料。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("导入的旧资料（只读）")
        dialog.resize(700, 500)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(json.dumps(details, ensure_ascii=False, indent=2))
        layout.addWidget(view)
        dialog_controls(dialog, layout)
        dialog.exec()

    def accept(self):
        if self.branch_options and self.chosen_branch is None:
            return
        if not self.editable:
            return super().accept()
        key = self.port["id"]
        branches = terminal_branches(self.draft)
        transfer = None
        if self.port["direct_sample_id"] and branches:
            transfer, ok = QInputDialog.getItem(self, "已有样品", "选择现有连接归属的分支", branches, 0, False)
            if not ok:
                return
        original = self.circuits[key]
        discarded = (set(route_parents(original)) | set(original.get("merge_switches", {}))) - (
            set(route_parents(self.draft)) | set(self.draft.get("merge_switches", {})))
        occupied = {row["branch"] for row in self.store.branch_assignments(key)} | self.store.branch_marks(key)
        drawings = json.loads(self.port["occupied_drawings"])
        renames = {origin: name for name, origin in self.merge_origins.items() if name != origin}
        transfers = {}
        for old in terminal_branches(original):
            if old in renames or old in branches or (old not in occupied and old not in drawings):
                continue
            options = transfer_destinations(original, self.draft, old)
            if not options:
                continue
            if len(options) == 1:
                transfers[old] = options[0]
                continue
            target, ok = QInputDialog.getItem(self, "转移分支接线", f"选择分支 {old} 的原接线归属",
                                              options, 0, False)
            if not ok:
                return
            transfers[old] = target
        try:
            self.store.save_circuit(key, self.draft, transfer, discarded, transfers, merge_renames=renames)
        except (ValueError, sqlite3.Error) as exc:
            self.error.setText(str(exc))
            return
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

"""Shared drafts and the reusable metadata editor for chip objects."""

from __future__ import annotations

import math
from copy import deepcopy
from collections.abc import Callable
from contextlib import contextmanager

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .storage import ChipStore
from .device_types import DEVICE_NAMES, SUBTYPES
from .undo import undoable


DEVICE_FIELDS = ("label", "subtype", "role", "x", "y", "notes")
ROLES = (("未指定", ""), ("数据比特", "data"), ("X 测量", "measure_x"), ("Z 测量", "measure_z"))


def combo_value(combo: QComboBox) -> str:
    """Read the stored key of a choice, preserving arbitrary editable text."""
    text = combo.currentText()
    index = combo.currentIndex()
    if index >= 0 and text == combo.itemText(index):
        data = combo.itemData(index)
        return str(data) if data is not None else text
    # An editable combo can keep its previous index while a known label is typed.
    index = combo.findText(text)
    if index >= 0:
        data = combo.itemData(index)
        return str(data) if data is not None else text
    return text


def _key(kind: str, ident: str) -> tuple[str, str]:
    if kind in DEVICE_NAMES:
        kind = "device"
    if kind not in ("device", "coupling", "link", "readout_link"):
        raise ValueError(f"不支持的对象类型：{kind}")
    return kind, ident


class DraftRegistry(QObject):
    """Keep one editable field snapshot per object across every editor."""

    changed = Signal(str, str)

    def __init__(self, store_getter: Callable[[], ChipStore | None], parent=None):
        super().__init__(parent)
        self._store_getter = store_getter
        self._drafts: dict[tuple[str, str], dict] = {}
        self.undo_history = None

    def undo_state(self):
        return deepcopy({key: state for key, state in self._drafts.items()
                         if state["values"] != state["snapshot"] or state["error"]})

    def restore_undo_state(self, snapshot):
        keys = set(self._drafts) | set(snapshot)
        self._drafts = deepcopy(snapshot)
        for key in keys:
            self.changed.emit(*key)

    def _store(self) -> ChipStore:
        store = self._store_getter()
        if store is None:
            raise RuntimeError("请先打开芯片文件")
        return store

    def _read(self, kind: str, ident: str) -> tuple[str, dict]:
        store = self._store()
        if kind == "device":
            row = store.device(ident)
            fields = DEVICE_FIELDS + (("drawing_number",) if row["kind"] == "qubit" else ()) + (("readout_length", "readout_direction", "readout_angle") if row["kind"] == "readout" else ())
            return row["kind"], {field: row[field] for field in fields}
        row = (store.coupling(ident) if kind == "coupling" else
               store.readout_link(ident) if kind == "readout_link" else store.link(ident))
        return kind, {"notes": row["notes"]}

    def ensure(self, kind: str, ident: str) -> dict:
        key = _key(kind, ident)
        scope, saved = self._read(*key)
        state = self._drafts.get(key)
        if state is None:
            state = {"snapshot": saved.copy(), "values": saved.copy(), "scope": scope, "error": ""}
            self._drafts[key] = state
        elif not self.dirty(*key) and saved != state["snapshot"]:
            state.update(snapshot=saved.copy(), values=saved.copy(), scope=scope, error="")
            self.changed.emit(*key)
        return state

    def values(self, kind: str, ident: str) -> dict:
        return self.ensure(kind, ident)["values"].copy()

    @undoable("编辑器件信息", merge=True)
    def update(self, kind: str, ident: str, field: str, value: object) -> None:
        key = _key(kind, ident)
        state = self.ensure(*key)
        if field not in state["values"]:
            raise ValueError(f"不支持的编辑字段：{field}")
        if state["values"][field] != value:
            state["values"][field] = value
            state["error"] = ""
            self.changed.emit(*key)

    def dirty(self, kind: str, ident: str) -> bool:
        state = self._drafts.get(_key(kind, ident))
        return state is not None and state["values"] != state["snapshot"]

    def dirty_keys(self) -> list[tuple[str, str]]:
        return [key for key in self._drafts if self.dirty(*key)]

    def error(self, kind: str, ident: str) -> str:
        state = self._drafts.get(_key(kind, ident))
        return state["error"] if state else ""

    @undoable("保存器件更改")
    def save(self, kind: str, ident: str) -> None:
        key = _key(kind, ident)
        try:
            state = self.ensure(*key)
            values = state["values"].copy()
            if key[0] == "device":
                values["label"] = str(values["label"]).strip()
                if not values["label"]:
                    raise ValueError("器件名称不能为空")
                for field in ("x", "y"):
                    values[field] = float(values[field])
                    if not math.isfinite(values[field]):
                        raise ValueError("格点坐标必须为有限数值")
                changes = {field: value for field, value in values.items()
                           if value != state["snapshot"][field]}
                if changes:
                    self._store().update_device(ident, **changes)
            elif values != state["snapshot"]:
                store = self._store()
                if key[0] == "coupling":
                    store.update_coupling_notes(ident, values["notes"])
                elif key[0] == "readout_link":
                    store.update_readout_link_notes(ident, values["notes"])
                else:
                    store.update_link_notes(ident, values["notes"])
            scope, saved = self._read(*key)
        except Exception as exc:
            state = self._drafts.get(key)
            if state is not None:
                state["error"] = str(exc)
                self.changed.emit(*key)
            raise
        state.update(snapshot=saved.copy(), values=saved.copy(), scope=scope, error="")
        self.changed.emit(*key)

    @undoable("取消器件更改")
    def reset(self, kind: str, ident: str) -> None:
        key = _key(kind, ident)
        scope, saved = self._read(*key)
        self._drafts[key] = {"snapshot": saved.copy(), "values": saved.copy(), "scope": scope, "error": ""}
        self.changed.emit(*key)

    def clear(self) -> None:
        keys = list(self._drafts)
        self._drafts.clear()
        for key in keys:
            self.changed.emit(*key)

    def discard_missing(self) -> None:
        if self._store_getter() is None:
            self.clear()
            return
        for key in list(self._drafts):
            try:
                self._read(*key)
            except KeyError:
                del self._drafts[key]
                self.changed.emit(*key)


@contextmanager
def _blocked(widget):
    previous = widget.blockSignals(True)
    try:
        yield
    finally:
        widget.blockSignals(previous)


def _set_line(edit: QLineEdit, text: str) -> None:
    if edit.text() == text:
        return
    cursor = edit.cursorPosition()
    selection = edit.selectionStart()
    length = len(edit.selectedText())
    with _blocked(edit):
        edit.setText(text)
        edit.setCursorPosition(min(cursor, len(text)))
        if selection >= 0:
            edit.setSelection(min(selection, len(text)), min(length, max(0, len(text) - selection)))


def _set_combo(combo: QComboBox, value: str) -> None:
    if combo_value(combo) == value:
        return
    with _blocked(combo):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setCurrentIndex(-1)
            _set_line(combo.lineEdit(), value)


class DeviceEditor(QWidget):
    """The shared six metadata fields, with draft and save error feedback."""

    def __init__(self, registry: DraftRegistry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.bound: tuple[str, str] | None = None
        self._scope: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.label = QLineEdit()
        self.label.setPlaceholderText("支持 Q_{1}、\\omega_{01} 等标签")
        self.subtype = QComboBox()
        self.subtype.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.role = QComboBox()
        self.role.setEditable(True)
        self.role.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for combo in (self.subtype, self.role):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for caption, value in ROLES:
            self.role.addItem(caption, value)
        self.x = QDoubleSpinBox()
        self.y = QDoubleSpinBox()
        for spin in (self.x, self.y):
            spin.setRange(-100000000, 100000000)
            spin.setDecimals(2)
            spin.setMinimumWidth(60)
            spin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.coordinates = QWidget()
        positions = QHBoxLayout(self.coordinates)
        positions.setContentsMargins(0, 0, 0, 0)
        for caption, spin in (("X", self.x), ("Y", self.y)):
            axis = QLabel(caption)
            axis.setProperty("role", "muted")
            positions.addWidget(axis)
            positions.addWidget(spin, 1)
        self.notes = QLineEdit()
        self.drawing_number = QLineEdit()
        self.readout_length = QDoubleSpinBox()
        self.readout_length.setRange(.25, 1000); self.readout_length.setDecimals(2)
        self.readout_length.setSuffix(" 格")
        self.readout_direction = QComboBox()
        self.readout_direction.addItem("横向", "horizontal"); self.readout_direction.addItem("纵向", "vertical")
        self.readout_direction.addItem("自定义角度", "angled")
        self.readout_angle = QDoubleSpinBox()
        self.readout_angle.setRange(-180, 180); self.readout_angle.setDecimals(2)
        self.readout_angle.setSuffix(" °")
        for caption, widget in (("名称", self.label), ("类型", self.subtype),
                                ("角色", self.role), ("格点", self.coordinates), ("备注", self.notes)):
            field_label = QLabel(caption)
            field_label.setProperty("role", "muted")
            self.form.addRow(field_label, widget)
        self.form.addRow("图纸编号", self.drawing_number)
        self.form.addRow("线长", self.readout_length)
        self.form.addRow("方向", self.readout_direction)
        self.form.addRow("角度", self.readout_angle)
        layout.addLayout(self.form)
        self.dirty_label = QLabel()
        self.dirty_label.setProperty("role", "muted")
        self.error_label = QLabel()
        self.error_label.setProperty("role", "danger")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.dirty_label)
        layout.addWidget(self.error_label)
        self.label.textChanged.connect(lambda value: self._edit("label", value))
        self.notes.textChanged.connect(lambda value: self._edit("notes", value))
        self.drawing_number.textChanged.connect(lambda value: self._edit("drawing_number", value))
        self.subtype.currentTextChanged.connect(lambda _text: self._edit("subtype", combo_value(self.subtype)))
        self.role.currentTextChanged.connect(lambda _text: self._edit("role", combo_value(self.role)))
        self.x.valueChanged.connect(lambda value: self._edit("x", value))
        self.y.valueChanged.connect(lambda value: self._edit("y", value))
        self.readout_length.valueChanged.connect(lambda value: self._edit("readout_length", value))
        self.readout_direction.currentIndexChanged.connect(lambda _: self._edit("readout_direction", self.readout_direction.currentData()))
        self.readout_angle.valueChanged.connect(lambda value: self._edit("readout_angle", value))
        self.registry.changed.connect(self._draft_changed)
        self.clear()

    def bind(self, kind: str, ident: str) -> None:
        key = _key(kind, ident)
        state = self.registry.ensure(*key)
        self.bound = key
        self.setEnabled(True)
        scope = state["scope"]
        if scope != self._scope:
            self._scope = scope
            with _blocked(self.subtype):
                self.subtype.setEditable(scope == "custom")
                self.subtype.clear()
                for caption, value in SUBTYPES.get(scope, ()):
                    self.subtype.addItem(caption, value)
        for widget in (self.label, self.subtype, self.role, self.coordinates):
            self.form.setRowVisible(widget, key[0] == "device")
        self.form.setRowVisible(self.role, scope in ("qubit", "coupler"))
        self.form.setRowVisible(self.drawing_number, scope == "qubit")
        for widget in (self.readout_length, self.readout_direction, self.readout_angle):
            self.form.setRowVisible(widget, scope == "readout")
        self._sync(state)

    def clear(self) -> None:
        """Unbind this view without discarding the shared draft."""
        self.bound = None
        self._scope = None
        _set_line(self.label, "")
        _set_line(self.notes, "")
        _set_line(self.drawing_number, "")
        with _blocked(self.subtype):
            self.subtype.clear()
        _set_combo(self.role, "")
        for spin in (self.x, self.y):
            with _blocked(spin):
                spin.setValue(0)
        self.dirty_label.clear()
        self.error_label.clear()
        self.error_label.hide()
        for widget in (self.readout_length, self.readout_direction, self.readout_angle):
            self.form.setRowVisible(widget, False)
        self.form.setRowVisible(self.drawing_number, False)
        self.setEnabled(False)

    def _edit(self, field: str, value: object) -> None:
        if self.bound is not None:
            self.registry.update(*self.bound, field, value)

    def _draft_changed(self, kind: str, ident: str) -> None:
        if self.bound == (kind, ident):
            state = self.registry._drafts.get(self.bound)
            if state is None:
                self.clear()
            else:
                self._sync(state)

    def _sync(self, state: dict) -> None:
        values = state["values"]
        if self.bound and self.bound[0] == "device":
            _set_line(self.label, str(values["label"]))
            _set_combo(self.subtype, str(values["subtype"]))
            _set_combo(self.role, str(values["role"]))
            _set_line(self.drawing_number, str(values.get("drawing_number", "")))
            if "readout_length" in values:
                with _blocked(self.readout_length):
                    self.readout_length.setValue(values["readout_length"])
                _set_combo(self.readout_direction, values["readout_direction"])
                angle = values["readout_angle"]
                with _blocked(self.readout_angle):
                    self.readout_angle.setValue(angle if angle is not None else 90 if values["readout_direction"] == "vertical" else 0)
                self.form.setRowVisible(self.readout_angle, values["readout_direction"] == "angled")
            for field, spin in (("x", self.x), ("y", self.y)):
                try:
                    value = float(values[field])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and spin.value() != value:
                    with _blocked(spin):
                        spin.setValue(value)
        _set_line(self.notes, str(values["notes"]))
        self.dirty_label.setText("有未保存的更改" if self.bound and self.registry.dirty(*self.bound) else "已保存")
        self.error_label.setText(f"保存失败：{state['error']}" if state["error"] else "")
        self.error_label.setVisible(bool(state["error"]))

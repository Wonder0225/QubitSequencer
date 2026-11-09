"""Calibration defaults preserve existing data and explicit measurement context."""

import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from qubit_sequencer import gui
from qubit_sequencer.attachment_store import AttachmentInput
from qubit_sequencer.energy_levels import qubit_levels
from qubit_sequencer.exports import build_tables
from qubit_sequencer.parameter_rules import (
    BIASED_FREQUENCY, CALIBRATION_PRESETS, COUPLED_CAVITY_FREQUENCY,
    PI_AMPLITUDE, PI_LENGTH, PI_PRODUCT, VERTEX_FREQUENCY,
)
from qubit_sequencer.storage import ChipStore, SCHEMA_VERSION
from qubit_sequencer.undo import EditHistory


def test_existing_v7_gets_presets_without_overwriting_custom_definitions(tmp_path):
    path = tmp_path / "old.qchip"
    with ChipStore.create(path, "旧项目") as store:
        ident = store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
        record = store.set_parameter(ident, "qubit.f01", 4.7, bias_value=0, source="已有测量")
        with store.db:
            store.db.executemany("DELETE FROM parameter_definitions WHERE key=?",
                                 [(preset[0],) for preset in CALIBRATION_PRESETS])
        store.add_definition(VERTEX_FREQUENCY, "实验室自定义", "qubit", unit="MHz")
        store.set_parameter(ident, VERTEX_FREQUENCY, 4700)
        original = [tuple(row) for row in store.records()]
    with ChipStore.open(path) as store:
        assert store.definition(PI_LENGTH)["unit"] == "ns"
        assert store.definition(VERTEX_FREQUENCY)["unit"] == "MHz"
        assert store.definition(VERTEX_FREQUENCY)["label"] == "实验室自定义"
        assert [tuple(row) for row in store.records()] == original
        assert store.record(record)["source"] == "已有测量"
        assert qubit_levels(store, frequency_key="auto")[0].f10 == 4.7
        assert store.validate() == []
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_bias_z_zero_history_and_product_in_overview(tmp_path):
    path = tmp_path / "rules.qchip"
    with ChipStore.create(path, "标定规则") as store:
        ident = store.add_device("qubit", "tunable_transmon", "Q1", 0, 0)
        history = EditHistory(); history.bind(store.db); store.undo_history = history
        for key in (BIASED_FREQUENCY, COUPLED_CAVITY_FREQUENCY):
            with pytest.raises(ValueError, match="BiasZ"):
                store.set_parameter(ident, key, 5.1, point="idle")
        assert not store.records() and not history.steps
        freq = store.set_parameter(ident, BIASED_FREQUENCY, 4.564, point="idle", bias_value=0)
        store.set_parameter(ident, COUPLED_CAVITY_FREQUENCY, 6.12, point="idle", bias_value=-.025, bias_unit="V")
        store.set_parameter(ident, VERTEX_FREQUENCY, 4.572, point="idle")
        store.set_parameter(ident, "qubit.cavity_name", "腔 A", point="idle")
        store.set_parameter(ident, "qubit.anharmonicity", -200, point="idle", bias_value=.1)
        assert qubit_levels(store, "idle", "auto")[0].f21 is None
        store.set_parameter(ident, "qubit.anharmonicity", -200, point="idle", bias_value=0)
        assert qubit_levels(store, "idle", "auto")[0].f21 == pytest.approx(4.364)
        assert qubit_levels(store, "idle", VERTEX_FREQUENCY)[0].f10 == 4.572
        store.set_parameter(ident, PI_LENGTH, 60, point="idle", bias_value=0, source="脉冲标定 A")
        store.set_parameter(ident, PI_AMPLITUDE, .18551, point="idle", bias_value=0, source="脉冲标定 B")
        headers, rows = build_tables(store, "idle")["比特"]
        product = next(i for i, name in enumerate(headers) if name.endswith(f"({PI_PRODUCT})"))
        assert rows[0][product] is None
        store.set_parameter(ident, PI_AMPLITUDE, .18551, point="idle", bias_value=0, source="脉冲标定 A")
        headers, rows = build_tables(store, "idle", include_history=True)["比特"]
        assert rows[0][product] == pytest.approx(11.1306)
        assert rows[0][headers.index("偏置频率 BiasZ")] == "0.0"
        assert rows[0][headers.index("耦合腔频 BiasZ")] == "-0.025 V"
        assert store.record(freq)["bias_value"] == 0
        assert not any(record["definition_key"] == PI_PRODUCT for record in store.records())
        history.undo()
        assert build_tables(store, "idle")["比特"][1][0][product] is None
        history.redo(); history.detach()
    with ChipStore.open(path) as store:
        assert build_tables(store, "idle")["比特"][1][0][product] == pytest.approx(11.1306)
        assert store.record(freq)["bias_value"] == 0


def test_legacy_generic_t2_becomes_ramsey_without_losing_records(tmp_path):
    path = tmp_path / "legacy-t2.qchip"
    with ChipStore.create(path, "旧 T2") as store:
        ident = store.add_device("qubit", "fixed_transmon", None, 0, 0)
        with store.db:
            store.db.execute("INSERT INTO parameter_definitions VALUES ('qubit.t2','T2','qubit','measured','number','µs',0)")
            store.db.execute("UPDATE parameter_definitions SET label='Echo T_{2}' WHERE key='qubit.t2_echo'")
        store.set_parameter(ident, "qubit.t2_star", 13)
        store.set_parameter(ident, "qubit.t2_echo", 21)
        older = store.set_parameter(ident, "qubit.t2", 6, point="idle")
        idle = store.set_parameter(ident, "qubit.t2", 7, point="idle", notes="旧记录",
                                   attachments=(AttachmentInput("fit.txt", b"fit"),))
        collision = store.set_parameter(ident, "qubit.t2", 8)
    for _ in range(2):
        with ChipStore.open(path) as store:
            assert "qubit.t2" not in {d["key"] for d in store.definitions("qubit")}
            assert store.definition("qubit.t2_echo")["label"] == "Spin Echo T_{2}"
            assert store.current_values()[(ident, "qubit.t2_star")] == 13
            assert store.current_values()[(ident, "qubit.t2_echo")] == 21
            assert store.current_values("idle")[(ident, "qubit.t2_star")] == 7
            for record_id, is_current in ((older, 0), (idle, 1), (collision, 0)):
                record = store.record(record_id)
                assert (record["definition_key"], record["is_current"]) == ("qubit.t2_star", is_current)
            assert store.record(idle)["notes"] == "旧记录"
            assert store.parameter_attachments(idle)[0]["file_name"] == "fit.txt"
            assert store.validate() == []


def test_primary_parameters_default_and_bias_dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "gui.qchip"
    with ChipStore.create(path, "参数界面") as store:
        ident = store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
        store.set_parameter(ident, "qubit.f01", 4.7)
        store.set_parameter(ident, BIASED_FREQUENCY, 4.6, bias_value=0)
        store.set_parameter(ident, PI_LENGTH, 60)
        store.set_parameter(ident, PI_AMPLITUDE, .18551)
    window = gui.MainWindow(str(path)); window.show(); app.processEvents()
    try:
        window.selected = ("device", ident); window.refresh_detail()
        old = next(i for i, definition in enumerate(window.visible_definitions) if definition["key"] == "qubit.f01")
        new = next(i for i, definition in enumerate(window.visible_definitions) if definition["key"] == BIASED_FREQUENCY)
        assert window.parameter_table.isRowHidden(old) and not window.parameter_table.isRowHidden(new)
        assert "BiasZ 0.0" in window.parameter_table.item(new, 1).toolTip()
        window.device_popup.device_id = ident
        assert window.device_popup.reload() and window.device_popup.parameters.isRowHidden(old)
        window.secondary_parameters.setChecked(True)
        assert not window.parameter_table.isRowHidden(old) and not window.device_popup.parameters.isRowHidden(old)
        legacy = window._parameter_visibility_actions["qubit.f01"]
        assert not legacy.isChecked()
        legacy.setChecked(True)
        assert window._is_table_parameter_visible("qubit.f01")
        for key in ("qubit.t2_star", "qubit.t2_echo", PI_PRODUCT):
            assert window._parameter_visibility_actions[key].isChecked()
        table = window.data_table
        assert "备注" not in {table.item(r, 0).text() for r in range(table.rowCount())}
        area_row = next(r for r, key in window._table_parameter_positions.items() if key == PI_PRODUCT)
        assert "π 脉冲面积" in table.item(area_row, 0).text()
        assert table.item(area_row, 1).data(Qt.ItemDataRole.UserRole) == pytest.approx(11.1306)
        orientation = window.table_orientation
        orientation.setCurrentIndex(orientation.findData("horizontal"))
        assert "备注" not in {table.horizontalHeaderItem(c).toolTip() for c in range(table.columnCount())}
        orientation.setCurrentIndex(orientation.findData("vertical"))
        bias_rows = [i for i, key in window._table_parameter_positions.items() if key == BIASED_FREQUENCY]
        assert len(bias_rows) == 2 and not any(window.data_table.isRowHidden(i) for i in bias_rows)
        window._parameter_visibility_actions[BIASED_FREQUENCY].setChecked(False)
        assert all(window.data_table.isRowHidden(i) for i in bias_rows)
        dialog = gui.ParameterDialog(window.store.definition(BIASED_FREQUENCY), "default", window)
        dialog.value.setText("4.6"); dialog.accept()
        assert "BiasZ" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted
        dialog.bias.setText("0"); dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        window.energy_button.click()
        assert window.energy_panel.rows[0].f10 == 4.6
        combo = window.energy_panel.frequency_source
        combo.setCurrentIndex(combo.findData("qubit.f01"))
        assert window.energy_panel.rows[0].f10 == 4.7
    finally:
        window.close(); app.processEvents()

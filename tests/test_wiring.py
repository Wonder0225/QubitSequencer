"""Cryostat registration checks use simulated data and isolated chip files."""

import csv
import sqlite3
from copy import deepcopy

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from qubit_sequencer import gui
from qubit_sequencer.exports import build_tables, export_csv, export_xlsx
from qubit_sequencer.storage import ChipStore, SCHEMA_VERSION
from qubit_sequencer.undo import EditHistory


def registration():
    return {
        "board": "TEST", "instrument": "DEMO generator", "cable": "DEMO-blue",
        "sample_pin": "S-DEMO", "drawing_label": "R-DEMO",
        "components": [
            {"stage": "3 K", "kind": "衰减器", "value": 0, "unit": "dB"},
            {"stage": "MC", "kind": "衰减器", "value": 15, "unit": "dB"},
            {"stage": "MC", "kind": "低通滤波器", "name": "DEMO-LP", "value": 420, "unit": "MHz"},
            {"stage": "MC", "kind": "红外滤波器", "name": "DEMO-IR", "value": None},
            {"stage": "MC", "kind": "微波开关", "name": "DEMO-SW", "terminal": "2"},
        ],
        "amplifiers": [{"stage": "3 K", "model": "DEMO-AMP", "serial": "DEMO-01",
                        "band": "3–9", "pins": "GND=P1；Vd=P2；Vg=P3"}],
        "biases": [{"amplifier": "DEMO-01", "temperature": 280, "vd": 1.1, "id": 25, "vg": -.5},
                   {"amplifier": "DEMO-01", "temperature": 8.5, "vd": .7, "id": 11, "vg": .2}],
        "checks": [{"time": "2026-10-07 10:00 +08:00", "sample_state": "装样前", "power": -11,
                    "band": "3–7", "transmission": -48, "result": "合格", "notes": "DEMO 检线"},
                   {"time": "2026-10-07 11:00 +08:00", "sample_state": "带样品", "power": 0,
                    "transmission": None, "result": "待复核", "notes": "DEMO 仅确认放大器工作"}],
    }


def test_roundtrip_associations_and_exports(tmp_path):
    path = tmp_path / "chip.qchip"
    with ChipStore.create(path, "模拟接线") as store:
        qubits = [store.add_device("qubit", "fixed_transmon", f"Q{i}", i, 0) for i in range(2)]
        line = store.add_line("读取输出", "DEMO-DR", "T1", devices=qubits, details=registration())
        before = store.line_details(line)
        store.backup_to(tmp_path / "backup.qchip")
        export_xlsx(store, tmp_path / "wiring.xlsx")
        paths = export_csv(store, tmp_path / "csv")
        assert len(paths) == 9
        headers, rows = build_tables(store)["接线"]
        assert len(rows) == 2 and {r[5] for r in rows} == {"Q0", "Q1"}
        configs = rows[0][headers.index("温级元件配置")]
        assert "数值: 0" in configs and "单位: MHz" in configs and "DEMO-SW" in configs
        assert "温度 [K]: 8.5" in rows[0][headers.index("放大器工作参数")]
        assert "DEMO 仅确认放大器工作" in rows[0][headers.index("检线记录")]
        book = load_workbook(tmp_path / "wiring.xlsx", read_only=True)
        assert book["接线"].cell(2, headers.index("温级元件配置")+1).value == configs
        book.close()
        with (tmp_path / "csv/wiring.csv").open(encoding="utf-8-sig", newline="") as f:
            csv_rows = list(csv.reader(f))
        assert csv_rows[1][headers.index("检线记录")] == rows[0][headers.index("检线记录")]
    for copy in [path, tmp_path / "backup.qchip"]:
        with ChipStore.open(copy) as store:
            assert store.line_details(line) == before
            assert set(store.line_devices(line)) == set(qubits)
            assert store.validate() == []


def test_atomic_failure_and_legacy_api_preserves_details(tmp_path):
    with ChipStore.create(tmp_path / "chip.qchip", "模拟") as store:
        line = store.add_line("Z", port="before", details=registration())
        before = store.line_details(line)
        invalid = deepcopy(before); invalid["biases"][0]["vd"] = "not a number"
        with pytest.raises(ValueError):
            store.update_line(line, "XY", port="changed", details=invalid)
        assert store.line(line)["port"] == "before" and store.line_details(line) == before
        invalid["biases"][0]["vd"] = float("nan")
        with pytest.raises(ValueError):
            store.add_line("XY", details=invalid)
        assert len(store.lines()) == 1
        store.update_line(line, "XY", port="new")
        assert store.line_details(line) == before


def test_one_step_save_delete_undo_redo(tmp_path):
    with ChipStore.create(tmp_path / "chip.qchip", "模拟") as store:
        history = EditHistory(); history.bind(store.db); store.undo_history = history
        line = store.add_line("XY/Z", port="TEST1", details=registration())
        original = store.line_details(line)
        assert len(history.steps) == 1
        history.undo(); assert store.lines() == []
        history.redo(); assert store.line_details(line) == original
        changed = deepcopy(original); changed["components"].append({"stage": "室温", "kind": "合路器", "name": "DEMO-COMB"})
        store.update_line(line, "XY/Z", port="TEST2", details=changed)
        history.undo(); assert store.line(line)["port"] == "TEST1" and store.line_details(line) == original
        history.redo(); assert store.line(line)["port"] == "TEST2" and store.line_details(line)["components"][-1]["kind"] == "合路器"
        store.delete_line(line); assert store.db.execute("SELECT COUNT(*) FROM wiring_details").fetchone()[0] == 0
        history.undo(); assert store.line_details(line)["components"][-1]["kind"] == "合路器"
        history.redo(); assert store.lines() == []


def test_v4_upgrade_preserves_old_line_and_backup(tmp_path):
    path = tmp_path / "old.qchip"
    with ChipStore.create(path, "旧数据") as store:
        q = store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
        line = store.add_line("读出", "OLD-DR", "A1", "OLD channel", "旧备注", [q])
        record = store.set_parameter(q, "qubit.f01", 4.7)
        with store.db:
            store.db.execute("DROP TABLE readout_links")
            store.db.execute("DROP TABLE wiring_details")
            store.db.execute("PRAGMA user_version=4")
    with ChipStore.open(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.line_details(line) == {}
        assert store.line(line)["notes"] == "旧备注" and store.line_devices(line) == [q]
        assert store.record(record)["numeric_value"] == 4.7
    with sqlite3.connect(tmp_path / "old.pre-v4.qchip") as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 4
        assert backup.execute("SELECT notes FROM wiring_lines").fetchone()[0] == "旧备注"


def test_editor_tables_validation_undo_and_main_filters(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "ui.qchip"
    with ChipStore.create(path, "模拟") as store:
        line = store.add_line("XY/Z", port="TEST1", details=registration())
        store.add_line("Z", port="OTHER")
    window = gui.MainWindow(str(path)); window.show()
    try:
        dialog = gui.LineDialog(window, window.store.line(line), window.store.line_details(line))
        dialog.show(); dialog.tabs.setCurrentIndex(1); app.processEvents()
        assert dialog.components.rowCount() == 5
        dialog.components.item(1, 3).setText("17")
        QTest.keyClick(dialog.components, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert dialog.components.item(1, 3).text() == "15"
        QTest.keyClick(dialog.components, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        assert dialog.components.item(1, 3).text() == "17"
        dialog.components.setCurrentCell(4, 0); dialog.components.move(-1)
        QTest.keyClick(dialog.components, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert dialog.components.item(4, 1).text() == "微波开关"
        dialog.components.item(1, 3).setText("invalid")
        dialog.accept(); assert dialog.result() != QDialog.DialogCode.Accepted
        assert "数值" in dialog.error.text()
        dialog.components.item(1, 3).setText("17")
        assert dialog.details()["components"][1]["value"] == 17
        dialog.reject()
        window.tabs.setCurrentIndex(2); window.wiring_search.setText("DEMO-SW")
        visible = [r for r in range(window.wiring_table.rowCount()) if not window.wiring_table.isRowHidden(r)]
        assert len(visible) == 1
        window.wiring_table.selectRow(visible[0]); app.processEvents()
        assert "Vd [V]" in window.wiring_detail.text() and "DEMO 仅确认放大器工作" in window.wiring_detail.text()
        window.wiring_usage.setCurrentText("Z")
        assert not window._chosen_line_id()
        assert not window.wiring_buttons["编辑线路"].isEnabled()
    finally:
        window.drafts.clear(); window.close(); app.processEvents()

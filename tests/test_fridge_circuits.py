import csv
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsPathItem, QInputDialog, QMessageBox, QTableWidget

from qubit_sequencer.fridge_circuit_ui import CircuitDialog, ComponentDialog, PresetDialog
from qubit_sequencer.fridge_components import BIAS_TEE_KIND, SWITCH_KIND, component_text, terminal_branches
from qubit_sequencer.fridge_presets import preset_for
from qubit_sequencer.fridge_store import FridgeStore
from qubit_sequencer.fridge_ui import AssignmentDialog, FridgePanel
from qubit_sequencer.exports import build_tables
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.undo import EditHistory


def port(store, board, number):
    return next(p for p in store.ports() if p['board'] == board and p['number'] == number)


def sample(path, name):
    with ChipStore.create(path, name) as chip:
        return chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)


def test_switch_control_wires_are_independent_of_rf_routes(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'control.qfridge'
    with closing(FridgeStore.create(path, 'DR', with_preset=True)) as store:
        target = port(store, 'F', 'O4')['id']
        dialog = CircuitDialog(store, store.port(target))

        def configure(editor):
            editor.has_reset.setChecked(True)
            assert list(editor.wire_editors) == ['C', '1', '2', '3', '4', '5', '6', 'Reset']
            for key, wire in (('C', 'W01'), ('1', 'W02'), ('6', 'W07'), ('Reset', 'W08')):
                editor.wire_editors[key].setText(wire)
            editor.accept()
            return editor.result()

        monkeypatch.setattr(ComponentDialog, 'exec', configure)
        dialog.configure_switch()
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted

    with closing(FridgeStore.open(path)) as store:
        data = store.circuit(target)
        switch = data['switch_component']
        assert switch['has_reset'] is True
        assert switch['control_wires'] == {'C': 'W01', '1': 'W02', '6': 'W07', 'Reset': 'W08'}
        assert terminal_branches(data) == ['1', '2', '3', '4', '5', '6']
        assert 'Reset=W08' in component_text(switch)
        editor = ComponentDialog(None, switch, branches=data['switch'])
        assert editor.wire_editors['Reset'].text() == 'W08'
        editor.has_reset.setChecked(False)
        editor.accept()
        assert 'has_reset' not in editor.result_component and 'Reset' not in editor.result_component['control_wires']
        assert editor.result_component['control_wires']['C'] == 'W01'

    merge = ComponentDialog(None, {'kind': SWITCH_KIND}, merge=(['left', 'right'], ['left', 'right'], 'out'))
    assert [merge.control_wires.item(row, 0).text() for row in range(3)] == ['C · 公共端', '1 · 分支 left', '2 · 分支 right']
    merge.has_reset.setChecked(True)
    merge.wire_editors['Reset'].setText('M-R')
    merge.accept()
    assert merge.merge_inputs() == ['left', 'right'] and merge.result_component['control_wires'] == {'Reset': 'M-R'}


def test_drag_swaps_components_only_within_same_stage(tmp_path):
    app = QApplication.instance() or QApplication([])
    with closing(FridgeStore.create(tmp_path / 'drag.qfridge', 'DR')) as store:
        target = port(store, 'A', '13')['id']
        editor = ComponentDialog(None, {'kind': 'HEMT', 'stage': 'MC', 'model': 'A'})
        editor.wire_number.setText('AMP-01')
        editor.accept()
        rows = [editor.result_component, {'kind': '滤波器', 'stage': '4 K', 'model': 'B'},
                {'kind': '衰减器', 'stage': 'MC', 'model': 'C'}]
        store.save_circuit(target, {'components': rows, 'switch': []})
        dialog = CircuitDialog(store, store.port(target))
        dialog.show()
        app.processEvents()

        def point(index):
            key = (target, None, index)
            item = next(item for item in dialog.view.scene().items() if item.data(0) == key)
            return dialog.view.mapFromScene(item.sceneBoundingRect().center())

        def drag(source, destination):
            viewport = dialog.view.viewport()
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point(source))
            QTest.mouseMove(viewport, point(destination))
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=point(destination))

        drag(0, 1)
        assert [row['model'] for row in dialog.rows(None)] == ['A', 'B', 'C']
        drag(0, 2)
        assert [row['model'] for row in dialog.rows(None)] == ['C', 'B', 'A']
        dialog.accept()
        assert store.circuit(target)['components'][2]['wire_number'] == 'AMP-01'


def test_bias_tee_follows_control_port_assignment(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    chip_path = tmp_path / "chip.qchip"
    with ChipStore.create(chip_path, "Bias sample") as chip:
        chip.enable_sample_box()
        box_id = chip.sample_box_ports()[0]["id"]

    project = tmp_path / "bias.qfridge"
    with closing(FridgeStore.create(project, "DR")) as store:
        rf = port(store, "A", "13")["id"]
        dc = port(store, "A", "42")["id"]
        sample_id = store.add_sample(chip_path)
        dialog = CircuitDialog(store, store.port(rf))
        monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (f"{BIAS_TEE_KIND}（内置类型）", True))

        def configure(editor):
            rf_selector = editor.input_ports["rf_input_port_id"]
            dc_selector = editor.input_ports["dc_input_port_id"]
            assert rf_selector.findData(dc) == dc_selector.findData(rf) == -1
            rf_selector.setCurrentIndex(rf_selector.findData(rf))
            dc_selector.setCurrentIndex(dc_selector.findData(dc))
            editor.stage.setCurrentText("MC")
            editor.accept()
            return editor.result()

        monkeypatch.setattr(ComponentDialog, "exec", configure)
        dialog.add_component()
        scene = dialog.view.scene()
        captions = {item.toolTip() for item in scene.items()}
        assert {"RF A13", "DC A42"} <= captions
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert not store.bias_tee_assignments()

    with closing(FridgeStore.open(project)) as store:
        component = store.circuit(rf)["components"][0]
        assert (component["rf_input_port_id"], component["dc_input_port_id"]) == (rf, dc)
        box = next(p for p in store.sample_box_ports(sample_id) if p["id"] == box_id)
        store.quick_assign(sample_id, chip_path, [(box, (rf, None), ("A", "13", "XY/Z"))])
        assert [(r["id"], r["number"]) for r in store.box_connections(sample_id)] == [(rf, "13")]
        assert store.bias_tee_assignments() == [(rf, dc, None, "Bias sample")]
        dc_dialog = CircuitDialog(store, store.port(dc))
        assert "来自 RF A13" in dc_dialog.lanes(store.port(dc))[0].endpoint[0]
        dc_dialog.close()
        store.unassign(rf)
        assert not store.bias_tee_assignments()

        store.save_circuit(rf, {"components": [], "switch": ["1", "2"], "branch_components": {"1": [component]}})
        store.assign(rf, sample_id, branch="2")
        assert not store.bias_tee_assignments()
        store.assign(rf, sample_id, branch="1", box_port_id=box_id)
        assert store.bias_tee_assignments() == [(rf, dc, "1", "Bias sample")]
        original = store.port(dc)
        store.save_port(dc, original["board"], "DC-test", original["x"], original["y"], original["kind"])
        reopened = CircuitDialog(store, store.port(rf))
        assert "DC ADC-test" in {item.toolTip() for item in reopened.view.scene().items()}
        reopened.close()


def test_v3_upgrade_and_branch_circuits_survive_exports(tmp_path):
    chip = tmp_path / "sample.qchip"
    sample(chip, "样品")
    path = tmp_path / "cryostat.qfridge"
    store = FridgeStore.create(path, "DR", with_preset=True)
    target = port(store, "F", "O4")["id"]
    sample_id = store.add_sample(chip)
    store.assign(target, sample_id, "R1", notes="原接线备注", branch="1")
    original = store.circuit(target)
    store.close()
    with closing(sqlite3.connect(path)) as db, db:
        for table in ("attachment_versions", "record_attachments", "attachment_blobs"):
            db.execute(f"DROP TABLE {table}")
        db.execute("DROP TABLE component_library")
        db.execute("PRAGMA user_version=3")

    store = FridgeStore.open(path)
    backup = tmp_path / "cryostat.pre-v3.qfridge"
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert store.circuit(target) == original
    assert store.branch_assignments(target)[0]["notes"] == "原接线备注"
    history = EditHistory()
    history.bind(store.db)
    store.undo_history = history
    entry = store.save_library_component(None, "实验室 HEMT", dict(kind="HEMT", value=38, unit="dB"))
    component = dict(store.component_library()[0][2], stage="3 K", model="HEMT-A", frequency="4–8 GHz")
    enhanced = dict(original, switch_component=dict(kind="微波开关", stage="MC", model="SW-6"),
                    branch_components={"2": [component]})
    store.save_circuit(target, enhanced)
    history.undo()
    assert store.circuit(target) == original
    history.redo()
    assert store.circuit(target) == enhanced
    store.save_library_component(entry, "已调整的 HEMT", dict(kind="HEMT", value=42))
    store.delete_library_component(entry)
    assert store.circuit(target)["branch_components"]["2"][0]["value"] == 38
    store.save_library_component(None, "备选衰减器", dict(kind="衰减器", value=20))
    without_two = dict(enhanced, switch=["1", "3", "4", "5", "6"], branch_components={})
    with pytest.raises(ValueError, match="先确认"):
        store.save_circuit(target, without_two)
    store.save_circuit(target, without_two, discard_branches={"2"})
    history.undo()
    assert store.circuit(target) == enhanced

    store.export_table(tmp_path / "wiring.csv")
    store.export_table(tmp_path / "wiring.xlsx")
    with (tmp_path / "wiring.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.reader(stream))
    workbook = load_workbook(tmp_path / "wiring.xlsx", read_only=True)
    for rows in (csv_rows, list(workbook.active.values)):
        column = rows[0].index("低温线路")
        routes = {row[3]: row[column] for row in rows[1:] if tuple(row[:2]) == ("F", "O4")}
        assert "共用段" in routes["1"] and "SW-6" in routes["1"] and "HEMT-A" not in routes["1"]
        assert "分支 2" in routes["2"] and "HEMT-A" in routes["2"]
    workbook.close()
    archive = tmp_path / "project.zip"
    store.export_bundle(archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(tmp_path / "unpacked")
    clone = FridgeStore.open(tmp_path / "unpacked" / "project.qfridge")
    assert clone.circuit(target) == enhanced
    assert clone.component_library() == store.component_library()
    assert clone.sample_path(sample_id).is_file()
    clone.close()
    history.detach()
    store.close()


def test_diagram_single_edit_and_read_only_comparison(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / "diagram.qfridge", "DR", with_preset=True)
    store.create_board("G", "empty_square")
    target = store.save_port(None, "G", "1", 0, 0, "XY/Z")
    second = store.save_port(None, "G", "2", 1, 0, "XY/Z")
    store.save_library_component(None, "放大模板", dict(kind="HEMT", value=35, unit="dB"))
    a1, f4 = port(store, "A", "1"), port(store, "F", "O4")
    single = CircuitDialog(store, a1)
    assert sum(item.data(0) in [(a1["id"], None, 3), (a1["id"], None, 4)]
               for item in single.view.scene().items()) == 2
    single.close()

    def edit_values(dialog):
        dialog.stage.setCurrentText("3 K")
        dialog.fields["value"].setText(str(38 if dialog.parent().port["id"] == target else 41))
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("放大模板（项目条目 1）", True))
    monkeypatch.setattr(ComponentDialog, "exec", edit_values)
    for port_id in (target, second):
        editor = CircuitDialog(store, store.port(port_id))
        editor.add_component()
        assert "HEMT" in editor.details.toPlainText() and editor.view.visible_stages == ("3 K",)
        assert store.circuit(port_id)["components"] == []
        editor.accept()
    assert [store.circuit(i)["components"][0]["value"] for i in (target, second)] == [38, 41]
    assert store.component_library()[0][2]["value"] == 35
    switch_editor = CircuitDialog(store, f4, initial_branch="2")
    switch_editor.add_component()
    assert "分支 2" in switch_editor.details.toPlainText()
    switch_editor.accept()
    assert list(store.circuit(f4["id"])["branch_components"]) == ["2"]
    store.set_ports_status([second], "damaged")
    locked = CircuitDialog(store, store.port(second))
    assert not locked.editable and locked.view.visible_stages == ("3 K",)
    locked.close()

    panel = FridgePanel(store)
    panel.show()
    panel.open_board("G")
    app.processEvents()
    for item in panel.scene.items():
        if hasattr(item, "port"):
            item.setSelected(True)
    assert panel.circuit_button.isEnabled()
    observed = []
    monkeypatch.setattr(CircuitDialog, "exec", lambda dialog: observed.append(dialog) or QDialog.DialogCode.Rejected)
    panel.circuit_button.click()
    assert len(observed[0].ports) == 2 and not observed[0].editable

    panel.overview_button.click()
    app.processEvents()
    panel.scene.clearSelection()
    assert panel.overview_circuit_button.isVisible() and not panel.overview_circuit_button.isEnabled()
    for port_id, modifier in ((a1["id"], Qt.KeyboardModifier.NoModifier),
                              (f4["id"], Qt.KeyboardModifier.ControlModifier)):
        item = next(item for item in panel.scene.items()
                    if hasattr(item, "port") and item.port["id"] == port_id)
        panel.graphics.ensureVisible(item)
        QTest.mouseClick(panel.graphics.viewport(), Qt.MouseButton.LeftButton, modifier,
                         pos=panel.graphics.mapFromScene(item.scenePos()))
        assert panel.overview_circuit_button.isEnabled()
    panel.overview_circuit_button.click()
    assert [p["id"] for p in observed[1].ports] == [a1["id"], f4["id"]]
    assert not observed[1].editable and not panel.inspector.isVisible()

    compare = CircuitDialog(store, a1, others=[f4, port(store, "A", "42")])
    compare.show()
    app.processEvents()
    assert compare.view.visible_stages == ("3 K", "STILL", "50 mK", "MC", "")
    assert not hasattr(compare, "edit_button")
    toggle = next(item for item in compare.view.scene().items() if item.data(0) == (f4["id"], None, "toggle"))
    QTest.mouseClick(compare.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=compare.view.mapFromScene(toggle.sceneBoundingRect().center()))
    assert f4["id"] in compare.expanded
    assert any(item.text() == "分支 6" for item in compare.view.scene().items() if hasattr(item, "text"))
    compare.close()
    panel.close()
    app.processEvents()


def test_manual_wiring_type_check_reports_port_and_branch_mismatches(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'sample.qchip'
    sample(path, '测试样品')
    store = FridgeStore.create(tmp_path / 'check.qfridge', '制冷机')
    sid = store.add_sample(path)
    assert store.check_wiring_types() == (0, [])
    store.create_board('G', 'empty_square')
    z = store.save_port(None, 'G', '1', 0, 0, 'Z')
    mixed = store.save_port(None, 'G', '2', 1, 0, 'XY/Z')
    readout = store.save_port(None, 'G', '3', 2, 0, '读出')
    store.save_circuit(mixed, {'switch': ['1', '2', '3', '4']})
    store.assign(z, sid, usage='XY/Z')
    store.assign(mixed, sid, usage='XY/Z', branch='1')
    store.assign(mixed, sid, usage='Z', branch='2')
    store.assign(mixed, sid, usage='XY', branch='3')
    store.assign(readout, sid, usage='读取输出')

    panel = FridgePanel(store)
    try:
        panel.open_board('A')  # The check covers the entire project, even when G is hidden.
        shown = []

        def capture_report(dialog):
            table = dialog.findChild(QTableWidget)
            shown.extend(tuple(table.item(row, col).text() for col in range(table.columnCount()))
                         for row in range(table.rowCount()))
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QDialog, 'exec', capture_report)
        before = store.db.total_changes
        panel.check_wiring_button.click()
        assert shown == [('G', '1', '—', 'Z', 'XY/Z', '测试样品'), ('G', '2', '3', 'XY/Z', 'XY', '测试样品')]
        assert store.db.total_changes == before
        assert store.check_wiring_types()[0] == 5

        store.assign(z, sid, usage='Z')
        store.assign(mixed, sid, usage='Z', branch='3')
        messages = []
        monkeypatch.setattr(QMessageBox, 'information', lambda _parent, _title, text: messages.append(text))
        panel.check_wiring_button.click()
        assert messages == ['已检查 5 条分配，线路用途均与端口类型兼容。']
    finally:
        panel.close()
        app.processEvents()


def test_preset_transcription():
    for n in range(1, 13):
        mc = [x for x in preset_for('A', n)['components'] if x['stage'] == 'MC']
        assert [x['value'] for x in mc[:2]] == [20, 20]
        assert (mc[2]['kind'], mc[2]['mark']) == ('滤波器', 'IR')
    for n in (5, 25, 45):
        assert preset_for('C', n)['components'][1]['value'] == 0
    for n in range(57, 62):
        mc = [x['value'] for x in preset_for('C', n)['components'] if x['stage'] == 'MC' and x['value'] is not None]
        assert mc == ([0, 20, 10] if n < 61 else [0, 10])
    marker = next(x for x in preset_for('C', 37)['components'] if x['mark'] == '500M')
    assert marker['kind'] == '标记' and marker['value'] is None and marker['unit'] == ''
    for board, number, mark in [('A', 13, 'IR1'), ('A', 32, 'IR2'), ('B', 1, 'IR1'), ('C', 1, 'IR2'), ('F', 'O4', 'IR')]:
        row = next(x for x in preset_for(board, number)['components'] if x['mark'] == mark)
        assert row['kind'] == '滤波器'
    assert preset_for('F', 'O4')['switch'] == ['1', '2', '3', '4', '5', '6']
    assert preset_for('D', 'O1')['components'][-1]['mark'] == 'I1'
    for board, n in [('A', 42), ('B', 21), ('B', 26), ('B', 45), ('B', 46)]:
        assert preset_for(board, n) is None


def test_edit_occupied_switch_circuit_preserves_routes_and_classifies_legacy_ir(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    chip = tmp_path / "sample.qchip"
    sample(chip, "样品")
    store = FridgeStore.create(tmp_path / "switch.qfridge", "DR", with_preset=True)
    target = port(store, "F", "O4")["id"]
    sid = store.add_sample(chip)
    store.assign(target, sid, "R1", branch="1")
    store.set_branch_occupied(target, "2", True)
    store.set_occupied(target, True)
    original = store.circuit(target)
    legacy = dict(original, branch_components={"2": [dict(kind="IR2", stage="MC")]})
    for component in legacy["components"]:
        if component["mark"] == "IR":
            component["kind"] = "标记"
    with store.db:
        store.db.execute("UPDATE port_circuits SET circuit_json=? WHERE port_id=?", (json.dumps(legacy), target))
    editor = CircuitDialog(store, store.port(target), initial_branch="2")
    assert editor.editable and editor.route.currentData() == "2"
    editor.select_symbol((target, "2", 0))
    assert "滤波器 · IR2" in editor.details.toPlainText()

    def edit_switch(dialog):
        assert dialog.branches.isReadOnly()
        dialog.fields["model"].setText("SP6T")
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(ComponentDialog, "exec", edit_switch)
    editor.select_symbol((target, None, "switch"))
    assert not editor.delete_button.isEnabled()
    editor.edit_button.click()
    assert editor.draft["switch_component"]["model"] == "SP6T"
    editor.accept()
    assert editor.result() == QDialog.DialogCode.Accepted
    saved = json.loads(store.db.execute("SELECT circuit_json FROM port_circuits WHERE port_id=?", (target,)).fetchone()[0])
    assert ("滤波器", "IR") in {(row["kind"], row["mark"]) for row in saved["components"]}
    assert saved["branch_components"]["2"] == [dict(kind="滤波器", stage="MC", mark="IR2")]
    assert store.port(target)["manual_status"] == "occupied"
    assert store.branch_assignments(target)[0]["sample_id"] == sid and store.branch_marks(target) == {"2"}
    with pytest.raises(ValueError, match="分支编号"):
        store.save_circuit(target, dict(saved, switch=[*saved["switch"], "7"]))
    store.close()
    app.processEvents()


def test_switch_directions_keep_branch_components_and_reverse_diagram_arrows(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "switch.qfridge"
    store = FridgeStore.create(path, "DR", with_preset=True)
    target = port(store, "F", "O4")["id"]
    original = store.circuit(target)
    branch_components = {"2": [dict(kind="滤波器", stage="MC", mark="IR2"),
                               dict(kind="放大器", stage="3 K", model="LNA")]}
    store.save_circuit(target, dict(original, branch_components=branch_components))

    def arrow_paths(dialog):
        return [item.path() for item in dialog.view.scene().items()
                if isinstance(item, QGraphicsPathItem)
                and item.path().elementCount() == 3 and item.path().elementAt(0).x > 100]

    for direction, label in (("fan_out", "一路到多路"), ("fan_in", "多路到一路")):
        def configure(dialog):
            dialog.direction.setCurrentIndex(dialog.direction.findData(direction))
            dialog.stage.setCurrentText("MC")
            dialog.accept()
            return dialog.result()

        monkeypatch.setattr(ComponentDialog, "exec", configure)
        editor = CircuitDialog(store, store.port(target))
        if direction == "fan_out":
            assert not arrow_paths(editor)
        editor.configure_switch()
        arrows = arrow_paths(editor)
        assert len(arrows) == 7
        assert all((arrow.elementAt(1).x > arrow.elementAt(0).x) == (direction == "fan_out") for arrow in arrows)
        positions = {item.data(0): item.sceneBoundingRect() for item in editor.view.scene().items()
                     if item.data(0) is not None}
        switch = positions[(target, None, "switch")]
        assert positions[(target, "2", 0)].left() > switch.right()
        assert positions[(target, "2", 1)].left() > switch.right()
        assert label in editor.details.toPlainText()
        editor.accept()
        circuit = store.circuit(target)
        assert circuit["switch_component"]["direction"] == direction
        assert circuit["switch"] == original["switch"] and circuit["branch_components"] == branch_components
        header, entries = store.table_entries()
        route = next(values for pid, branch, values in entries if pid == target and branch == "2")
        assert label in route[header.index("低温线路")]
    store.close()
    with closing(FridgeStore.open(path)) as reopened:
        assert reopened.circuit(target)["switch_component"]["direction"] == "fan_in"
    app.processEvents()


def test_cascaded_switches_use_registered_stage_and_keep_branch_wiring(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    chip = tmp_path / "sample.qchip"
    sample(chip, "样品")
    path = tmp_path / "cascade.qfridge"
    store = FridgeStore.create(path, "DR", with_preset=True)
    sid = store.add_sample(chip)
    target = port(store, "F", "O4")["id"]
    original = store.circuit(target)
    original["switch_component"] = {"stage": "MC", "model": "SW-A"}
    original["branch_components"] = {"1": [dict(stage="MC", kind="滤波器", mark="原分支元件")]}
    store.save_circuit(target, original)
    store.assign(target, sid, "R1", branch="1", notes="保留接线")
    store.set_branch_occupied(target, "1", True)
    store.set_occupied_drawing(target, "1", "P-17")
    store.close()
    with closing(sqlite3.connect(path)) as db, db:
        for table in ("attachment_versions", "record_attachments", "attachment_blobs"):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=4")
    store = FridgeStore.open(path)
    assert (tmp_path / "cascade.pre-v4.qfridge").is_file()
    assert store.circuit(target) == original
    history = EditHistory()
    history.bind(store.db)
    store.undo_history = history
    editor = CircuitDialog(store, store.port(target), initial_branch="1")

    def configure(dialog):
        dialog.stage.setCurrentText("MC")
        dialog.fields["model"].setText("SW-B")
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(ComponentDialog, "exec", configure)
    editor.configure_switch()
    assert editor.route.findData("1/2") >= 0
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("1/2", True))
    editor.accept()
    assert editor.result() == QDialog.DialogCode.Accepted, editor.error.text()
    tree = store.circuit(target)
    assert terminal_branches(tree) == ["1/1", "1/2", "2", "3", "4", "5", "6"]
    assert tree["branch_components"]["1"] == original["branch_components"]["1"]
    assignment, = store.branch_assignments(target)
    assert (assignment["branch"], assignment["sample_id"], assignment["notes"]) == ("1/2", sid, "保留接线")
    assert store.branch_marks(target) == {"1/2"}
    assert json.loads(store.port(target)["occupied_drawings"]) == {"1/2": "P-17"}
    with pytest.raises(ValueError, match="不能移除"):
        store.save_circuit(target, original)
    history.undo()
    assert store.circuit(target) == original and store.branch_assignments(target)[0]["branch"] == "1"
    history.redo()
    assert store.branch_assignments(target)[0]["branch"] == "1/2"
    tree["branch_components"]["1/2"] = [
        dict(stage="3 K", kind="放大器", model="LNA"),
        dict(stage="MC", kind="滤波器", mark="末端元件"),
    ]
    store.save_circuit(target, tree)
    history.detach()
    store.close()

    with closing(FridgeStore.open(path)) as reopened:
        view = CircuitDialog(reopened, reopened.port(target))
        items = view.view.scene().items()
        locations = {item.data(0): item.sceneBoundingRect() for item in items if item.data(0) is not None}
        root_x = locations[(target, None, "switch")].center().x()
        next_switch_x = locations[(target, "1", "switch")].center().x()
        # The root MC switch shares the MC column with the root's other MC components.
        headers = {item.toolTip() or item.text(): item.x() for item in items
                   if hasattr(item, "text") and item.y() == 11}
        assert headers["MC"] < root_x < headers["分支 · MC"] < next_switch_x < headers["第2级 · 3 K"]
        assert locations[(target, "1/2", 0)].left() > next_switch_x
        assert locations[(target, "1/2", 1)].left() > next_switch_x
        assert "开关" not in headers
        columns, entries = reopened.table_entries()
        row = next(values for pid, branch, values in entries if pid == target and branch == "1/2")
        description = row[columns.index("低温线路")]
        assert description.index("SW-A") < description.index("原分支元件") < description.index("SW-B") < description.index("末端元件")
        view.close()
    app.processEvents()


def test_upgrade_and_live_drawing_with_switch_and_exports(tmp_path):
    a = tmp_path / 'a.qchip'; b = tmp_path / 'b.qchip'
    qa = sample(a, '样品 A'); qb = sample(b, '样品 B')
    with closing(sqlite3.connect(a)) as db, db:
        db.execute('DROP TABLE sample_box_bindings'); db.execute('DROP TABLE sample_box_ports')
        db.execute('ALTER TABLE devices DROP COLUMN drawing_number')
        db.execute('PRAGMA user_version=7')
    with ChipStore.open(a) as chip:
        assert chip.device(qa)['drawing_number'] == ''
        chip.update_device(qa, drawing_number='图-A')
    assert (tmp_path / 'a.pre-v7.qchip').is_file()
    with ChipStore.open(b) as chip:
        chip.update_device(qb, drawing_number='图-B')
    path = tmp_path / 'fridge.qfridge'
    store = FridgeStore.create(path, 'DR'); aid = store.add_sample(a); bid = store.add_sample(b)
    target = port(store, 'F', 'O4')['id']
    store.assign(target, aid, devices=[qa], usage='Z', drawing='历史文字')
    store.close()
    with closing(sqlite3.connect(path)) as db, db:
        for table in ("attachment_versions", "record_attachments", "attachment_blobs"):
            db.execute(f"DROP TABLE {table}")
        db.execute('DROP TABLE port_circuits'); db.execute('DROP TABLE branch_assignments')
        db.execute('DROP TABLE component_library')
        db.execute('ALTER TABLE assignments DROP COLUMN box_port_id')
        db.execute('PRAGMA user_version=1')
    store = FridgeStore.open(path)
    assert (tmp_path / 'fridge.pre-v1.qfridge').is_file()
    assert store.port(target)['drawing'] == '历史文字' and store.circuit(target)['components'] == []
    preset = preset_for('F', 'O4')
    with pytest.raises(ValueError, match='所属开关分支'):
        store.save_circuit(target, preset)
    assert store.port(target)['sample_id'] == aid
    history = EditHistory(); history.bind(store.db); store.undo_history = history
    store.save_circuit(target, preset, '1')
    store.assign(target, bid, devices=[qb], usage='XY', branch='2')
    with pytest.raises(ValueError, match='已分配'):
        store.assign(target, aid, branch='2')
    with pytest.raises(ValueError, match='不能移除'):
        store.save_circuit(target, {'components': [], 'switch': ['1']})
    assert len(store.branch_assignments(target)) == 2
    store.unassign(target, '2'); assert store.circuit(target) == preset
    history.undo(); assert len(store.branch_assignments(target)) == 2
    with ChipStore.open(a) as chip:
        chip.update_device(qa, drawing_number='最新编号')
        for point in ('default', 'other'):
            head, rows = build_tables(chip, point)['比特']
            assert rows[0][head.index('图纸编号')] == '最新编号'
    headers, rows = store.table(); selected = [r for r in rows if r[0:2] == ['F', 'O4']]
    assert len(selected) == 6
    assert '最新编号' in selected[0][headers.index('比特图纸编号')]
    assert selected[0][headers.index('线路用途')] == 'Z'
    assert selected[0][headers.index('历史图纸编号 / 信号名称')] == '历史文字'
    a.rename(tmp_path / 'missing.qchip')
    assert '样品不可用' in store.table()[1][-6][headers.index('比特图纸编号')]
    (tmp_path / 'missing.qchip').rename(a)
    assert '最新编号' in store.table()[1][-6][headers.index('比特图纸编号')]
    with ChipStore.open(b) as chip:
        chip.delete_device(qb)
    app = QApplication.instance() or QApplication([])
    dialog = AssignmentDialog(None, store, store.port(target), '2')
    assert '器件已删除' in dialog.devices.item(0).text()
    store.assign(target, bid, devices=dialog.device_ids(), branch='2')
    assert '器件已删除' in store.table()[1][-5][headers.index('比特图纸编号')]
    for suffix in ('csv', 'xlsx'):
        store.export_table(tmp_path / f'wiring.{suffix}')
    with (tmp_path / 'wiring.csv').open(encoding='utf-8-sig', newline='') as f:
        assert any('最新编号' in cell for row in csv.reader(f) for cell in row)
    wb = load_workbook(tmp_path / 'wiring.xlsx', read_only=True)
    assert any('最新编号' in str(cell) for row in wb.active.values for cell in row)
    wb.close(); history.detach(); store.close()
    store = FridgeStore.open(path)
    assert len(store.branch_assignments(target)) == 2 and store.circuit(target) == preset
    store.close()


def test_merge_switch_edit_preserves_input_components_and_output_assignment(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    chip = tmp_path / "sample.qchip"
    sample(chip, "样品")
    path = tmp_path / "join.qfridge"
    store = FridgeStore.create(path, "DR", with_preset=True)
    sid = store.add_sample(chip)
    target = port(store, "F", "O4")["id"]
    original = store.circuit(target)
    original["switch_component"] = {"stage": "MC"}
    original["branch_components"] = {
        "1": [dict(stage="MC", kind="环行器", mark="C1")],
        "2": [dict(stage="MC", kind="环行器", mark="C2")],
        "3": [dict(stage="MC", kind="滤波器", mark="IR3")],
    }
    store.save_circuit(target, original)
    store.assign(target, sid, "R1", branch="1", notes="原接线备注")
    store.assign(target, sid, "R2", branch="2")
    store.set_branch_occupied(target, "1", True)
    store.set_occupied_drawing(target, "1", "D-1")
    candidate = dict(original, merge_switches={"合路1": dict(inputs=["1", "2"], component=dict(stage="MC"))})
    with pytest.raises(ValueError, match="只能保留一份样品分配"):
        store.save_circuit(target, candidate, branch_transfers={"1": "合路1", "2": "合路1"})
    assert store.circuit(target) == original
    store.unassign(target, "2")
    store.set_branch_occupied(target, "2", True)
    store.set_occupied(target, True)
    history = EditHistory()
    history.bind(store.db)
    store.undo_history = history

    def configure(inputs, stage, model, output=None):
        def run(dialog):
            for index in range(dialog.merge_list.count()):
                item = dialog.merge_list.item(index)
                checked = item.data(Qt.ItemDataRole.UserRole) in inputs
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            dialog.stage.setCurrentText(stage)
            dialog.fields["model"].setText(model)
            if output:
                assert not dialog.output.isReadOnly()
                dialog.output.setText(output)
            dialog.accept()
            return dialog.result()
        monkeypatch.setattr(ComponentDialog, "exec", run)

    configure({"1", "2"}, "MC", "SW-2")
    editor = CircuitDialog(store, store.port(target), initial_branch="1")
    editor.configure_merge()
    assert "输入支路 1、2" in editor.details.toPlainText()
    items = {item.data(0): item.sceneBoundingRect() for item in editor.view.scene().items()
             if item.data(0) is not None}
    assert items[(target, "合路1", "merge")].left() > max(
        items[(target, "1", 0)].right(), items[(target, "2", 0)].right())
    editor.rows("合路1").append(dict(stage="MC", kind="HEMT", model="LNA"))
    editor.accept()
    assert editor.result() == QDialog.DialogCode.Accepted, editor.error.text()
    merged = store.circuit(target)
    assert terminal_branches(merged) == ["3", "4", "5", "6", "合路1"]
    assert store.branch_marks(target) == {"合路1"}
    assert json.loads(store.port(target)["occupied_drawings"]) == {"合路1": "D-1"}
    assert store.port(target)["manually_occupied"]
    assert store.branch_assignments(target)[0]["branch"] == "合路1"
    history.undo()
    assert store.circuit(target) == original and store.branch_marks(target) == {"1", "2"}
    history.redo()
    assert store.circuit(target) == merged
    history.detach()
    store.close()

    with closing(FridgeStore.open(path)) as reopened:
        history.bind(reopened.db)
        reopened.undo_history = history
        editor = CircuitDialog(reopened, reopened.port(target))
        configure({"3", "4"}, "50 mK", "SW-3", output="3")
        editor.edit_clicked((target, "合路1", "merge"))
        assert "已存在" in editor.error.text() and editor.draft == merged
        configure({"3", "4"}, "50 mK", "SW-3", output="中间编号")
        editor.edit_clicked((target, "合路1", "merge"))
        configure({"3", "4"}, "50 mK", "SW-3", output="合路读出")
        editor.edit_clicked((target, "中间编号", "merge"))
        editor.accept()
        result = reopened.circuit(target)
        assert result["merge_switches"]["合路读出"]["inputs"] == ["3", "4"]
        assert result["merge_switches"]["合路读出"]["component"]["stage"] == "50 mK"
        assert result["branch_components"]["合路读出"] == merged["branch_components"]["合路1"]
        assert "合路1" not in result["branch_components"]
        assignment, = reopened.branch_assignments(target)
        assert (assignment["branch"], assignment["notes"]) == ("合路读出", "原接线备注")
        assert reopened.branch_marks(target) == {"合路读出"}
        assert json.loads(reopened.port(target)["occupied_drawings"]) == {"合路读出": "D-1"}
        history.undo()
        assert reopened.circuit(target) == merged and reopened.branch_assignments(target)[0]["branch"] == "合路1"
        history.redo()
        assert reopened.circuit(target) == result and reopened.branch_assignments(target)[0]["branch"] == "合路读出"
        headers, entries = reopened.table_entries()
        row = next(values for pid, branch, values in entries if pid == target and branch == "合路读出")
        line = row[headers.index("低温线路")]
        assert "IR3" in line and "SW-3" in line and "LNA" in line and "C1" not in line and "C2" not in line
        history.detach()
    app.processEvents()


def test_choose_terminal_branch_and_merge_output_on_diagram(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / "pick.qfridge", "DR", with_preset=True)
    target = port(store, "F", "O4")["id"]
    circuit = store.circuit(target)
    circuit["merge_switches"] = {"OUT": dict(inputs=["1", "2"], component=dict(stage="MC"))}
    store.save_circuit(target, circuit)
    store.set_branch_occupied(target, "4", True)
    panel = FridgePanel(store)
    selected = []

    def choose(dialog):
        dialog.show()
        app.processEvents()
        assert not dialog.choose_button.isEnabled()
        choices = {item.data(0)[1]: item for item in dialog.view.scene().items()
                   if item.data(0) and item.data(0)[2] == "endpoint"}
        assert set(choices) == {"3", "5", "6", "OUT"}
        for name in ("3", "OUT"):
            item = next(item for item in dialog.view.scene().items() if item.data(0) == (target, name, "endpoint"))
            dialog.view.ensureVisible(item)
            QTest.mouseClick(dialog.view.viewport(), Qt.MouseButton.LeftButton,
                             pos=dialog.view.mapFromScene(item.sceneBoundingRect().center()))
            selected.append(dialog.chosen_branch)
        dialog.choose_button.click()
        return dialog.result()

    monkeypatch.setattr(CircuitDialog, "exec", choose)
    assert panel.choose_branch(store.port(target)) == "OUT"
    assert selected == ["3", "OUT"]
    panel.close()
    app.processEvents()


def test_preset_conflicts_template_and_circuit_dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / 'f.qfridge', 'DR')
    store.create_board('G', 'A')
    target = port(store, 'A', '1')['id']; copy = port(store, 'G', '1')['id']
    store.save_circuit(target, {'components': [{'stage': 'MC', 'kind': '标记', 'mark': 'existing'}], 'switch': [], 'legacy_details': {'checks': ['ok']}})
    preview = store.preset_preview()
    assert next(x for x in preview if x['port']['id'] == target)['conflict']
    assert next(x for x in preview if x['port']['id'] == copy)['preset'] == preset_for('A', '1')
    dialog = PresetDialog(store)
    i = next(i for i, x in enumerate(dialog.preview) if x['port']['id'] == target)
    assert dialog.table.item(i, 0).checkState() == Qt.CheckState.Unchecked
    assert 'existing' in dialog.table.item(i, 3).text()
    editor = CircuitDialog(store, store.port(target))
    assert any('existing' in item.toolTip() for item in editor.view.scene().items())
    editor.accept(); assert store.circuit(target)['legacy_details'] == {'checks': ['ok']}
    store.close(); app.processEvents()


def test_migrate_to_branch_and_explicit_legacy_import(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'sample.qchip'; device = sample(source, '样品')
    with ChipStore.open(source) as chip:
        line = chip.add_line('Z', port='O4', devices=[device], details={
            'board': 'F', 'components': [{'stage': 'MC', 'kind': 'filter', 'name': 'old',
                                           'terminal': 'P1', 'notes': 'note'}],
            'amplifiers': [{'model': 'amp'}], 'checks': [{'result': 'ok'}]})
        original = chip.line_details(line)
    store = FridgeStore.create(tmp_path / 'f.qfridge', 'DR', with_preset=True)
    sid = store.add_sample(source); target = port(store, 'F', 'O4')['id']
    store.migrate_lines(sid, {line: (target, '3')})
    assert json.loads(store.branch_assignments(target)[0]['legacy_json'])['details'] == original
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('分支 3', True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    dialog = CircuitDialog(store, store.port(target)); dialog.import_legacy(); dialog.accept()
    circuit = store.circuit(target)
    assert circuit['legacy_details'] == original
    assert circuit['components'][0]['terminal'] == 'P1'
    assert circuit['components'][0]['notes'] == 'note'
    store.unassign(target, '3')
    assert store.circuit(target)['legacy_details'] == original
    store.close(); app.processEvents()


def test_qubit_editor_updates_fridge_and_branch_filters(tmp_path):
    from qubit_sequencer import gui
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'sample.qchip'; device = sample(source, '样品')
    fridge = tmp_path / 'f.qfridge'; store = FridgeStore.create(fridge, 'DR', with_preset=True)
    sid = store.add_sample(source); target = port(store, 'F', 'O4')['id']
    store.assign(target, sid, devices=[device], usage='Z', branch='1'); store.close()
    window = gui.MainWindow(); window.show(); app.processEvents()
    assert window._open_path(str(source)); window._open_fridge_path(str(fridge)); app.processEvents()
    panel = window.fridge_panel; panel.open_board('F'); panel.select_port(target)
    window.navigation.setCurrentRow(0); window.on_scene_clicked('device', device)
    window.detail_editor.drawing_number.setText('Q-42'); window.save_device()
    window.tabs.setCurrentWidget(window.fridge_page); app.processEvents()
    assert 'Q-42' in panel.circuit_info.text()
    panel.views.setCurrentIndex(1); panel.status.setCurrentIndex(panel.status.findData('未分配'))
    rows = [[panel.table.item(r, c).text() for c in range(panel.table.columnCount())]
            for r in range(panel.table.rowCount())]
    assert sum(row[1] == 'O4' for row in rows) == 5
    assert all(row[5] == '未分配' for row in rows)
    window.close(); app.processEvents()

def test_reopen_remembers_main_page(tmp_path):
    from qubit_sequencer import gui
    app = QApplication.instance() or QApplication([])
    window = gui.MainWindow(); window.show(); app.processEvents()
    window.tabs.setCurrentWidget(window.fridge_page); window.close(); app.processEvents()
    reopened = gui.MainWindow(); reopened.show(); app.processEvents()
    assert reopened.tabs.currentWidget() is reopened.fridge_page
    reopened.tabs.setCurrentWidget(reopened.sample_box_panel); reopened.close(); app.processEvents()
    again = gui.MainWindow(); again.show(); app.processEvents()
    assert again.tabs.currentWidget() is again.sample_box_panel
    again.close(); app.processEvents()


def test_manual_occupied_marker_and_branch_drawings(tmp_path):
    from qubit_sequencer.fridge_ui import FridgePanel
    from PySide6.QtWidgets import QApplication
    from qubit_sequencer.undo import EditHistory
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'occupied.qfridge'; store = FridgeStore.create(path, 'DR', with_preset=True)
    p = port(store, 'F', 'O4'); history = EditHistory(); history.bind(store.db); store.undo_history = history
    store.set_occupied(p['id'], True); store.set_occupied_drawing(p['id'], '1', 'gate R1out')
    assert p['id'] not in {row['port']['id'] for row in store.preset_preview()}
    with pytest.raises(ValueError, match='取消标记'): store.save_circuit(p['id'], {'components': [], 'switch': []})
    headers, rows = store.table(); row = next(r for r in rows if r[0:2] == ['F', 'O4'] and r[3] == '1')
    assert row[headers.index('手动占用标记')] == '已占用'
    assert row[headers.index('占用接线目标图纸编号')] == 'gate R1out'
    history.undo(); assert store.table()[1] != rows
    history.redo(); history.detach(); store.close()
    store = FridgeStore.open(path); assert store.port(p['id'])['manually_occupied']
    panel = FridgePanel(store); panel.show(); panel.open_board('F'); panel.select_port(p['id']); app.processEvents()
    assert panel.occupied.currentData() == 'occupied' and not panel.port_buttons[0].isEnabled()
    panel.occupied_branch.setCurrentIndex(panel.occupied_branch.findData('1')); assert panel.occupied_drawing.text() == 'gate R1out'
    panel.status.setCurrentIndex(panel.status.findData('未分配'))
    assert not any(panel.table.item(r, 1).text() == 'O4' for r in range(panel.table.rowCount()))
    panel.status.setCurrentIndex(panel.status.findData('全部状态'))
    panel.occupied.setCurrentIndex(panel.occupied.findData('damaged'))
    assert store.port(p['id'])['manual_status'] == 'damaged'
    panel.status.setCurrentIndex(panel.status.findData('已损坏'))
    assert any(panel.table.item(r, 1).text() == 'O4' for r in range(panel.table.rowCount()))
    panel.status.setCurrentIndex(0)
    panel.occupied.setCurrentIndex(panel.occupied.findData('')); assert not store.port(p['id'])['manually_occupied']
    panel.close(); app.processEvents()


def test_switch_manual_branch_occupancy_and_view(tmp_path):
    from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem
    from qubit_sequencer.fridge_ui import FridgePanel
    from qubit_sequencer.undo import EditHistory
    app = QApplication.instance() or QApplication([])
    chip_path = tmp_path / 'branch.qchip'; device = sample(chip_path, '样品')
    path = tmp_path / 'f.qfridge'; store = FridgeStore.create(path, 'DR', with_preset=True)
    sid = store.add_sample(chip_path); target = port(store, 'F', 'O4')['id']
    history = EditHistory(); history.bind(store.db); store.undo_history = history
    store.set_branch_occupied(target, '1', True)
    store.set_occupied_drawing(target, '1', 'R1OUT')
    with pytest.raises(ValueError, match='分支已手动'): store.assign(target, sid, devices=[device], branch='1')
    store.assign(target, sid, devices=[device], branch='2')
    store.set_branch_occupied(target, '2', True)
    with pytest.raises(ValueError, match='分支已手动'): store.unassign(target, '2')
    store.set_branch_occupied(target, '2', False)
    with pytest.raises(ValueError, match='不能移除'): store.save_circuit(target, {'components': [], 'switch': ['2']})
    headers, entries = store.table_entries(); rows = {branch: values for ident, branch, values in entries if ident == target}
    assert rows['1'][headers.index('占用状态')] == '已占用'
    assert rows['1'][headers.index('占用接线目标图纸编号')] == 'R1OUT'
    assert rows['2'][headers.index('占用状态')] == '已占用'
    assert rows['3'][headers.index('占用状态')] == '未分配'
    store.export_table(tmp_path / 'branches.csv')
    assert 'R1OUT' in (tmp_path / 'branches.csv').read_text(encoding='utf-8-sig')
    history.detach(); store.close(); store = FridgeStore.open(path)
    panel = FridgePanel(store); panel.show(); panel.open_board('F'); panel.select_port(target); app.processEvents()
    item = next(item for item in panel.scene.items() if hasattr(item, 'port') and item.port['id'] == target)
    assert '2/6' in [child.text() for child in item.childItems() if isinstance(child, QGraphicsSimpleTextItem)]
    assert '分支 1 · 手动占用' in panel.circuit_info.text()
    assert '分支 3 · 未分配' in panel.circuit_info.text()
    panel.occupied_branch.setCurrentIndex(panel.occupied_branch.findData('1')); assert panel.branch_occupied.isChecked()
    panel.branch_occupied.setChecked(False); assert store.branch_marks(target) == set()
    panel.undo(); assert store.branch_marks(target) == {'1'}
    panel.occupied.setCurrentIndex(panel.occupied.findData('damaged'))
    with pytest.raises(ValueError, match='损坏'): store.assign(target, sid, devices=[device], branch='3')
    panel.close(); app.processEvents()

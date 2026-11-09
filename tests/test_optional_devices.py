"""Optional readout/filter devices share the existing editing and marker lifecycle."""

import os
import sqlite3
import re
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from qubit_sequencer import gui
from qubit_sequencer.exports import build_tables, export_csv, export_xlsx
from qubit_sequencer.storage import ChipStore, SCHEMA, SCHEMA_VERSION, PRESETS
from qubit_sequencer.undo import EditHistory
from qubit_sequencer.readout_store import READOUT_SCHEMA, readout_point, readout_endpoints
from openpyxl import load_workbook


@pytest.mark.parametrize("kind,subtype,title", [
    ("readout", "readout_line", "读取线"), ("filter", "tunable_filter", "可调 filter")])
def test_optional_device_roundtrip_export_and_delete_undo(tmp_path, kind, subtype, title):
    path = tmp_path / "optional.qchip"
    with ChipStore.create(path, "测试芯片") as store:
        assert store.devices(kind) == []
        ident = store.add_device(kind, subtype, "test", .5, 1)
        store.add_definition(f"{kind}.frequency", "频率", kind, unit="GHz")
        store.set_parameter(ident, f"{kind}.frequency", 6.2)
        store.set_chip_design("test.png", b"embedded-test-image")
        store.set_design_marker(ident, .25, .75)
        store.set_design_rotation(90)
        line = store.add_line("读取", devices=[ident])
        store.update_device(ident, label="renamed", notes="manual position")
        history = EditHistory(); history.bind(store.db); store.undo_history = history
        store.delete_device(ident)
        assert store.design_markers() == []
        assert store.line_devices(line) == []
        history.undo()
        assert store.design_markers()[0]["label"] == "renamed"
        assert store.line_devices(line) == [ident]
        assert store.current_values()[(ident, f"{kind}.frequency")] == 6.2
        history.redo()
        assert store.devices(kind) == []
        history.undo()
        history.detach()
        store.backup_to(tmp_path / "backup.qchip")
    for copy in (path, tmp_path / "backup.qchip"):
        with ChipStore.open(copy) as store:
            assert store.validate() == []
            marker = store.design_markers()[0]
            assert (marker["kind"], marker["x"], marker["y"]) == (kind, .25, .75)
            assert store.chip_design()["rotation"] == 90
            headers, rows = build_tables(store)[title]
            assert rows[0][headers.index("名称")] == "renamed"
            assert rows[0][-1] == 6.2
    with ChipStore.open(path) as store:
        assert (tmp_path / ("readout_lines.csv" if kind == "readout" else "filters.csv")) in export_csv(store, tmp_path)
        export_xlsx(store, tmp_path / "optional.xlsx")
    workbook = load_workbook(tmp_path / "optional.xlsx")
    assert workbook[title].cell(2, 2).value == "renamed"
    workbook.close()


def test_v5_upgrade_preserves_existing_device_children(tmp_path):
    path = tmp_path / "v5.qchip"
    schema_v5 = SCHEMA.replace(",\n drawing_number TEXT NOT NULL DEFAULT ''", "").replace(READOUT_SCHEMA, "").replace("'qubit','coupler','readout','filter'", "'qubit','coupler'")
    schema_v5 = re.sub(r",\n readout_length[^\n]*\n readout_direction[^\n]*\n readout_angle[^\n]*", "", schema_v5)
    with sqlite3.connect(path) as db:
        db.executescript(schema_v5)
        db.executemany("INSERT INTO meta VALUES (?,?)", [("format", "qchip"), ("chip_name", "old"),
                                                      ("template", "custom"), ("created_at", "2026-10-07")])
        db.executemany("INSERT INTO parameter_definitions VALUES (?,?,?,?,?,?,0)", [row for row in PRESETS if row[2] not in ("readout", "filter")])
        db.execute("PRAGMA user_version=5")
    # Seed real v5 constraints through the domain API without running migration.
    with ChipStore(path, sqlite3.connect(path)) as store:
        q = store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
        c = store.add_device("coupler", "fixed_coupler", "C1", 1, 0)
        store.add_link(q, c)
        store.set_parameter(q, "qubit.f01", 5.1)
        store.set_chip_design("old.png", b"original-image")
        store.set_design_marker(q, .1, .2)
        store.add_line("XY", devices=[q], details={"board": "test-board"})
        tables = [row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {table: [tuple(row) for row in store.db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                  for table in tables}
    with ChipStore.open(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for table in tables:
            after = {row[0]: tuple(row) for row in store.db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')}
            for row in before[table]:
                assert after[row[0]][:len(row)] == row
            assert len(after) == len(before[table]) + (2 if table == "parameter_definitions" else 0)
        assert store.validate() == []
        store.add_device("readout", "readout_line", "R1", 2, 0)
        f = store.add_device("filter", "tunable_filter", "F1", 3, 0)
        store.add_definition("filter.test", "原始幅度", "filter")
        store.set_parameter(f, "filter.test", .1)
    with sqlite3.connect(tmp_path / "v5.pre-v5.qchip") as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 5
        assert old.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 2
        assert "'readout'" not in old.execute("SELECT sql FROM sqlite_master WHERE name='devices'").fetchone()[0]


def test_place_mark_rotate_inspect_filter_and_undo_in_gui(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "gui.qchip"
    with ChipStore.create(path, "可选元件", "surface") as store:
        store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
    window = gui.MainWindow(str(path)); window.resize(1200, 800); window.show()
    try:
        app.processEvents()
        ids = []
        for action, kind, label, x in ((window.place_readout_action, "readout", "R1", .5),
                                     (window.place_filter_action, "filter", "F1", 1.5)):
            action.setChecked(True)
            with patch.object(gui.QInputDialog, "getText", return_value=(label, True)):
                if kind == "readout":
                    window.on_blank_clicked(x-1, 1)
                    window.on_blank_clicked(x+1, 1)
                else:
                    window.on_blank_clicked(x, 1)
            ident = window.selected[1]; ids.append(ident)
            assert window.store.device(ident)["kind"] == kind
            assert window.scene._device_items[ident].data(0) == ("device", ident)
            assert label in window.scene._device_items[ident].toolTip()
            window.undo_edit(); assert ident not in window.scene._device_items
            window.redo_edit(); assert window.store.device(ident)["kind"] == kind
        image = QImage(800, 600, QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.lightGray)
        image_path = tmp_path / "layout.png"; assert image.save(str(image_path))
        window.store.set_chip_design(image_path.name, image_path.read_bytes())
        window.design_panel.reload_image(); window.design_button.click(); app.processEvents()
        panel = window.design_panel
        assert not window.place_filter_action.isEnabled()
        panel.edit_button.setChecked(True); panel.angle.setValue(37.5)
        for kind, ident, x in (("readout", ids[0], .25), ("filter", ids[1], .75)):
            panel.device_kind.setCurrentIndex(panel.device_kind.findData(kind))
            assert panel.devices.count() == 1
            rect = panel.view.image_item.boundingRect()
            point = panel.view.mapFromScene(panel.view.image_item.mapToScene(QPointF(x * rect.width(), .4 * rect.height())))
            QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            marker = next(row for row in window.store.design_markers() if row["device_id"] == ident)
            assert marker["x"] == pytest.approx(x, abs=.006)
            assert marker["y"] == pytest.approx(.4, abs=.006)
            assert panel.view.markers[ident].kind == kind
            window.undo_edit(); assert ident not in panel.view.markers
            window.redo_edit(); assert ident in panel.view.markers
        panel.edit_button.setChecked(False)
        # A marker outside the selected category still opens the correct editor.
        panel.activate_marker(ids[0], QPoint(200, 200)); app.processEvents()
        assert panel.device_kind.currentData() is None
        assert window.device_popup.device_id == ids[0]
        window.device_popup.label.setText("R2"); window.device_popup.save()
        assert panel.view.markers[ids[0]].document.toPlainText() == "R2"
        window.device_popup.close()
        window.undo_edit(); assert panel.view.markers[ids[0]].document.toPlainText() == "R1"
        window.redo_edit(); assert panel.view.markers[ids[0]].document.toPlainText() == "R2"
        assert window._open_path(str(path))
        assert panel.angle.value() == 37.5
        assert len(panel.view.markers) == 2
        window.design_button.click()
        window.table_kind.setCurrentText("可调 filter")
        window.on_data_row_double_clicked(0, 1)
        assert window.selected == ("device", ids[1])
    finally:
        window.drafts.clear(); window.close(); app.processEvents()


def test_shared_bus_filter_anchor_geometry_history_and_atomic_placement(tmp_path):
    path = tmp_path / "shared.qchip"
    with ChipStore.create(path, "共享读取测试") as store:
        qs = [store.add_device("qubit", "fixed_transmon", f"Q{i}", i*2, 0) for i in range(2)]
        coupler = store.add_device("coupler", "tunable_coupler", "C1", 1, 0)
        bus = store.add_device("readout", "readout_line", "R1", 1, 0)
        links = [store.add_readout_link(bus, q) for q in qs]
        assert store.add_readout_link(bus, qs[0]) == links[0]
        # At the coupler's axis the bus moves to a clear parallel lane.
        x, y = readout_point(store.device(bus), .5, store.devices())
        assert (x-store.device(coupler)["x"])**2 + (y-store.device(coupler)["y"])**2 > .21**2
        f = store.add_device("filter", "tunable_filter", "F1", x, y, readout_id=bus, readout_position=.5)
        assert len(store.readout_links(bus)) == 3
        assert [d["key"] for d in store.definitions("filter")] == ["filter.bias_z"]
        assert [d["key"] for d in store.definitions("readout")] == ["readout.resonator_frequency"]
        store.set_parameter(f, "filter.bias_z", 0)
        store.set_parameter(bus, "readout.resonator_frequency", 6.5)
        history = EditHistory(); history.bind(store.db); store.undo_history = history
        store.update_device(bus, x=3, y=1, readout_direction="vertical", readout_length=4)
        assert (store.device(f)["x"], store.device(f)["y"]) == (3, 1)
        assert len(history.steps) == 1
        history.undo(); assert store.device(f)["y"] == pytest.approx(y)
        history.redo()
        store.update_device(f, x=4, y=2)
        assert (store.device(f)["x"], store.device(f)["y"]) == (3, 2)
        assert store.filter_readout(f)["position"] == .75
        store.delete_device(bus)
        assert store.readout_links() == []
        assert store.device(f)["label"] == "F1"
        history.undo()
        assert len(store.readout_links(bus)) == 3
        assert len(build_tables(store)["读取线连接"][1]) == 3
        assert store.current_values()[(f, "filter.bias_z")] == 0
        assert store.current_values()[(bus, "readout.resonator_frequency")] == 6.5
        before = len(store.devices())
        with pytest.raises(ValueError):
            store.add_device("filter", "tunable_filter", "invalid", 5, 5, readout_id=qs[0])
        assert len(store.devices()) == before
        assert store.validate() == []
        history.detach()
    with ChipStore.open(path) as store:
        assert len(store.readout_links(bus)) == 3
        assert store.filter_readout(f)["position"] == .75


def test_connect_shared_readout_place_filter_on_bus_and_hide(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "bus-ui.qchip"
    with ChipStore.create(path, "共享读取界面测试") as store:
        qs = [store.add_device("qubit", "fixed_transmon", f"Q{i}", i*2, 0) for i in range(2)]
        c = store.add_device("coupler", "tunable_coupler", "C1", 1, 0)
        store.add_coupling(qs[0], qs[1], "coupler", c)
        bus = store.add_device("readout", "readout_line", "R1", 1, 1)
        store.update_device(bus, readout_length=3)
        store.set_parameter(bus, "readout.resonator_frequency", 6.6)
    window = gui.MainWindow(str(path)); window.show()
    try:
        app.processEvents()
        window.connect_action.setChecked(True)
        mode = window.connection_mode
        mode.setCurrentIndex(mode.findText("读取腔耦合"))
        for first, second in ((bus, qs[0]), (qs[1], bus)):
            window.on_scene_clicked("device", first)
            window.on_scene_clicked("device", second)
        assert len(window.store.readout_links(bus)) == 2
        assert "读取腔耦合" in window.selected_title.text()
        with patch.object(window, "_error") as error:
            window.on_scene_clicked("device", qs[0])
            window.on_scene_clicked("device", qs[1])
        assert "一个比特和一条读取线" in str(error.call_args.args[1])
        assert len(window.store.couplings()) == 1
        assert window.scene._device_items[c].opacity() < window.scene._device_items[qs[0]].opacity()
        branches = [item for item in window.scene.items() if item.data(0) and item.data(0)[0] == "readout_link"]
        assert len(branches) == 2
        for branch in branches:
            assert branch.line().x1() == branch.line().x2()
        assert window.scene._device_items[bus].path().elementCount() == 2
        window.selected = ("device", bus); window.refresh_detail()
        assert "Q0" in window.readout_info.text() and "Q1" in window.readout_info.text()
        window.detail_editor.readout_direction.setCurrentIndex(1)
        window.save_device()
        assert window.store.device(bus)["readout_direction"] == "vertical"
        window.place_filter_action.setChecked(True)
        from qubit_sequencer.diagram import PAD, CELL
        x, y = readout_point(window.store.device(bus), 2/3, window.store.devices())
        point = window.view.mapFromScene(QPointF(PAD+x*CELL, PAD+y*CELL))
        with patch.object(gui.QInputDialog, "getText", return_value=("F1", True)):
            QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        f = window.store.devices("filter")[0]["id"]
        assert window.store.filter_readout(f)["readout_id"] == bus
        assert window.store.device(f)["y"] == pytest.approx(1.5, abs=.02)
        image = QImage(500, 500, QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.gray)
        image_path = tmp_path / "bus.png"; image.save(str(image_path))
        window.store.set_chip_design("bus.png", image_path.read_bytes())
        for i, ident in enumerate((qs[0], bus, f)):
            window.store.set_design_marker(ident, .2+i*.25, .5)
        window.design_panel.reload_image(); window.design_panel.refresh_markers()
        window.readout_visible_action.setChecked(False)
        assert bus not in window.scene._device_items and f not in window.scene._device_items
        assert window.scene._device_items[c].opacity() == 1
        assert all(item.opacity() == 1 for item in window.scene.items() if item.data(0) and item.data(0)[0] == "link")
        assert len(window.store.readout_links(bus)) == 3
        assert window.design_panel.view.markers[qs[0]].isVisible()
        assert not window.design_panel.view.markers[bus].isVisible()
        window.readout_visible_action.setChecked(True)
        assert bus in window.scene._device_items and f in window.scene._device_items
        assert window.design_panel.view.markers[f].isVisible()
        window.table_kind.setCurrentText("读取线连接")
        window.on_data_row_double_clicked(0, 1)
        assert window.selected[0] == "readout_link"
        window.notes_edit.setText("shared readout"); window.save_device()
        assert window.store.readout_link(window.selected[1])["notes"] == "shared readout"
        window.field_combo.setCurrentIndex(window.field_combo.findData("readout.resonator_frequency"))
        assert any(item.data(0) == ("device", bus) and hasattr(item, "toPlainText") and item.toPlainText() == "6.6"
                   for item in window.scene.items())
    finally:
        window.drafts.clear(); window.close(); app.processEvents()


def test_readout_endpoint_snapping_preview_cancel_history_and_reopen(tmp_path):
    app = QApplication.instance() or QApplication([])
    from qubit_sequencer.diagram import PAD, CELL
    path = tmp_path / "endpoints.qchip"
    with ChipStore.create(path, "端点吸附") as store:
        store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
        store.add_device("coupler", "tunable_coupler", "C1", 2.5, 0)
    window = gui.MainWindow(str(path)); window.resize(1200, 800); window.show()
    try:
        app.processEvents()
        window.place_readout_action.setChecked(True)
        def click(x, y):
            point = window.view.mapFromScene(QPointF(PAD+x*CELL, PAD+y*CELL))
            QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            app.processEvents()
        with patch.object(gui.QInputDialog, "getText", return_value=("R1", True)) as prompt:
            click(.02, .03)  # Click an existing qubit, not just blank canvas.
            assert window.readout_start == (0, 0)
            assert window.store.devices("readout") == [] and window.undo_history.steps == []
            prompt.assert_not_called()
            pointer = window.view.mapFromScene(QPointF(PAD+2.48*CELL, PAD+.04*CELL))
            QTest.mouseMove(window.view.viewport(), pointer); app.processEvents()
            assert window.scene.readout_end == (2.5, 0)
            click(2.48, .04)  # Endpoint on a coupler is valid, too.
        bus = window.store.devices("readout")[0]["id"]
        a, b = readout_endpoints(window.store.device(bus))
        assert a == pytest.approx((0, 0)); assert b == pytest.approx((2.5, 0))
        assert window.scene.readout_start is None
        assert len(window.undo_history.steps) == 1
        window.undo_edit(); assert window.store.devices("readout") == []
        window.redo_edit(); assert readout_endpoints(window.store.device(bus))[1] == pytest.approx(b)
        count = len(window.undo_history.steps)
        window.on_blank_clicked(-.52, 1.02)
        QTest.keyClick(window.view, Qt.Key.Key_Escape); app.processEvents()
        assert window.readout_start is None
        window.on_blank_clicked(.53, 1.03)
        with patch.object(gui.QInputDialog, "getText", return_value=("", False)) as prompt:
            window.on_blank_clicked(.51, 1.02)  # Same snapped endpoint: keep selecting.
            prompt.assert_not_called()
            window.on_blank_clicked(2.03, 1.02)
        assert window.readout_start is None and len(window.undo_history.steps) == count
        for label, start, end, expected in (
                ("Rv", (3.02, -.03), (3.01, 2.48), ((3, 0), (3, 2.5))),
                ("Rd", (2.49, 2.03), (-.48, .51), ((2.5, 2), (-.5, .5)))):
            with patch.object(gui.QInputDialog, "getText", return_value=(label, True)):
                window.on_blank_clicked(*start); window.on_blank_clicked(*end)
            row = window.store.device(window.selected[1])
            actual = readout_endpoints(row)
            assert actual[0] == pytest.approx(expected[0]); assert actual[1] == pytest.approx(expected[1])
        diagonal = window.selected[1]
        row = window.store.device(diagonal)
        x, y = readout_point(row, .5)
        f = window.store.add_device("filter", "tunable_filter", "F1", x, y, readout_id=diagonal)
        window.store.update_device(f, x=x+.1, y=y+.2)
        row = window.store.device(f)
        _, projected = gui.project_to_readout(window.store.device(diagonal), row["x"], row["y"])
        assert (row["x"], row["y"]) == pytest.approx(projected)
        window.drafts.clear()
        assert window._open_path(str(path))
        assert window.store.device(diagonal)["readout_direction"] == "angled"
        assert readout_endpoints(window.store.device(diagonal))[0] == pytest.approx((2.5, 2))
        headers, rows = build_tables(window.store)["读取线"]
        exported = next(row for row in rows if row[0] == diagonal)
        assert exported[headers.index("起点 X")] == pytest.approx(2.5)
        assert exported[headers.index("终点 Y")] == pytest.approx(.5)
    finally:
        window.drafts.clear(); window.close(); app.processEvents()


def test_v6_upgrade_preserves_readout_geometry_and_children(tmp_path):
    path = tmp_path / "v6.qchip"
    schema = SCHEMA.replace(",\n drawing_number TEXT NOT NULL DEFAULT ''", "").replace(",\n readout_angle REAL", "").replace("'horizontal','vertical','angled'", "'horizontal','vertical'")
    with sqlite3.connect(path) as db:
        db.executescript(schema)
        db.executemany("INSERT INTO meta VALUES (?,?)", [("format", "qchip"), ("chip_name", "v6"),
                                                       ("template", "custom"), ("created_at", "2026-10-07")])
        db.executemany("INSERT INTO parameter_definitions VALUES (?,?,?,?,?,?,0)", PRESETS)
        db.execute("INSERT INTO devices VALUES ('R','readout','readout_line','R1','',1,2,0,0,'','2026-10-07',3,'vertical')")
        db.execute("INSERT INTO devices VALUES ('F','filter','tunable_filter','F1','',1,2,0,0,'','2026-10-07',2,'horizontal')")
        db.execute("INSERT INTO readout_links VALUES ('RF','R','F',.5,'保留关联')")
        db.execute("PRAGMA user_version=6")
    with ChipStore.open(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 8
        assert store.validate() == []
        assert readout_endpoints(store.device("R")) == ((1, .5), (1, 3.5))
        assert store.device("R")["readout_angle"] is None
        assert store.filter_readout("F")["notes"] == "保留关联"
        store.add_device("readout", "readout_line", "Rd", 0, 0, readout_endpoints=((-.5, 0), (1, 2)))
    with sqlite3.connect(tmp_path / "v6.pre-v6.qchip") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert "readout_angle" not in [row[1] for row in db.execute("PRAGMA table_info(devices)")]

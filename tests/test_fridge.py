"""Cryostat projects use isolated databases and simulated sample data."""

import json
import sqlite3
import zipfile
from contextlib import closing

import pytest
from openpyxl import load_workbook
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsItem, QGraphicsRectItem, QMessageBox
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from qubit_sequencer.fridge_store import FridgeStore
from qubit_sequencer.fridge_ui import BoardDialog, FridgeWindow, PortDialog, line_type_value, port_color
from qubit_sequencer.fridge_templates import TRIANGLE_HEIGHT, snap_to_grid
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.undo import EditHistory


def sample(path, name="样品"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ChipStore.create(path, name) as chip:
        device = chip.add_device("qubit", "fixed_transmon", "Q0", 0, 0)
    return device


def port(store, board, number):
    return next(p for p in store.ports() if p["board"] == board and p["number"] == number)


def test_default_layout_allocation_undo_and_reopen(tmp_path):
    chip1 = tmp_path / "one.qchip"; chip2 = tmp_path / "two.qchip"
    device = sample(chip1, "一号"); sample(chip2, "二号")
    store = FridgeStore.create(tmp_path / "test.qfridge", "制冷机")
    assert {b: sum(p["board"] == b for p in store.ports()) for b in "ABCDEF"} == {
        "A": 54, "B": 54, "C": 61, "D": 4, "E": 4, "F": 4}
    assert port(store, "A", "12")["kind"] == "读取输入"
    assert port(store, "A", "13")["kind"] == "XY/Z"
    assert port(store, "B", "42")["kind"] == "直流"
    assert port(store, "B", "44")["kind"] == "XY/Z"
    assert port(store, "D", "O1")["kind"] == "读取输出"
    first = store.add_sample(chip1); second = store.add_sample(chip2)
    history = EditHistory(); history.bind(store.db); store.undo_history = history
    target = port(store, "A", "1")["id"]
    store.assign(target, first, "pin 1", "input", [device])
    with pytest.raises(ValueError, match="已分配"):
        store.assign(target, second)
    history.undo(); assert store.port(target)["sample_id"] is None
    history.redo(); assert store.port(target)["pin"] == "pin 1"
    store.save_port(target, "A", "1", 10, 10, "读取输入")
    history.undo(); assert store.port(target)["x"] != 10
    history.detach(); store.close()
    store = FridgeStore.open(tmp_path / "test.qfridge")
    assert store.port(target)["sample_id"] == first
    assert store.sample_path(first) == chip1.resolve()
    moved = tmp_path / "moved.qchip"; chip1.rename(moved)
    assert store.port(target)["sample_id"] == first
    store.relocate_sample(first, moved)
    assert store.sample_devices(first)[0]["id"] == device
    store.close()


def test_migration_preserves_history_backup_and_conflicts(tmp_path):
    source = tmp_path / "sample.qchip"; device = sample(source)
    details = {"board": "A", "sample_pin": "P1", "components": [{"stage": "MC", "value": 10}],
               "amplifiers": [{"model": "example"}], "checks": [{"result": "ok"}]}
    with ChipStore.open(source) as chip:
        line = chip.add_line("读取输入", "DR", "1", "AWG", "remark", [device], details=details)
        other = chip.add_line("Z", "DR", "unmatched")
        details = chip.line_details(line)
    store = FridgeStore.create(tmp_path / "f.qfridge", "DR")
    sid = store.add_sample(source); preview = store.migration_preview(sid)
    item = next(i for i in preview if i["line"]["id"] == line)
    assert item["reason"] == "可迁移" and item["devices"][0]["id"] == device
    target = item["port_id"]
    backup = store.migrate_lines(sid, {line: target})
    record = store.port(target)
    assert json.loads(record["legacy_json"])["details"] == details
    assert json.loads(record["devices_json"])[0]["id"] == device
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("SELECT id FROM wiring_lines").fetchall() == [(other,)]
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("SELECT COUNT(*) FROM wiring_lines").fetchone()[0] == 2
    with pytest.raises(ValueError, match="占用"):
        store.migrate_lines(sid, {other: target})
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("SELECT id FROM wiring_lines").fetchall() == [(other,)]
    store.close()


def test_migration_failure_rolls_back_both_files(tmp_path):
    source = tmp_path / "sample.qchip"; sample(source)
    with ChipStore.open(source) as chip:
        line = chip.add_line("Z", port="1", details={"board": "A"})
        with chip.db:
            chip.db.execute("""CREATE TRIGGER reject_migration BEFORE DELETE ON wiring_lines
                BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    store = FridgeStore.create(tmp_path / "f.qfridge", "DR"); sid = store.add_sample(source)
    target = port(store, "A", "1")["id"]
    with pytest.raises(sqlite3.IntegrityError):
        store.migrate_lines(sid, {line: target})
    assert store.port(target)["sample_id"] is None
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("SELECT id FROM wiring_lines").fetchall() == [(line,)]
    assert list(tmp_path.glob("sample.pre-fridge-*.qchip"))
    store.close()


def test_bundle_relocations_and_exports(tmp_path):
    a = tmp_path / "a" / "same.qchip"; b = tmp_path / "b" / "same.qchip"
    device = sample(a, "一号"); sample(b, "二号")
    store = FridgeStore.create(tmp_path / "f.qfridge", "DR")
    one = store.add_sample(a); two = store.add_sample(b)
    store.assign(port(store, "A", "1")["id"], one, "P1", devices=[device])
    store.export_table(tmp_path / "wiring.csv"); store.export_table(tmp_path / "wiring.xlsx")
    assert "P1" in (tmp_path / "wiring.csv").read_text(encoding="utf-8-sig")
    book = load_workbook(tmp_path / "wiring.xlsx", read_only=True)
    assert book["制冷机接线"].max_row == 182; book.close()
    bundle = tmp_path / "out.zip"; store.export_bundle(bundle)
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(tmp_path / "unpacked")
        assert len([n for n in archive.namelist() if n.endswith("same.qchip")]) == 2
    copy = FridgeStore.open(tmp_path / "unpacked" / "project.qfridge")
    assert copy.sample_path(one).is_file() and copy.sample_path(two).is_file()
    assert copy.sample_devices(one)[0]["id"] == device
    assert port(copy, "A", "1")["pin"] == "P1"
    copy.close()
    b.unlink()
    before = bundle.read_bytes()
    with pytest.raises(FileNotFoundError): store.export_bundle(bundle)
    assert bundle.read_bytes() == before
    store.close()


def test_fridge_window_smoke(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / "ui.qfridge", "界面")
    window = FridgeWindow(store); window.show(); app.processEvents()
    assert window.table.rowCount() == 181
    window.board.setCurrentText("A"); app.processEvents()
    assert window.table.rowCount() == 54
    window.fit_map(); app.processEvents()
    graphic = next(item for item in window.scene.items() if hasattr(item, "port") and item.port["number"] == "1")
    point = window.graphics.mapFromScene(graphic.scenePos())
    QTest.mouseClick(window.graphics.viewport(), Qt.MouseButton.LeftButton, pos=point); app.processEvents()
    assert window.selected == graphic.port["id"]
    assert "A 盘" in window.info.text()
    port_id = graphic.port["id"]; old_x = graphic.port["x"]
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    destination = window.graphics.mapFromScene(QPointF(5 * 58, 0))
    QTest.mousePress(window.graphics.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(window.graphics.viewport(), destination, delay=20)
    QTest.mouseRelease(window.graphics.viewport(), Qt.MouseButton.LeftButton, pos=destination)
    app.processEvents()
    assert not warnings
    assert store.port(port_id)["x"] != old_x
    window.undo(); assert store.port(port_id)["x"] == old_x
    window.close(); app.processEvents()


def test_embedded_overview_and_separate_undo(tmp_path):
    from qubit_sequencer import gui
    app = QApplication.instance() or QApplication([])
    chip_path = tmp_path / "chip.qchip"; sample(chip_path)
    fridge_path = tmp_path / "fridge.qfridge"
    FridgeStore.create(fridge_path, "测试制冷机").close()
    window = gui.MainWindow(); window.show(); app.processEvents()
    window.navigation.setCurrentRow(2); app.processEvents()
    preview = window.fridge_panel
    assert preview.preview and preview.is_overview() and preview.table.rowCount() == 181
    assert not any(b.isEnabled() for b in preview.port_buttons)
    assert not any(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                   for item in preview.scene.items() if hasattr(item, "port"))
    preview.open_board("B"); app.processEvents()
    assert preview.board.currentData() == "B" and not any(b.isEnabled() for b in preview.port_buttons)

    assert window._open_path(str(chip_path))
    window.store.add_device("qubit", "fixed_transmon", "Q1", 2, 0)
    window._open_fridge_path(str(fridge_path)); app.processEvents()
    panel = window.fridge_panel
    assert window.tabs.currentIndex() == 2 and panel.window() is window and panel.is_overview()
    assert not any(b.isEnabled() for b in panel.port_buttons)
    item = next(item for item in panel.scene.items() if hasattr(item, "port") and item.port["board"] == "A")
    assert not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    panel.fit_map(); app.processEvents()
    point = panel.graphics.mapFromScene(item.scenePos())
    QTest.mouseDClick(panel.graphics.viewport(), Qt.MouseButton.LeftButton, pos=point); app.processEvents()
    assert panel.board.currentData() == "A"
    assert panel.port_buttons[3].isEnabled() and not panel.port_buttons[0].isEnabled()
    item = next(item for item in panel.scene.items() if hasattr(item, "port"))
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    panel.select_port(item.port["id"])
    assert panel.port_buttons[0].isEnabled() and panel.port_buttons[4].isEnabled()
    panel.add_port(); assert panel.placing_port
    panel.graphics.viewport().setFocus()
    QTest.keyClick(panel.graphics.viewport(), Qt.Key.Key_Escape); app.processEvents()
    assert not panel.placing_port
    p = port(panel.store, "A", "1"); old_x = p["x"]
    panel.store.save_port(p["id"], "A", "1", old_x + 10, p["y"], p["kind"])
    window.undo_edit()
    assert panel.store.port(p["id"])["x"] == old_x
    assert any(d["label"] == "Q1" for d in window.store.devices())
    window.set_theme("dark", persist=False)
    assert panel.theme == "dark" and panel.board.currentData() == "A"
    panel.overview_button.click(); app.processEvents()
    assert panel.is_overview() and not window.undo_action.isEnabled()
    window.navigation.setCurrentRow(0); window.undo_edit()
    assert not any(d["label"] == "Q1" for d in window.store.devices())
    window.close(); app.processEvents()


def test_overview_reflows_and_keeps_type_colors_and_assignments(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "sample.qchip"; device = sample(path)
    store = FridgeStore.create(tmp_path / "fridge.qfridge", "测试")
    sample_id = store.add_sample(path); target = port(store, "A", "1")["id"]
    store.assign(target, sample_id, devices=[device])
    window = FridgeWindow(store); window.resize(1500, 800); window.show(); app.processEvents()
    window.fit_map(); app.processEvents()
    assert window.overview_columns == window.overview_column_count() == 4
    items = {i.port["id"]: i for i in window.scene.items() if hasattr(i, "port")}
    assert items[target].brush().color() == port_color("读取输入", "light")
    assert len(items[target].childItems()) == 3  # label and the two strokes of the bright ring
    unassigned = port(store, "A", "2")["id"]
    assert len(items[unassigned].childItems()) == 1
    old_c_y = next(i.scenePos().y() for i in items.values() if i.port["board"] == "C")
    window.resize(700, 800); app.processEvents(); app.processEvents()
    assert window.overview_columns == window.overview_column_count() == 2
    items = {i.port["id"]: i for i in window.scene.items() if hasattr(i, "port")}
    assert next(i.scenePos().y() for i in items.values() if i.port["board"] == "C") > old_c_y
    assert store.port(target)["sample_id"] == sample_id
    window.set_theme("dark"); app.processEvents()
    items = {i.port["id"]: i for i in window.scene.items() if hasattr(i, "port")}
    assert items[target].brush().color() == port_color("读取输入", "dark")
    assert len(items[target].childItems()) == 3
    window.close(); app.processEvents()


def test_readin_readout_labels_preserve_existing_storage(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / "labels.qfridge", "标签")
    incoming = port(store, "A", "1"); outgoing = port(store, "D", "O1")
    assert incoming["kind"] == "读取输入" and outgoing["kind"] == "读取输出"
    dialog = PortDialog(None, incoming)
    assert dialog.kind.currentText() == "读入"
    assert line_type_value(dialog.kind, dialog.original_kind) == "读取输入"
    dialog.kind.setCurrentText("读出")
    assert line_type_value(dialog.kind, dialog.original_kind) == "读取输出"
    dialog.close()
    legacy = port(store, "D", "O2")
    store.save_port(legacy["id"], "D", "O2", legacy["x"], legacy["y"], "读出")
    dialog = PortDialog(None, store.port(legacy["id"]))
    assert line_type_value(dialog.kind, dialog.original_kind) == "读出"
    dialog.close()
    headers, rows = store.table()
    assert next(r for r in rows if r[:2] == ["A", "1"])[headers.index("端口类型")] == "读入"
    assert next(r for r in rows if r[:2] == ["D", "O1"])[headers.index("端口类型")] == "读出"
    window = FridgeWindow(store); window.show(); app.processEvents()
    assert "读入" in window.legend.text() and "读出" in window.legend.text()
    window.kind.setCurrentText("读出"); app.processEvents()
    assert window.table.rowCount() == 12
    assert port_color("读出", "light") == port_color("读取输出", "light")
    window.close(); app.processEvents()


def test_plate_templates_grid_snapping_and_legacy_positions(tmp_path):
    path = tmp_path / "plates.qfridge"
    store = FridgeStore.create(path, "模板")
    history = EditHistory(); history.bind(store.db); store.undo_history = history
    for board, expected in (("A", 54), ("B", 54), ("C", 61), ("D", 4), ("E", 4), ("F", 4)):
        name = "copy-" + board
        store.create_board(name, board)
        copied = [p for p in store.ports() if p["board"] == name]
        assert len(copied) == expected
        assert all(not p["sample_id"] for p in copied)
        assert store.board_grid(name) == ("triangle" if board in "AB" else "square")
        assert all(snap_to_grid(p["x"], p["y"], store.board_grid(name)) == (p["x"], p["y"]) for p in copied)
        assert {(p["number"], p["x"], p["y"], p["kind"]) for p in copied} == {
            (p["number"], p["x"], p["y"], p["kind"]) for p in store.ports() if p["board"] == board}
    history.undo(); assert "copy-F" not in store.boards()
    history.redo(); assert "copy-F" in store.boards()
    with pytest.raises(ValueError, match="重复"):
        store.create_board("copy-A", "B")
    store.create_board("G", "empty_triangle")
    assert store.board_grid("G") == "triangle"
    assert snap_to_grid(0.2, 0.91, "triangle") == pytest.approx((0.5, TRIANGLE_HEIGHT))
    assert snap_to_grid(-1.6, 2.4, "square") == (-2.0, 2.0)
    first = store.save_port(None, "G", "1", 0.2, 0.91, "XY/Z")
    assert (store.port(first)["x"], store.port(first)["y"]) == pytest.approx((0.5, TRIANGLE_HEIGHT))
    with pytest.raises(ValueError, match="已有端口"):
        store.save_port(None, "G", "2", 0.4, 1.05, "直流")
    assert store.next_port_number("G") == "2"
    history.clear()
    store.set_board_grid("G", "square")
    assert store.board_grid("G") == "square"
    assert not history.steps
    store.set_board_grid("G", "triangle")
    assert store.board_grid("G") == "triangle" and not history.steps
    legacy = port(store, "A", "1")
    sample_path = tmp_path / "legacy-sample.qchip"; device = sample(sample_path)
    sample_id = store.add_sample(sample_path)
    store.assign(legacy["id"], sample_id, "PIN", devices=[device])
    with store.db:
        store.db.execute("UPDATE ports SET x=?,y=? WHERE id=?", (0.123456, 12.345678, legacy["id"]))
    legacy = store.port(legacy["id"])
    app = QApplication.instance() or QApplication([])
    dialog = PortDialog(None, legacy)
    assert dialog.position() == (0.123456, 12.345678)
    store.save_port(legacy["id"], "A", legacy["number"], *dialog.position(), "Z")
    assert (store.port(legacy["id"])["x"], store.port(legacy["id"])["y"]) == (0.123456, 12.345678)
    store.create_board("H", "empty_square")
    with store.db:
        store.db.executemany("INSERT INTO ports VALUES (?,?,?,?,?,?)", [
            ("legacy-1", "H", "1", 0.1, 0.1, "XY/Z"), ("legacy-2", "H", "2", 0.3, 0.3, "XY/Z")])
    assert store.port("legacy-1")["x"] == 0.1 and store.port("legacy-2")["x"] == 0.3
    dialog.close(); history.detach(); store.close()
    store = FridgeStore.open(path)
    assert store.board_grid("G") == "triangle" and "copy-F" in store.boards()
    assert (store.port(legacy["id"])["x"], store.port(legacy["id"])["y"]) == pytest.approx((0, 14 * TRIANGLE_HEIGHT))
    assert store.port(legacy["id"])["sample_id"] == sample_id
    assert json.loads(store.port(legacy["id"])["devices_json"])[0]["id"] == device
    assert (store.port("legacy-1")["x"], store.port("legacy-1")["y"]) != (store.port("legacy-2")["x"], store.port("legacy-2")["y"])
    backups = list(tmp_path.glob("plates.pre-grid-*.qfridge"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("SELECT x,y FROM ports WHERE id=?", (legacy["id"],)).fetchone() == (0.123456, 12.345678)
        assert db.execute("SELECT x,y FROM ports WHERE id='legacy-1'").fetchone() == (0.1, 0.1)
        assert db.execute("SELECT sample_id FROM assignments WHERE port_id=?", (legacy["id"],)).fetchone()[0] == sample_id
    store.close()
    store = FridgeStore.open(path); store.close()
    assert len(list(tmp_path.glob("plates.pre-grid-*.qfridge"))) == 1
    app.processEvents()


def test_grid_change_clears_older_undo_and_new_undo_still_works(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / "grid.qfridge", "格子")
    store.create_board("G", "empty_square")
    panel = FridgeWindow(store); panel.show(); panel.open_board("G"); app.processEvents()
    ident = store.save_port(None, "G", "1", 0, 0, "XY/Z")
    store.save_port(ident, "G", "1", 0, 1, "XY/Z")
    assert panel.history.steps
    panel.grid_choice.setCurrentIndex(panel.grid_choice.findData("triangle")); app.processEvents()
    assert store.board_grid("G") == "triangle" and not panel.history.steps
    assert (store.port(ident)["x"], store.port(ident)["y"]) == pytest.approx((-0.5, TRIANGLE_HEIGHT))
    store.save_port(ident, "G", "1", 3, 0, "XY/Z")
    panel.undo()
    assert store.board_grid("G") == "triangle"
    assert (store.port(ident)["x"], store.port(ident)["y"]) == pytest.approx((-0.5, TRIANGLE_HEIGHT))
    panel.redo()
    assert (store.port(ident)["x"], store.port(ident)["y"]) == (3, 0)
    panel.close(); app.processEvents()


def test_new_plate_and_grid_placement_in_ui(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / "placement.qfridge", "盘位")
    panel = FridgeWindow(store); panel.resize(1200, 800); panel.show(); app.processEvents()
    headings = {item.text() for item in panel.scene.items() if hasattr(item, "text") and item.text().endswith(" 盘")}
    assert {f"{board} 盘" for board in "ABCDEF"} <= headings
    cards = [item.rect() for item in panel.scene.items() if isinstance(item, QGraphicsRectItem)]
    assert len(cards) == 6 and {(r.width(), r.height()) for r in cards} == {(560, 590)}
    monkeypatch.setattr(BoardDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    panel.add_board(); app.processEvents()
    assert panel.board.currentData() == "G" and len([p for p in store.ports() if p["board"] == "G"]) == 54
    store.create_board("H", "empty_square"); panel.refresh_filters(); panel.open_board("H")
    app.processEvents(); panel.fit_map(); app.processEvents()
    monkeypatch.setattr(PortDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    panel.add_port(); assert panel.placing_port
    click = panel.graphics.mapFromScene(QPointF(2.2 * 58, 1.8 * 58))
    QTest.mouseClick(panel.graphics.viewport(), Qt.MouseButton.LeftButton, pos=click)
    app.processEvents()
    created = [p for p in store.ports() if p["board"] == "H"]
    assert len(created) == 1 and (created[0]["x"], created[0]["y"]) == (2, 2)
    assert created[0]["number"] == "1" and not panel.placing_port
    item = next(i for i in panel.scene.items() if hasattr(i, "port") and i.port["id"] == created[0]["id"])
    start = panel.graphics.mapFromScene(item.scenePos())
    end = panel.graphics.mapFromScene(QPointF(4.2 * 58, 3.1 * 58))
    QTest.mousePress(panel.graphics.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(panel.graphics.viewport(), end, delay=20)
    QTest.mouseRelease(panel.graphics.viewport(), Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()
    assert (store.port(created[0]["id"])["x"], store.port(created[0]["id"])["y"]) == (4, 3)
    panel.undo()
    assert (store.port(created[0]["id"])["x"], store.port(created[0]["id"])["y"]) == (2, 2)
    panel.add_port(); assert panel.placing_port
    QTest.keyClick(panel.graphics.viewport(), Qt.Key.Key_Escape); app.processEvents()
    assert not panel.placing_port
    with store.db:
        store.db.execute("UPDATE ports SET x=?,y=? WHERE id=?", (2.12, 2.18, created[0]["id"]))
    panel.refresh(); app.processEvents()
    item = next(i for i in panel.scene.items() if hasattr(i, "port") and i.port["id"] == created[0]["id"])
    point = panel.graphics.mapFromScene(item.scenePos())
    QTest.mousePress(panel.graphics.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(panel.graphics.viewport(), point + QPointF(1, 0).toPoint())
    QTest.mouseRelease(panel.graphics.viewport(), Qt.MouseButton.LeftButton, pos=point + QPointF(1, 0).toPoint())
    app.processEvents()
    assert (store.port(created[0]["id"])["x"], store.port(created[0]["id"])["y"]) == (2.12, 2.18)
    panel.add_port(); panel.undo()
    assert not panel.placing_port
    panel.close(); app.processEvents()

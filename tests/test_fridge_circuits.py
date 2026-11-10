import csv
import json
import sqlite3
from contextlib import closing

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QTableWidget

from qubit_sequencer.fridge_circuit_ui import CircuitDialog, PresetDialog
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
        assert mc[2]['mark'] == 'IR'
    for n in (5, 25, 45):
        assert preset_for('C', n)['components'][1]['value'] == 0
    for n in range(57, 62):
        mc = [x['value'] for x in preset_for('C', n)['components'] if x['stage'] == 'MC' and x['value'] is not None]
        assert mc == ([0, 20, 10] if n < 61 else [0, 10])
    marker = next(x for x in preset_for('C', 37)['components'] if x['mark'] == '500M')
    assert marker['value'] is None and marker['unit'] == ''
    assert preset_for('F', 'O4')['switch'] == ['1', '2', '3', '4', '5', '6']
    assert preset_for('D', 'O1')['components'][-1]['mark'] == 'I1'
    for board, n in [('A', 42), ('B', 21), ('B', 26), ('B', 45), ('B', 46)]:
        assert preset_for(board, n) is None


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
        db.execute('DROP TABLE port_circuits'); db.execute('DROP TABLE branch_assignments')
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
    assert 'existing' in editor.stage_labels['MC'].text()
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

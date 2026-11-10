import csv
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsTextItem

from qubit_sequencer import gui, sample_box_ui
from qubit_sequencer.exports import build_tables
from qubit_sequencer.fridge_store import FridgeStore
from qubit_sequencer.fridge_ui import AssignmentDialog
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.undo import EditHistory


def test_sample_box_template_roles_and_history(tmp_path):
    path = tmp_path / 'chip.qchip'
    with ChipStore.create(path, '芯片') as chip:
        qubit = chip.add_device('qubit', 'fixed_transmon', 'q_{1}', 0, 0)
        bus = chip.add_device('readout', 'readout_line', 'r_{1}', 1, 1)
        with chip.db:
            chip.db.execute('DROP TABLE sample_box_bindings')
            chip.db.execute('DROP TABLE sample_box_ports')
            chip.db.execute('PRAGMA user_version=8')
    with ChipStore.open(path) as chip:
        assert (tmp_path / 'chip.pre-v8.qchip').is_file()
        assert chip.sample_box_ports() == []
        history = EditHistory(); history.bind(chip.db); chip.undo_history = history
        chip.enable_sample_box()
        ports = {p['number']: p for p in chip.sample_box_ports()}
        assert set(ports) == {str(i) for i in range(1, 101)}
        assert (ports['53']['x'], ports['53']['y'], ports['53']['source']) == (1, 1, 'photo')
        assert (ports['3']['x'], ports['3']['y'], ports['3']['source']) == (10, 10, 'inferred')
        history.undo(); assert chip.sample_box_ports() == []
        history.redo(); assert len(chip.sample_box_ports()) == 100
        chip.save_sample_box_port(ports['3']['id'], 'P3', True, qubit, signal_name='Q1')
        chip.save_sample_box_port(ports['1']['id'], '1', False, bus, 'read_in', 'R1IN')
        chip.save_sample_box_port(ports['2']['id'], '2', False, bus, 'read_out', 'R1OUT')
        for device, role in ((qubit, 'single'), (bus, 'read_in')):
            with pytest.raises(ValueError, match='已登记'):
                chip.save_sample_box_port(ports['4']['id'], '4', False, device, role)
        with pytest.raises(ValueError, match='已存在'):
            chip.save_sample_box_port(ports['4']['id'], 'P3', False)
        chip.delete_device(qubit)
        assert not any(p['device_id'] == qubit for p in chip.sample_box_ports())
        history.undo()
        assert chip.sample_box_summary(bus) == '样品盒接口：1 读入、2 读出'
        assert ['P3', '对称推断（已核对）', 'Q1', 'q_{1}', ''] in build_tables(chip)['样品盒接口'][1]
        history.detach()
    with ChipStore.open(path) as chip:
        assert chip.sample_box_summary(qubit) == '样品盒接口：P3'
        assert chip.validate() == []


def test_replace_sample_box_preserves_original_bindings_on_undo(tmp_path):
    path = tmp_path / 'chip.qchip'
    with ChipStore.create(path, '芯片') as chip:
        qubit = chip.add_device('qubit', 'fixed_transmon', 'q_{1}', 0, 0)
        history = EditHistory()
        history.bind(chip.db)
        chip.undo_history = history
        chip.enable_sample_box()
        old = next(port for port in chip.sample_box_ports() if port['number'] == '14')
        chip.save_sample_box_port(old['id'], '14', True, qubit)

        chip.enable_sample_box('lab-56', replace=True)
        ports = {port['number']: port for port in chip.sample_box_ports()}
        assert set(ports) == {str(number) for number in range(1, 57)}
        assert (ports['14']['x'], ports['14']['y']) == (0, 0)
        assert (ports['42']['x'], ports['42']['y']) == (7, 7)
        assert ports['2']['source'] == 'inferred'
        assert ports['14']['device_id'] is None and ports['14']['id'] != old['id']

        history.undo()
        assert chip.sample_box_template() == 'lab-100'
        restored = next(port for port in chip.sample_box_ports() if port['number'] == '14')
        assert (restored['id'], restored['device_id']) == (old['id'], qubit)
        history.redo()
        assert chip.sample_box_template() == 'lab-56'
        assert chip.sample_box_ports()[0]['id'] == ports['14']['id']
        history.detach()
    with ChipStore.open(path) as chip:
        assert len(chip.sample_box_ports()) == 56
        assert chip.sample_box_appearance()['drawing']['pitch'] == 58


def test_external_sample_box_templates_load_on_startup(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    sample = tmp_path / 'chip.qchip'
    folder = tmp_path / 'templates'
    monkeypatch.setattr(sample_box_ui, 'sample_box_template_directory', lambda: folder)
    with ChipStore.create(sample, '芯片'):
        pass

    window = gui.MainWindow(str(sample))
    try:
        panel = window.sample_box_panel
        window.tabs.setCurrentWidget(panel)
        combo = panel.template_combo
        assert {combo.itemData(i) for i in range(combo.count())} == {'lab-100', 'lab-56'}
        combo.setCurrentIndex(combo.findData('lab-56'))
        panel.enable_button.click()
        assert len(panel.items) == 56
        title = panel.title.text()
        ids = set(panel.items)

        folder.mkdir()
        for key, name, rows in (
            ('lab-56', '自定义 56 芯', [[1, 2]]),
            ('custom', '自定义样品盒', [['A', 0, 'B']]),
            ('invalid', '重复接口', [[1, '1']]),
        ):
            (folder / f'{key}.json').write_text(json.dumps({'id': key, 'name': name, 'rows': rows}), encoding='utf-8')
        assert combo.findData('custom') == -1
    finally:
        window.close()
        app.processEvents()

    window = gui.MainWindow(str(sample))
    try:
        panel = window.sample_box_panel
        window.tabs.setCurrentWidget(panel)
        combo = panel.template_combo
        assert combo.itemText(combo.findData('lab-56')) == '自定义 56 芯'
        assert combo.findData('custom') >= 0
        assert combo.findData('invalid') == -1
        assert panel.title.text() == title and set(panel.items) == ids

        other = tmp_path / 'other.qchip'
        with ChipStore.create(other, '另一样品'):
            pass
        window._open_path(str(other))
        window.tabs.setCurrentWidget(panel)
        combo.setCurrentIndex(combo.findData('custom'))
        panel.enable_button.click()
        assert {port['number'] for port in panel.ports.values()} == {'A', 'B'}
        assert panel.title.text() == '自定义样品盒'
    finally:
        window.close()
        app.processEvents()


def test_fridge_binding_migration_conflicts_and_bundle(tmp_path):
    sample = tmp_path / 'sample.qchip'
    with ChipStore.create(sample, '样品') as chip:
        q = chip.add_device('qubit', 'fixed_transmon', 'q_{1}', 0, 0)
        chip.enable_sample_box()
        box = next(p['id'] for p in chip.sample_box_ports() if p['number'] == '3')
        chip.save_sample_box_port(box, '3', False, q, signal_name='Q1')
    project = tmp_path / 'fridge.qfridge'
    with closing(FridgeStore.create(project, '制冷机')) as fridge:
        aid = fridge.add_sample(sample)
        direct, switch = [p['id'] for p in fridge.ports()[:2]]
        fridge.assign(direct, aid, '旧引脚', '旧图纸', [q], '旧备注', 'Z')
    with closing(sqlite3.connect(project)) as db, db:
        db.execute('ALTER TABLE assignments DROP COLUMN box_port_id')
        db.execute('ALTER TABLE branch_assignments DROP COLUMN box_port_id')
        db.execute('PRAGMA user_version=2')
    with closing(FridgeStore.open(project)) as fridge:
        assert (tmp_path / 'fridge.pre-v2.qfridge').is_file()
        assert fridge.port(direct)['box_port_id'] is None
        history = EditHistory(); history.bind(fridge.db); fridge.undo_history = history
        fridge.assign(direct, aid, drawing='旧图纸', notes='旧备注', usage='Z', box_port_id=box)
        assert fridge.port(direct)['pin'] == '旧引脚'
        history.undo(); assert fridge.port(direct)['box_port_id'] is None
        history.redo(); assert fridge.port(direct)['box_port_id'] == box
        fridge.save_circuit(switch, {'switch': ['1', '2']})
        with pytest.raises(ValueError, match='请先解除分配'):
            fridge.assign(switch, aid, branch='1', box_port_id=box)
        fridge.unassign(direct); fridge.assign(switch, aid, branch='1', box_port_id=box)
        with pytest.raises(ValueError, match='分支 1'):
            fridge.assign(direct, aid, box_port_id=box)
        with ChipStore.open(sample) as chip:
            chip.update_device(q, label='控制比特', drawing_number='Z-1')
            chip.save_sample_box_port(box, 'P3', True, q, signal_name='Q1')
        assert fridge.resolve_assignment(fridge.branch_assignments(switch)[0])[2][0]['label'] == '控制比特'
        csv_file = tmp_path / 'fridge.csv'; fridge.export_table(csv_file)
        with csv_file.open(encoding='utf-8-sig', newline='') as stream:
            mapped = next(r for r in csv.DictReader(stream) if r['样品盒接口'] == 'P3')
        assert (mapped['接口编号来源'], mapped['接口信号'], mapped['器件'], mapped['连接状态']) == (
            '对称推断（已核对）', 'Q1', '控制比特', '已连接')
        archive = tmp_path / 'bundle.zip'; fridge.export_bundle(archive)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(tmp_path / 'bundle')
        with closing(FridgeStore.open(tmp_path / 'bundle' / 'project.qfridge')) as copied:
            assert copied.resolve_assignment(copied.branch_assignments(switch)[0])[0]['number'] == 'P3'
        with ChipStore.open(sample) as chip, chip.db:
            chip.db.execute('DELETE FROM sample_box_ports WHERE id=?', (box,))
        assert fridge.resolve_assignment(fridge.branch_assignments(switch)[0]) == (None, '样品盒接口不可用', [])
        with pytest.raises(ValueError, match='不包含已关联的样品盒接口'):
            fridge.relocate_sample(aid, sample)
        history.detach()


def test_box_workbench_search_and_locate(tmp_path):
    app = QApplication.instance() or QApplication([])
    sample = tmp_path / 'box.qchip'; project = tmp_path / 'box.qfridge'
    with ChipStore.create(sample, '样品') as chip:
        q = chip.add_device('qubit', 'fixed_transmon', 'q_{1}', 0, 0)
        chip.enable_sample_box()
        box = next(p['id'] for p in chip.sample_box_ports() if p['number'] == '3')
        chip.save_sample_box_port(box, '3', False, q, signal_name='Q1')
    with closing(FridgeStore.create(project, '制冷机')) as fridge:
        aid = fridge.add_sample(sample)
        port = fridge.ports()[0]['id']
        fridge.assign(port, aid, box_port_id=box)
    window = gui.MainWindow(str(sample)); window.show(); app.processEvents()
    try:
        window._open_fridge_path(str(project))
        panel = window.sample_box_panel
        window.tabs.setCurrentWidget(panel); app.processEvents()
        panel.search.setText('Q1')
        assert panel.selected == box and panel.items[box].isSelected()
        assert '对称推断' in panel.info.text() and '制冷机：' in panel.info.text()
        panel.trace_button.click()
        fridge = window.fridge_panel
        assert window.tabs.currentWidget() is window.fridge_page and fridge.selected == port
        dialog = AssignmentDialog(window, fridge.store, fridge.store.port(port))
        assert dialog.mode.currentData() == 'box' and dialog.box_port.currentData() == box
        assert '对称推断' in dialog.box_detail.text()
        dialog.close()
    finally:
        window.close(); app.processEvents()


def test_custom_device_can_be_placed_edited_and_wired(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    sample = tmp_path / 'chip.qchip'
    project = tmp_path / 'fridge.qfridge'
    with ChipStore.create(sample, '芯片') as chip:
        chip.enable_sample_box()
        box_port = chip.sample_box_ports()[0]['id']

    def fill_device(dialog):
        assert dialog.name.text() == 'u_{1}'
        dialog.subtype.setEditText('测试电容')
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(gui.CustomDeviceDialog, 'exec', fill_device)
    window = gui.MainWindow(str(sample))
    try:
        window.place_custom_action.trigger()
        window.on_blank_clicked(1.48, 2.02)
        device = window.store.devices('custom')[0]
        ident = device['id']
        assert (device['subtype'], device['x'], device['y']) == ('测试电容', 1.5, 2)
        assert '测试电容' in window.scene._device_items[ident].toolTip()
        window.detail_editor.subtype.setEditText('直通线')
        window.save_device()
        assert window.store.device(ident)['subtype'] == '直通线'
        load = window.store.add_device('custom', 'R_in < 50', None, 2.5, 2)
        same = window.store.add_device('custom', '直通线', None, 3.5, 2)
        window.refresh_content()
        assert not any(item.data(0) in [('device', key) for key in (ident, load, same)]
                       for item in window.scene.items() if isinstance(item, QGraphicsTextItem))
        legend = [item.toPlainText() for item in window.scene._legend_items]
        assert '⬢ 直通线 · u1、u3' in legend and '⬢ R_in < 50 · u2' in legend
        colors = {key: window.scene._device_items[key].brush().color() for key in (ident, load, same)}
        assert colors[ident] == colors[same] != colors[load]
        assert QColor(dict(window.scene.legend_entries)['⬢ 直通线 · u_{1}、u_{3}']) == colors[ident]

        image_path = tmp_path / 'layout.png'
        image = QImage(120, 120, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.lightGray)
        assert image.save(str(image_path))
        window.store.set_chip_design(image_path.name, image_path.read_bytes())
        window.store.set_design_marker(ident, .3, .4)
        window.design_panel.reload_image()
        window.design_panel.refresh_markers()
        marker = window.design_panel.view.markers[ident]
        assert marker.document.toPlainText() == 'u1\n直通线'
        assert marker.color == colors[ident]

        panel = window.sample_box_panel
        window.tabs.setCurrentWidget(panel)
        port = panel.ports[box_port]
        box_dialog = sample_box_ui.BoxPortDialog(window, port, window.store.devices())
        assert '直通线' in box_dialog.device.itemText(box_dialog.device.findData(ident))
        window.store.save_sample_box_port(box_port, port['number'], False, ident)
        panel.refresh()
        assert panel.ports[box_port]['device_label'] == 'u_{1}'
        assert panel.items[box_port].brush().color() == colors[ident]

        with closing(FridgeStore.create(project, '制冷机')) as fridge:
            sample_id = fridge.add_sample(sample)
            direct, through_box = [port['id'] for port in fridge.ports()[:2]]
            dialog = AssignmentDialog(window, fridge, fridge.port(direct))
            dialog.mode.setCurrentIndex(dialog.mode.findData('direct'))
            item = dialog.devices.item(0)
            assert '自定义元件（直通线）' in item.text()
            item.setCheckState(Qt.CheckState.Checked)
            fridge.assign(direct, sample_id, devices=dialog.device_ids())
            fridge.assign(through_box, sample_id, box_port_id=box_port)
    finally:
        window.close(); app.processEvents()

    with closing(FridgeStore.open(project)) as fridge:
        for port_id in (direct, through_box):
            device = fridge.resolve_assignment(fridge.port(port_id))[2][0]
            assert (device['id'], device['subtype']) == (ident, '直通线')


def test_box_canvas_displays_linked_device_names(tmp_path):
    from PySide6.QtWidgets import QGraphicsSimpleTextItem
    app = QApplication.instance() or QApplication([])
    sample = tmp_path / 'labels.qchip'
    with ChipStore.create(sample, '样品') as chip:
        device = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        chip.enable_sample_box()
        port = chip.sample_box_ports()[0]['id']
        chip.save_sample_box_port(port, chip.sample_box_ports()[0]['number'], True, device)
    window = gui.MainWindow(str(sample)); window.show(); app.processEvents()
    panel = window.sample_box_panel; window.tabs.setCurrentWidget(panel); panel.refresh(); app.processEvents()
    def labels():
        return [item.text() for item in panel.items[port].childItems() if isinstance(item, QGraphicsSimpleTextItem)]
    assert 'Q1' in labels()
    assert 'Q1' in panel.items[port].toolTip()
    panel.show_names.setChecked(False); assert 'Q1' not in labels()
    panel.show_names.setChecked(True)
    window.store.update_device(device, label='Q2'); panel.refresh(); assert 'Q2' in labels()
    window.close(); app.processEvents()


def test_sample_box_rotate_view_preserves_connections_and_text(tmp_path):
    from PySide6.QtWidgets import QGraphicsSimpleTextItem
    app = QApplication.instance() or QApplication([])
    sample = tmp_path / 'rotate.qchip'
    with ChipStore.create(sample, '样品') as chip:
        device = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        chip.enable_sample_box()
        port = chip.sample_box_ports()[0]
        chip.save_sample_box_port(port['id'], port['number'], True, device)
    window = gui.MainWindow(str(sample)); window.show(); app.processEvents()
    panel = window.sample_box_panel; window.tabs.setCurrentWidget(panel); panel.refresh(); app.processEvents()
    ident = port['id']; original = panel.items[ident].scenePos()
    panel.items[ident].setSelected(True)
    center_vertices = panel.appearance['drawing']['outline']
    cx = (min(v[0] for v in center_vertices) + max(v[0] for v in center_vertices)) / 2
    cy = (min(v[1] for v in center_vertices) + max(v[1] for v in center_vertices)) / 2
    panel.rotate_right.click(); app.processEvents()
    rotated = panel.items[ident].scenePos()
    assert abs(rotated.x() - (cx - (original.y() - cy))) < .001
    assert abs(rotated.y() - (cy + (original.x() - cx))) < .001
    assert panel.items[ident].isSelected()
    assert all(item.sceneTransform().m12() == 0 for item in panel.items[ident].childItems() if isinstance(item, QGraphicsSimpleTextItem))
    assert window.store.sample_box_ports()[0]['device_id'] == device
    panel.rotate_left.click(); app.processEvents()
    assert (panel.items[ident].scenePos() - original).manhattanLength() < .001
    window.close(); app.processEvents()


def test_smart_wiring_live_mapping_and_exports(tmp_path):
    from PySide6.QtWidgets import QGraphicsSimpleTextItem
    app = QApplication.instance() or QApplication([])
    sample = tmp_path / 'chip.qchip'; project = tmp_path / 'fridge.qfridge'
    with ChipStore.create(sample, '样品') as chip:
        q = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        chip.enable_sample_box(); ports = chip.sample_box_ports()
        a, b = ports[0]['id'], ports[1]['id']
        chip.save_sample_box_port(a, ports[0]['number'], True, q)
    with closing(FridgeStore.create(project, 'DR', with_preset=True)) as fridge:
        sid = fridge.add_sample(sample)
        a10 = next(p['id'] for p in fridge.ports() if p['board'] == 'A' and p['number'] == '10')
        f4 = next(p['id'] for p in fridge.ports() if p['board'] == 'F' and p['number'] == 'O4')
        fridge.assign(a10, sid, box_port_id=a); fridge.assign(f4, sid, box_port_id=b, branch='3')
    window = gui.MainWindow(str(sample)); window.show(); app.processEvents()
    panel = window.sample_box_panel; window.tabs.setCurrentWidget(panel); panel.refresh(); panel.smart_wiring.setChecked(True)
    def labels(port_id):
        return [child.text() for child in panel.items[port_id].childItems() if isinstance(child, QGraphicsSimpleTextItem)]
    assert '未知' in labels(a)
    window._open_fridge_path(str(project)); panel.refresh_connections(); app.processEvents()
    assert 'A10' in labels(a) and 'FO4 / 3' in labels(b)
    panel.items[a].setSelected(True)
    for ext in ('png', 'svg', 'pdf'):
        path = tmp_path / f'wiring.{ext}'; panel.export_scene(path)
        assert path.stat().st_size > 500
    assert (tmp_path / 'wiring.pdf').read_bytes().startswith(b'%PDF')
    assert b'<svg' in (tmp_path / 'wiring.svg').read_bytes()[:500]
    assert panel.items[a].isSelected()
    fridge = window.fridge_panel.store
    fridge.unassign(a10); panel.refresh_connections(); assert '未分配' in labels(a)
    window.close(); app.processEvents()

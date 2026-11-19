import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.fridge_store import FridgeStore
from qubit_sequencer.quick_wiring import QuickWiringDialog, diagram_values
from qubit_sequencer.undo import EditHistory


def test_quick_wiring_atomic_undo_and_live_diagram(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'chip.qchip'
    with ChipStore.create(path, 'chip') as chip:
        q1 = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        q2 = chip.add_device('qubit', 'fixed_transmon', 'Q2', 1, 0)
        chip.enable_sample_box()
        boxes = chip.sample_box_ports()
        chip.save_sample_box_port(boxes[0]['id'], boxes[0]['number'], True, q1)
        chip.save_sample_box_port(boxes[1]['id'], boxes[1]['number'], True, q2)
        fridge = FridgeStore.create(tmp_path / 'f.qfridge', 'DR')
        sid = fridge.add_sample(path)
        ports = [p for p in fridge.ports() if p['board'] == 'A' and p['number'] in ('13', '14')]
        hist = EditHistory(); hist.bind(fridge.db); fridge.undo_history = hist
        dialog = QuickWiringDialog(None, chip, fridge, fridge.sample(sid), [q1, q2], [p['id'] for p in ports])
        assert dialog.table.rowCount() == 2
        dialog.accept(); assert dialog.result() == QDialog.DialogCode.Accepted
        assert len(fridge.box_connections(sid)) == 2
        panel = type('Panel', (), {'store': fridge, 'preview': False})()
        values, status = diagram_values(chip, panel)
        assert 'A13' in values['port'][q1] or 'A14' in values['port'][q1]
        hist.undo(); assert not fridge.box_connections(sid)
        hist.redo(); assert len(fridge.box_connections(sid)) == 2
        hist.detach(); fridge.close()
    app.processEvents()


def test_quick_wiring_conflict_rolls_back(tmp_path):
    path = tmp_path / 'chip.qchip'
    with ChipStore.create(path, 'chip') as chip:
        a = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        b = chip.add_device('qubit', 'fixed_transmon', 'Q2', 1, 0)
        chip.enable_sample_box(); boxes = chip.sample_box_ports()
        chip.save_sample_box_port(boxes[0]['id'], boxes[0]['number'], True, a)
        chip.save_sample_box_port(boxes[1]['id'], boxes[1]['number'], True, b)
        boxes = [dict(p) for p in chip.sample_box_ports() if p['device_id']]
        fridge = FridgeStore.create(tmp_path / 'f.qfridge', 'DR'); sid = fridge.add_sample(path)
        ports = [p for p in fridge.ports() if p['board'] == 'A' and p['number'] in ('13', '14')]
        fridge.set_occupied(ports[1]['id'], True)
        with pytest.raises(ValueError):
            fridge.quick_assign(sid, str(path), [(boxes[0], ports[0]['id'], (ports[0]['board'], ports[0]['number'], ports[0]['kind'])), (boxes[1], ports[1]['id'], (ports[1]['board'], ports[1]['number'], ports[1]['kind']))])
        assert not fridge.box_connections(sid)
        fridge.close()


def test_readout_matching_partial_and_switch_type_mismatch(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'readout.qchip'
    with ChipStore.create(path, 'chip') as chip:
        bus = chip.add_device('readout', 'readout_line', 'R1', 0, 0)
        chip.enable_sample_box(); p = chip.sample_box_ports()[0]
        chip.save_sample_box_port(p['id'], p['number'], True, bus, 'read_in')
        fridge = FridgeStore.create(tmp_path / 'f.qfridge', 'DR', with_preset=True); sid = fridge.add_sample(path)
        targets = [p for p in fridge.ports() if (p['board'], p['number']) in [('A', '1'), ('F', 'O4'), ('D', 'O1')]]
        dialog = QuickWiringDialog(None, chip, fridge, fridge.sample(sid), [bus], [p['id'] for p in targets])
        assert dialog.table.rowCount() == 2
        row = next(i for i, (_, box, _) in enumerate(dialog.rows) if box)
        assert dialog.table.cellWidget(row, 3).currentData() == next(p['id'] for p in targets if p['board'] == 'A')
        assert any('缺少读出' in dialog.table.item(i, 4).text() for i in range(dialog.table.rowCount()))
        combo = dialog.table.cellWidget(row, 3)
        switch = next(p['id'] for p in targets if p['board'] == 'F')
        for index in range(1, combo.count()):
            endpoint = combo.itemData(index)
            if isinstance(endpoint, tuple):
                assert endpoint[0] == switch and not combo.model().item(index).isEnabled()
            elif endpoint == next(p['id'] for p in targets if p['board'] == 'D'):
                assert not combo.model().item(index).isEnabled()
        dialog.accept(); assert len(fridge.box_connections(sid)) == 1
        fridge.close()
    app.processEvents()


def test_quick_wiring_manual_switch_endpoints_and_atomic_recheck(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'switch.qchip'
    with ChipStore.create(path, 'chip') as chip:
        first = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        second = chip.add_device('qubit', 'fixed_transmon', 'Q2', 1, 0)
        chip.enable_sample_box()
        for device, box in zip((first, second), chip.sample_box_ports()):
            chip.save_sample_box_port(box['id'], box['number'], True, device)
        fridge = FridgeStore.create(tmp_path / 'f.qfridge', 'DR', with_preset=True)
        sid = fridge.add_sample(path)
        port_id = next(p['id'] for p in fridge.ports() if (p['board'], p['number']) == ('F', 'O4'))
        circuit = fridge.circuit(port_id)
        circuit['merge_switches'] = {'OUT': {'inputs': ['1', '2'], 'component': {'stage': 'MC'}}}
        fridge.save_circuit(port_id, circuit)
        fridge.assign(port_id, sid, 'original', branch='3')
        fridge.set_branch_occupied(port_id, '4', True)
        history = EditHistory()
        history.bind(fridge.db)
        fridge.undo_history = history
        dialog = QuickWiringDialog(None, chip, fridge, fridge.sample(sid), [first, second], [port_id])
        combos = [dialog.table.cellWidget(i, 3) for i in range(2)]
        assert all(not combo.currentData() for combo in combos)
        indices = {combos[0].itemData(i)[1]: i for i in range(1, combos[0].count())}
        assert set(indices) == {'3', '4', '5', '6', 'OUT'}
        assert not combos[0].model().item(indices['3']).isEnabled()
        assert not combos[0].model().item(indices['4']).isEnabled()
        assert '公共输出 OUT' in combos[0].itemText(indices['OUT'])
        combos[0].setCurrentIndex(indices['5'])
        combos[1].setCurrentIndex(indices['5'])
        assert dialog.table.item(0, 4).text() == '目标端口重复'
        combos[1].setCurrentIndex(indices['OUT'])
        assert all(dialog.table.item(i, 0).checkState() == Qt.CheckState.Checked for i in range(2))
        warnings = []
        monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
        fridge.set_branch_occupied(port_id, 'OUT', True)
        dialog.accept()
        assert '已占用' in warnings[-1] and not fridge.box_connections(sid)
        fridge.set_branch_occupied(port_id, 'OUT', False)
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert {row['branch'] for row in fridge.box_connections(sid)} == {'5', 'OUT'}
        history.undo()
        assert not fridge.box_connections(sid) and fridge.branch_assignments(port_id)[0]['branch'] == '3'
        history.redo()
        assert {row['branch'] for row in fridge.box_connections(sid)} == {'5', 'OUT'}
        history.detach()
        fridge.close()
    app.processEvents()


def test_quick_wiring_rejects_changed_port(tmp_path):
    path = tmp_path / 'chip.qchip'
    with ChipStore.create(path, 'chip') as chip:
        q = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        chip.enable_sample_box(); p = chip.sample_box_ports()[0]
        chip.save_sample_box_port(p['id'], p['number'], True, q)
        box = next(dict(p) for p in chip.sample_box_ports() if p['device_id'] == q)
        fridge = FridgeStore.create(tmp_path / 'f.qfridge', 'DR'); sid = fridge.add_sample(path)
        target = next(p for p in fridge.ports() if p['board'] == 'A' and p['number'] == '13')
        snapshot = (target['board'], target['number'], target['kind'])
        fridge.save_port(target['id'], 'A', '999', target['x'], target['y'], target['kind'])
        with pytest.raises(ValueError, match='已变化'):
            fridge.quick_assign(sid, str(path), [(box, target['id'], snapshot)])
        assert not fridge.box_connections(sid)
        fridge.close()


def test_multicolumn_quick_wiring_updates_box_and_layer(tmp_path, monkeypatch):
    from qubit_sequencer import gui
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'chip.qchip'; project = tmp_path / 'f.qfridge'
    with ChipStore.create(path, 'chip') as chip:
        q = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        chip.enable_sample_box(); p = chip.sample_box_ports()[0]
        chip.save_sample_box_port(p['id'], p['number'], True, q, signal_name='X')
        box_id = p['id']
    fridge = FridgeStore.create(project, 'DR'); fridge.add_sample(path); fridge.close()
    window = gui.MainWindow(str(path)); window.show(); window._open_fridge_path(str(project)); app.processEvents()
    window.tabs.set_multicolumn(True); window.tabs.setCurrentWidget(window.topology_page)
    window.tabs.setCurrentWidget(window.fridge_page); app.processEvents()
    window.fridge_panel.open_board('A'); app.processEvents()
    window.scene.select_devices((q,))
    target = next(item for item in window.fridge_panel.scene.items() if hasattr(item, 'port') and item.port['number'] == '13')
    target.setSelected(True); app.processEvents()
    def run(dialog):
        dialog.accept(); return dialog.result()
    monkeypatch.setattr(QuickWiringDialog, 'exec', run)
    window.quick_wiring(); app.processEvents()
    assert len(window.fridge_panel.store.box_connections(window.fridge_panel.store.samples()[0]['id'])) == 1
    window.field_combo.setCurrentIndex(window.field_combo.findData('wiring.port')); app.processEvents()
    assert any('A13' in item.toPlainText() for item in window.scene.items() if hasattr(item, 'toPlainText'))
    from qubit_sequencer.diagram import export_diagram
    values, status = diagram_values(window.store, window.fridge_panel)
    image = tmp_path / 'wiring.svg'
    export_diagram(window.store, image, 'wiring.port', fridge_values=values, fridge_status=status)
    assert image.stat().st_size > 1000
    window.sample_box_panel.refresh_connections()
    assert window.sample_box_panel.connections[box_id]['number'] == '13'
    window.fridge_panel.undo(); app.processEvents()
    assert not any('A13' in item.toPlainText() for item in window.scene.items() if hasattr(item, 'toPlainText'))
    window.fridge_panel.redo(); app.processEvents()
    assert any('A13' in item.toPlainText() for item in window.scene.items() if hasattr(item, 'toPlainText'))
    window.close(); app.processEvents()


def test_sample_box_layers_include_both_readout_roles(tmp_path):
    from qubit_sequencer.diagram import ChipScene
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'box.qchip'
    with ChipStore.create(path, 'chip') as chip:
        bus = chip.add_device('readout', 'readout_line', 'R1', 0, 0)
        chip.enable_sample_box(); boxes = chip.sample_box_ports()
        chip.save_sample_box_port(boxes[0]['id'], boxes[0]['number'], True, bus, 'read_in', 'IN')
        chip.save_sample_box_port(boxes[1]['id'], boxes[1]['number'], True, bus, 'read_out', 'OUT')
        for field, terms in [('sample_box.number', ('读入', '读出')), ('sample_box.signal', ('IN', 'OUT'))]:
            scene = ChipScene(chip, field)
            labels = ' '.join(item.toPlainText() for item in scene.items() if hasattr(item, 'toPlainText'))
            assert all(term in labels for term in terms)
    app.processEvents()


def test_quick_wiring_uses_click_order_without_reordering(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from qubit_sequencer import gui
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'order.qchip'; project = tmp_path / 'order.qfridge'
    with ChipStore.create(path, 'chip') as chip:
        q1 = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        q2 = chip.add_device('qubit', 'fixed_transmon', 'Q2', 2, 0)
        chip.enable_sample_box(); boxes = chip.sample_box_ports()
        chip.save_sample_box_port(boxes[0]['id'], boxes[0]['number'], True, q1)
        chip.save_sample_box_port(boxes[1]['id'], boxes[1]['number'], True, q2)
    fridge = FridgeStore.create(project, 'DR'); fridge.add_sample(path); fridge.close()
    window = gui.MainWindow(str(path)); window.show(); window._open_fridge_path(str(project)); app.processEvents()
    window.tabs.set_multicolumn(True); window.tabs.setCurrentWidget(window.topology_page); window.tabs.setCurrentWidget(window.fridge_page)
    panel = window.fridge_panel; panel.open_board('A'); app.processEvents()
    for ident in (q2, q1):
        point = window.view.mapFromScene(window.scene._device_items[ident].sceneBoundingRect().center())
        QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, point); app.processEvents()
    assert window.topology_device_selection() == (q2, q1)
    targets = [next(i for i in panel.scene.items() if hasattr(i, 'port') and i.port['number'] == n) for n in ('14', '13')]
    for item in targets:
        QTest.mouseClick(panel.graphics.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                         panel.graphics.mapFromScene(item.scenePos())); app.processEvents()
    assert panel.selected_port_ids() == [i.port['id'] for i in targets]
    dialog = QuickWiringDialog(window, window.store, panel.store, panel.store.samples()[0], window.topology_device_selection(), panel.selected_port_ids())
    assert [row[0] for row in dialog.rows] == ['Q2', 'Q1']
    assert [dialog.table.cellWidget(i, 3).currentData() for i in range(2)] == [i.port['id'] for i in targets]
    dialog.accept(); assert dialog.result() == QDialog.DialogCode.Accepted
    panel.refresh(); window.refresh_scene()
    assert window.topology_device_selection() == (q2, q1)
    assert panel.selected_port_ids() == [i.port['id'] for i in targets]
    window.close(); app.processEvents()


def test_incompatible_port_remains_available_for_later_interface(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'read.qchip'
    with ChipStore.create(path, 'chip') as chip:
        bus = chip.add_device('readout', 'readout_line', 'R', 0, 0)
        q = chip.add_device('qubit', 'fixed_transmon', 'Q', 2, 0)
        chip.enable_sample_box(); boxes = chip.sample_box_ports()
        chip.save_sample_box_port(boxes[0]['id'], boxes[0]['number'], True, bus, 'read_in')
        chip.save_sample_box_port(boxes[1]['id'], boxes[1]['number'], True, q)
        fridge = FridgeStore.create(tmp_path / 'f.qfridge', 'DR'); sid = fridge.add_sample(path)
        output = next(p for p in fridge.ports() if p['board'] == 'D' and p['number'] == 'O1')
        xy = next(p for p in fridge.ports() if p['board'] == 'A' and p['number'] == '13')
        dialog = QuickWiringDialog(None, chip, fridge, fridge.sample(sid), [bus, q], [output['id'], xy['id']])
        assert not dialog.table.cellWidget(0, 3).currentData()
        qrow = next(i for i, row in enumerate(dialog.rows) if row[0] == 'Q')
        assert dialog.table.cellWidget(qrow, 3).currentData() == output['id']
        fridge.close()
    app.processEvents()


def test_already_connected_readin_does_not_claim_readout_port(tmp_path):
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'read.qchip'
    with ChipStore.create(path, 'chip') as chip:
        bus = chip.add_device('readout', 'readout_line', 'R', 0, 0)
        chip.enable_sample_box(); boxes = chip.sample_box_ports()
        chip.save_sample_box_port(boxes[0]['id'], boxes[0]['number'], True, bus, 'read_in')
        chip.save_sample_box_port(boxes[1]['id'], boxes[1]['number'], True, bus, 'read_out')
        fridge = FridgeStore.create(tmp_path / 'f.qfridge', 'DR'); sid = fridge.add_sample(path)
        source = next(p for p in fridge.ports() if p['board'] == 'A' and p['number'] == '1')
        target = next(p for p in fridge.ports() if p['board'] == 'E' and p['number'] == 'O3')
        fridge.assign(source['id'], sid, box_port_id=boxes[0]['id'])
        dialog = QuickWiringDialog(None, chip, fridge, fridge.sample(sid), [bus], [target['id']])
        assert dialog.table.item(0, 4).text() == '样品盒接口已有连接'
        assert not dialog.table.cellWidget(0, 3).currentData()
        assert dialog.table.cellWidget(1, 3).currentData() == target['id']
        assert dialog.table.item(1, 0).checkState() == Qt.CheckState.Checked
        dialog.accept(); assert dialog.result() == QDialog.DialogCode.Accepted
        assert {row['id'] for row in fridge.box_connections(sid)} == {source['id'], target['id']}
        fridge.close()
    app.processEvents()

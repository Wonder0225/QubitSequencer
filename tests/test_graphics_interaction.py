from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsScene, QMessageBox

from qubit_sequencer import gui
from qubit_sequencer.diagram import CELL
from qubit_sequencer.fridge_ui import PortView
from qubit_sequencer.storage import ChipStore


def drag(view, start, end, button):
    viewport = view.viewport()
    QTest.mousePress(viewport, button, pos=start)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
                        Qt.MouseButton.NoButton, button, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, event)
    QTest.mouseRelease(viewport, button, pos=end)
    QApplication.processEvents()


def test_plate_canvas_rubber_band_and_right_pan():
    app = QApplication.instance() or QApplication([])
    view = PortView(); scene = QGraphicsScene(); view.setScene(scene)
    scene.setSceneRect(-1000, -1000, 2000, 2000)
    for x in (-30, 30):
        item = scene.addEllipse(x - 10, -10, 20, 20)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
    view.resize(500, 400); view.show(); view.centerOn(0, 0); app.processEvents()
    drag(view, view.mapFromScene(QPointF(-60, -30)), view.mapFromScene(QPointF(60, 30)), Qt.MouseButton.LeftButton)
    assert len(scene.selectedItems()) == 2
    center = view.mapToScene(view.viewport().rect().center())
    drag(view, QPoint(300, 250), QPoint(340, 280), Qt.MouseButton.RightButton)
    assert (view.mapToScene(view.viewport().rect().center()) - center).manhattanLength() > 10
    assert len(scene.selectedItems()) == 2
    view.close()


def test_chip_rubber_band_selects_devices(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'chip.qchip'
    with ChipStore.create(path, 'chip') as chip:
        a = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        b = chip.add_device('qubit', 'fixed_transmon', 'Q2', 2, 0)
    window = gui.MainWindow(); window.show(); window._open_path(str(path)); app.processEvents()
    scene = window.scene; view = window.view
    bounds = scene._device_items[a].sceneBoundingRect().united(scene._device_items[b].sceneBoundingRect()).adjusted(-20, -20, 20, 20)
    drag(view, view.mapFromScene(bounds.topLeft()), view.mapFromScene(bounds.bottomRight()), Qt.MouseButton.LeftButton)
    assert {item.data(0)[1] for item in scene.selectedItems() if item.data(0)} == {a, b}
    window.close(); app.processEvents()


def test_chip_drags_and_deletes_selected_devices_as_a_group(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'chip.qchip'
    with ChipStore.create(path, 'chip') as chip:
        a = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        b = chip.add_device('qubit', 'fixed_transmon', 'Q2', 1, 0)
        other = chip.add_device('qubit', 'fixed_transmon', 'Q3', 3, 0)
        coupling = chip.add_coupling(a, b)
    window = gui.MainWindow(str(path)); window.show(); app.processEvents()
    errors = []
    monkeypatch.setattr(window, '_error', lambda title, exc: errors.append((title, str(exc))))
    try:
        view = window.view
        bounds = window.scene._device_items[a].sceneBoundingRect().united(window.scene._device_items[b].sceneBoundingRect())
        bounds = bounds.adjusted(-20, -20, 20, 20)
        drag(view, view.mapFromScene(bounds.topLeft()), view.mapFromScene(bounds.bottomRight()), Qt.MouseButton.LeftButton)
        assert set(window.topology_device_selection()) == {a, b}

        def move_one_grid():
            center = window.scene._device_items[a].sceneBoundingRect().center()
            drag(view, view.mapFromScene(center), view.mapFromScene(center + QPointF(CELL, 0)), Qt.MouseButton.LeftButton)

        move_one_grid()
        assert [window.store.device(ident)['x'] for ident in (a, b, other)] == [1, 2, 3]
        assert set(window.topology_device_selection()) == {a, b}
        assert len(window.undo_history.steps) == 1
        move_one_grid()
        assert errors == [('移动失败', '目标位置已有比特')]
        assert [window.store.device(ident)['x'] for ident in (a, b, other)] == [1, 2, 3]
        assert len(window.undo_history.steps) == 1
        window.undo_edit()
        assert [window.store.device(ident)['x'] for ident in (a, b)] == [0, 1]
        window.redo_edit()
        assert [window.store.device(ident)['x'] for ident in (a, b)] == [1, 2]

        point = view.mapFromScene(window.scene._device_items[a].sceneBoundingRect().center())
        context = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, view.viewport().mapToGlobal(point))
        app.sendEvent(view.viewport(), context)
        assert window.detail_menu.isVisible()
        assert window.delete_action.text() == '删除所选 2 个元件'
        window.detail_menu.close()
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Delete)
        app.processEvents()
        assert [device['id'] for device in window.store.devices()] == [other]
        assert window.store.couplings() == []
        window.undo_edit()
        assert window.store.coupling(coupling)['qubit_a'] in (a, b)
        window.redo_edit()
        assert [device['id'] for device in window.store.devices()] == [other]
    finally:
        window.close(); app.processEvents()


def test_chip_group_move_preserves_filter_anchor(tmp_path):
    with ChipStore.create(tmp_path / 'chip.qchip', 'chip') as chip:
        readout = chip.add_device('readout', 'readout_line', 'R1', 2, 0, readout_endpoints=((0, 0), (4, 0)))
        tunable_filter = chip.add_device('filter', 'tunable_filter', 'F1', 1, 0)
        link = chip.add_readout_link(readout, tunable_filter, position=.25)
        second_filter = chip.add_device('filter', 'tunable_filter', 'F2', 2, 0)
        second_link = chip.add_readout_link(readout, second_filter, position=.5)
        chip.move_devices((readout, tunable_filter), 2, 1)
        assert chip.readout_link(link)['position'] == .25
        assert tuple(chip.device(tunable_filter)[axis] for axis in ('x', 'y')) == (3, 1)
        assert tuple(chip.device(second_filter)[axis] for axis in ('x', 'y')) == (4, 1)
        chip.move_devices((tunable_filter, second_filter), 1, 1)
        assert chip.readout_link(link)['position'] == .5
        assert chip.readout_link(second_link)['position'] == .75
        assert tuple(chip.device(tunable_filter)[axis] for axis in ('x', 'y')) == (4, 1)


def test_design_marker_selection_and_pan():
    from PySide6.QtGui import QImage
    from qubit_sequencer.design_ui import DesignView
    app = QApplication.instance() or QApplication([])
    view = DesignView(); image = QImage(400, 300, QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.white)
    view.set_image(image); view.sync_markers([
        {'device_id': 'a', 'kind': 'qubit', 'label': 'Q1', 'x': .3, 'y': .5},
        {'device_id': 'b', 'kind': 'qubit', 'label': 'Q2', 'x': .7, 'y': .5}])
    view.resize(600, 450); view.show(); app.processEvents()
    bounds = view.markers['a'].sceneBoundingRect().united(view.markers['b'].sceneBoundingRect()).adjusted(-30, -50, 30, 30)
    drag(view, view.mapFromScene(bounds.topLeft()), view.mapFromScene(bounds.bottomRight()), Qt.MouseButton.LeftButton)
    assert {item.device_id for item in view.design_scene.selectedItems()} == {'a', 'b'}
    view.close(); app.processEvents()


def test_sample_box_canvas_batch_selection(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'box.qchip'
    with ChipStore.create(path, 'chip') as chip:
        chip.enable_sample_box()
    window = gui.MainWindow(str(path)); window.show(); app.processEvents()
    panel = window.sample_box_panel; window.tabs.setCurrentWidget(panel); app.processEvents()
    items = sorted(panel.items.values(), key=lambda item: (item.scenePos().y(), item.scenePos().x()))[:2]
    bounds = items[0].sceneBoundingRect().united(items[1].sceneBoundingRect()).adjusted(-3, -3, 3, 3)
    drag(panel.view, panel.view.mapFromScene(bounds.topLeft()), panel.view.mapFromScene(bounds.bottomRight()), Qt.MouseButton.LeftButton)
    assert all(item.isSelected() for item in items)
    window.close(); app.processEvents()


def test_fridge_ctrl_select_batch_status_and_undo(tmp_path):
    from qubit_sequencer.fridge_ui import FridgePanel
    from qubit_sequencer.fridge_store import FridgeStore
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / 'f.qfridge', 'fridge')
    panel = FridgePanel(store); panel.resize(1200, 800); panel.show(); panel.open_board('A'); panel.fit_map(); app.processEvents()
    items = sorted((item for item in panel.scene.items() if hasattr(item, 'port')), key=lambda item: int(item.port['number']))[:3]
    viewport = panel.graphics.viewport()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=panel.graphics.mapFromScene(items[0].scenePos()))
    for item in items[1:]:
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, panel.graphics.mapFromScene(item.scenePos()))
    app.processEvents()
    ids = {item.port['id'] for item in items}
    assert set(panel.selected_port_ids()) == ids
    assert '3' in panel.info.text() and panel.occupied.currentIndex() == -1
    panel.occupied.setCurrentIndex(panel.occupied.findData('damaged')); app.processEvents()
    assert all(store.port(ident)['manual_status'] == 'damaged' for ident in ids)
    assert set(panel.selected_port_ids()) == ids
    panel.undo(); assert all(store.port(ident)['manual_status'] == '' for ident in ids)
    panel.redo(); assert all(store.port(ident)['manual_status'] == 'damaged' for ident in ids)
    panel.close(); app.processEvents()


def test_chip_ctrl_click_multi_select(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'ctrl.qchip'
    with ChipStore.create(path, 'chip') as chip:
        a = chip.add_device('qubit', 'fixed_transmon', 'Q1', 0, 0)
        b = chip.add_device('qubit', 'fixed_transmon', 'Q2', 2, 0)
    window = gui.MainWindow(); window.show(); window._open_path(str(path)); app.processEvents()
    for i, ident in enumerate((a, b)):
        item = window.scene._device_items[ident]
        point = window.view.mapFromScene(item.sceneBoundingRect().center())
        modifier = Qt.KeyboardModifier.ControlModifier if i else Qt.KeyboardModifier.NoModifier
        QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, modifier, point); app.processEvents()
    assert {item.data(0)[1] for item in window.scene.selectedItems() if item.data(0)} == {a, b}
    window.close(); app.processEvents()


def test_batch_status_preserves_allocations(tmp_path):
    from qubit_sequencer.fridge_store import FridgeStore
    from qubit_sequencer.undo import EditHistory
    source = tmp_path / 'chip.qchip'
    with ChipStore.create(source, 'sample') as chip:
        q = chip.add_device('qubit', 'fixed_transmon', 'Q', 0, 0)
    store = FridgeStore.create(tmp_path / 'fridge.qfridge', 'fridge')
    ids = [p['id'] for p in store.ports() if p['board'] == 'A'][:2]
    sid = store.add_sample(source); store.assign(ids[0], sid, devices=[q])
    history = EditHistory(); history.bind(store.db); store.undo_history = history
    store.set_ports_status(ids, 'damaged')
    assert all(store.port(ident)['manual_status'] == 'damaged' for ident in ids)
    assert store.port(ids[0])['sample_id'] == sid
    history.undo(); assert all(not store.port(ident)['manual_status'] for ident in ids)
    history.redo(); store.set_ports_status(ids, '')
    headers, rows = store.table()
    assert store.port(ids[0])['sample_id'] == sid
    row = next(row for ident, branch, row in store.table_entries()[1] if ident == ids[0])
    assert row[headers.index('占用状态')] == '已占用'
    history.detach(); store.close()


def test_fridge_batch_unassign_preserves_protected_branches_and_undo(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from qubit_sequencer.fridge_ui import FridgePanel
    from qubit_sequencer.fridge_store import FridgeStore
    app = QApplication.instance() or QApplication([])
    chip_path = tmp_path / 'chip.qchip'
    with ChipStore.create(chip_path, 'sample') as chip:
        device = chip.add_device('qubit', 'fixed_transmon', 'Q', 0, 0)
    store = FridgeStore.create(tmp_path / 'f.qfridge', 'DR', with_preset=True)
    sid = store.add_sample(chip_path)
    switch = next(p['id'] for p in store.ports() if p['board'] == 'F' and p['number'] == 'O4')
    other = next(p['id'] for p in store.ports() if p['board'] == 'F' and p['number'] == 'O3')
    store.assign(switch, sid, devices=[device], branch='1'); store.assign(switch, sid, devices=[device], branch='2')
    store.assign(other, sid, devices=[device]); store.set_branch_occupied(switch, '2', True)
    panel = FridgePanel(store); panel.show(); panel.open_board('F'); app.processEvents()
    for item in panel.scene.items():
        if hasattr(item, 'port') and item.port['id'] in (switch, other): item.setSelected(True)
    app.processEvents(); assert panel.port_buttons[1].isEnabled()
    prompts = []
    def approve(*args):
        prompts.append(args[2]); return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', approve)
    panel.unassign(); app.processEvents()
    assert '分支 2' in prompts[0] and '跳过' in prompts[0]
    assert store.port(other)['sample_id'] is None
    assert [row['branch'] for row in store.branch_assignments(switch)] == ['2']
    assert store.branch_marks(switch) == {'2'}
    assert store.circuit(switch)['switch'] == ['1', '2', '3', '4', '5', '6']
    panel.undo(); assert store.port(other)['sample_id'] == sid
    assert {r['branch'] for r in store.branch_assignments(switch)} == {'1', '2'}
    panel.redo(); assert store.port(other)['sample_id'] is None
    panel.close(); app.processEvents()


def test_fridge_rubber_band_batch_status(tmp_path):
    from qubit_sequencer.fridge_ui import FridgePanel
    from qubit_sequencer.fridge_store import FridgeStore
    app = QApplication.instance() or QApplication([])
    store = FridgeStore.create(tmp_path / 'f.qfridge', 'DR')
    panel = FridgePanel(store); panel.resize(1200, 800); panel.show(); panel.open_board('F'); panel.fit_map(); app.processEvents()
    items = [item for item in panel.scene.items() if hasattr(item, 'port') and item.port['number'] in ('O1', 'O2')]
    rect = items[0].sceneBoundingRect().united(items[1].sceneBoundingRect()).adjusted(-6, -6, 6, 6)
    drag(panel.graphics, panel.graphics.mapFromScene(rect.topLeft()), panel.graphics.mapFromScene(rect.bottomRight()), Qt.MouseButton.LeftButton)
    ids = {item.port['id'] for item in items}
    assert set(panel.selected_port_ids()) == ids
    panel.occupied.setCurrentIndex(panel.occupied.findData('occupied'))
    assert all(store.port(ident)['manual_status'] == 'occupied' for ident in ids)
    panel.undo(); assert all(not store.port(ident)['manual_status'] for ident in ids)
    panel.close(); app.processEvents()

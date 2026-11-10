from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsScene

from qubit_sequencer import gui
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

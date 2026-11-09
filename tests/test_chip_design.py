"""Design layout persistence and editing, using temporary chips and settings."""

import os
import sqlite3
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from qubit_sequencer import gui
from qubit_sequencer.design_ui import design_image
from qubit_sequencer.storage import ChipStore, SCHEMA_VERSION


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def layout_file(app, tmp_path):
    path = tmp_path / "layout.png"
    image = QImage(800, 600, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.lightGray)
    assert image.save(str(path))
    return path


def test_design_roundtrip_backup_rename_delete_replace(tmp_path, layout_file):
    path = tmp_path / "chip.qchip"
    data = layout_file.read_bytes()
    with ChipStore.create(path, "样片") as store:
        qubit = store.add_device("qubit", "fixed_transmon", "Q_{1}", 0, 0)
        store.set_chip_design(layout_file.name, data)
        store.set_design_marker(qubit, .25, .75)
        store.set_design_rotation(-90)
        store.update_device(qubit, label="Q_{renamed}")
        backup = tmp_path / "backup.qchip"
        store.backup_to(backup)
    layout_file.unlink()
    for copy in (path, backup):
        with ChipStore.open(copy) as store:
            assert store.chip_design()["data"] == data
            assert store.chip_design()["rotation"] == 270
            marker = store.design_markers()[0]
            assert (marker["x"], marker["y"], marker["label"]) == (.25, .75, "Q_{renamed}")
    with ChipStore.open(path) as store:
        store.set_design_marker(qubit, .8, .2)
        assert len(store.design_markers()) == 1
        store.delete_device(qubit)
        assert store.design_markers() == []
        other = store.add_device("coupler", "fixed_coupler", "C1", 1, 1)
        store.set_design_marker(other, .5, .5)
        store.set_chip_design("replacement.png", data)
        assert store.design_markers() == []
        assert store.chip_design()["rotation"] == 0
        store.set_design_marker(other, .5, .5)
        store.remove_chip_design()
        assert store.chip_design() is None
        assert store.design_markers() == []
        assert len(store.devices()) == 1


def test_v3_upgrade_preserves_parameters_and_original_backup(tmp_path):
    path = tmp_path / "legacy.qchip"
    with ChipStore.create(path, "原有芯片") as store:
        qubit = store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
        store.set_parameter(qubit, "qubit.f01", 5.2)
        with store.db:
            store.db.execute("DROP TABLE readout_links")
            store.db.execute("DROP TABLE wiring_details")
            store.db.execute("DROP TABLE design_markers")
            store.db.execute("DROP TABLE chip_design")
            store.db.execute("PRAGMA user_version=3")
    with ChipStore.open(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.chip_design() is None
        assert store.current_values()[(qubit, "qubit.f01")] == 5.2
    with sqlite3.connect(tmp_path / "legacy.pre-v3.qchip") as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 3
        assert old.execute("SELECT numeric_value FROM parameter_records").fetchone()[0] == 5.2
        assert old.execute("SELECT name FROM sqlite_master WHERE name='chip_design'").fetchone() is None


@pytest.fixture
def window(app, tmp_path):
    path = tmp_path / "gui.qchip"
    with ChipStore.create(path, "设计图测试") as store:
        store.add_device("qubit", "fixed_transmon", "Q_{1}", 0, 0)
        store.add_device("qubit", "fixed_transmon", "Q_{2}", 1, 0)
    window = gui.MainWindow(str(path))
    window.show()
    window.design_button.click()
    app.processEvents()
    yield window
    window.drafts.clear()
    window.close()
    app.processEvents()


def import_layout(panel, path):
    with patch("qubit_sequencer.design_ui.QFileDialog.getOpenFileName", return_value=(str(path), "")):
        panel.import_image()


def image_point(view, x, y):
    rect = view.image_item.boundingRect()
    scene_pos = view.image_item.mapToScene(QPointF(x * rect.width(), y * rect.height()))
    return view.mapFromScene(scene_pos)


def test_rotated_click_drag_inspect_rename_and_reopen(app, window, layout_file):
    panel = window.design_panel
    import_layout(panel, layout_file)
    assert panel.view.image_item is not None
    ident = panel.devices.currentData()
    panel.edit_button.setChecked(True)
    panel.rotate_right.click()
    assert panel.angle.value() == 90
    QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=image_point(panel.view, .25, .3))
    row = window.store.design_markers()[0]
    assert row["device_id"] == ident
    assert row["x"] == pytest.approx(.25, abs=.004)
    assert row["y"] == pytest.approx(.3, abs=.004)
    item = panel.view.markers[ident]
    # Qt must drag an upright, fixed-size marker in its rotated parent's coordinates.
    start = image_point(panel.view, row["x"], row["y"])
    end = image_point(panel.view, .6, .65)
    QTest.mousePress(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(panel.view.viewport(), end, delay=30)
    QTest.mouseRelease(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    row = window.store.design_markers()[0]
    assert row["x"] == pytest.approx(.6, abs=.006)
    assert row["y"] == pytest.approx(.65, abs=.006)
    panel.edit_button.setChecked(False)
    QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=image_point(panel.view, row["x"], row["y"]))
    app.processEvents()
    assert window.device_popup.device_id == ident
    assert window.device_popup.isVisible()
    window.device_popup.label.setText("Q_{renamed}")
    window.device_popup.save()
    assert "renamed" in panel.view.markers[ident].document.toPlainText()
    assert window.tabs.currentIndex() == 0
    assert window.chip_views.currentWidget() is panel
    window.device_popup.close()
    path = window.store.path
    layout_file.unlink()
    assert window._open_path(str(path))
    assert panel.angle.value() == 90
    assert len(panel.view.markers) == 1
    assert "renamed" in panel.view.markers[ident].document.toPlainText()
    panel.edit_button.setChecked(True)
    assert window.design_button.text() == "返回构型图"
    window.design_button.click()
    app.processEvents()
    assert window.tabs.currentIndex() == 0
    assert window.view.isVisible()
    assert not panel.isVisible()
    assert window.place_action.isEnabled()
    assert window.design_button.text() == "查看设计图"
    window.connect_action.setChecked(True)
    window.on_scene_clicked("device", ident)
    assert window.first_endpoint == ident
    window.design_button.click()
    app.processEvents()
    assert window.first_endpoint is None
    assert not window.connect_action.isEnabled()
    assert not panel.edit_button.isChecked()
    assert panel.isVisible()
    assert panel.angle.value() == 90
    assert ident in panel.view.markers


def test_import_cancel_invalid_image_and_marker_removal(window, layout_file, tmp_path):
    panel = window.design_panel
    import_layout(panel, layout_file)
    ident = panel.devices.currentData()
    panel.edit_button.setChecked(True)
    panel.place_marker(.4, .5)
    panel.angle.setValue(37.5)
    with patch("qubit_sequencer.design_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
        import_layout(panel, layout_file)
    assert len(window.store.design_markers()) == 1
    assert panel.angle.value() == 37.5
    invalid = tmp_path / "bad.png"
    invalid.write_bytes(b"not an image")
    with patch.object(window, "_error") as error:
        import_layout(panel, invalid)
        error.assert_called_once()
    assert panel.angle.value() == 37.5
    assert ident in panel.view.markers
    panel.remove_marker_button.click()
    assert window.store.design_markers() == []
    assert panel.view.markers == {}
    with patch("qubit_sequencer.design_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
        import_layout(panel, layout_file)
    assert panel.angle.value() == 0
    assert not panel.remove_marker_button.isEnabled()


def test_new_chip_can_import_layout_at_creation(app, window, tmp_path, layout_file):
    dialog = gui.NewChipDialog(window)
    dialog.design_path.setText(str(layout_file))
    dialog.template.setCurrentIndex(1)
    dialog.rows.setValue(2); dialog.columns.setValue(2)
    path = tmp_path / "created.qchip"
    with patch.object(gui, "NewChipDialog", return_value=dialog), \
            patch.object(dialog, "exec", return_value=QDialog.DialogCode.Accepted), \
            patch.object(gui.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
        window.new_chip()
    assert window.store.path == path
    assert len(window.store.devices("qubit")) == 4
    assert window.store.chip_design()["data"] == layout_file.read_bytes()
    assert not design_image(window.store.chip_design()["data"]).isNull()
    assert window.design_panel.view.image_item is not None


def test_clipboard_import_replace_cancel_undo_redo_and_reopen(window):
    panel = window.design_panel
    image = QImage(200, 120, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    image.setPixelColor(20, 20, Qt.GlobalColor.cyan)
    # Mock the clipboard provider without changing the user's clipboard.
    with patch("qubit_sequencer.attachment_ui.QGuiApplication.clipboard") as clipboard:
        clipboard.return_value.image.return_value = image
        panel.clipboard_import_button.click()
        old_data = window.store.chip_design()["data"]
        decoded = design_image(old_data)
        assert decoded.size() == image.size()
        assert decoded.pixelColor(0, 0).alpha() == 0
        assert decoded.pixelColor(20, 20) == image.pixelColor(20, 20)
        assert window.store.chip_design()["file_name"].endswith(".png")
        panel.edit_button.setChecked(True)
        ident = panel.devices.currentData()
        panel.place_marker(.3, .4); panel.angle.setValue(45)
        count = len(window.undo_history.steps)
        replacement = QImage(320, 180, QImage.Format.Format_RGB32)
        replacement.fill(Qt.GlobalColor.red)
        clipboard.return_value.image.return_value = replacement
        with patch("qubit_sequencer.design_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            panel.clipboard_import_button.click()
        assert window.store.chip_design()["data"] == old_data
        assert panel.angle.value() == 45 and ident in panel.view.markers
        assert len(window.undo_history.steps) == count
        with patch("qubit_sequencer.design_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            panel.clipboard_import_button.click()
    assert panel.angle.value() == 0 and panel.view.markers == {}
    assert len(window.undo_history.steps) == count+1
    window.undo_edit()
    assert window.store.chip_design()["data"] == old_data
    assert panel.angle.value() == 45 and ident in panel.view.markers
    window.redo_edit()
    assert panel.angle.value() == 0 and panel.view.markers == {}
    assert window._open_path(str(window.store.path))
    assert panel.view.image_item.pixmap().size() == replacement.size()
    assert design_image(window.store.chip_design()["data"]).pixelColor(0, 0) == replacement.pixelColor(0, 0)


def test_clipboard_without_image_preserves_existing_design(window, layout_file):
    panel = window.design_panel
    import_layout(panel, layout_file)
    panel.edit_button.setChecked(True)
    ident = panel.devices.currentData()
    panel.place_marker(.2, .6); panel.angle.setValue(90)
    old_data = window.store.chip_design()["data"]
    count = len(window.undo_history.steps)
    with patch("qubit_sequencer.attachment_ui.QGuiApplication.clipboard") as clipboard, \
            patch.object(window, "_error") as error:
        clipboard.return_value.image.return_value = QImage()
        panel.clipboard_import_button.click()
        error.assert_called_once()
        assert "剪贴板中没有图片" in error.call_args.args[1]
    assert window.store.chip_design()["data"] == old_data
    assert panel.angle.value() == 90 and ident in panel.view.markers
    assert len(window.undo_history.steps) == count

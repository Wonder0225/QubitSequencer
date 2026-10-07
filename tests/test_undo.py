"""Undo/redo preserves whole edits, including cascades, drafts and attachments."""

import os
import sqlite3
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from qubit_sequencer import gui
from qubit_sequencer.attachment_store import AttachmentChange, AttachmentInput
from qubit_sequencer.attachment_ui import AttachmentDialog
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.undo import EditHistory


def snapshot(store):
    tables = [row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {table: [tuple(row) for row in store.db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables}


@pytest.fixture
def store(tmp_path):
    store = ChipStore.create(tmp_path / "undo.qchip", "撤回测试")
    q1 = store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
    q2 = store.add_device("qubit", "fixed_transmon", "Q2", 2, 0)
    q3 = store.add_device("qubit", "fixed_transmon", "Q3", 4, 0)
    c1 = store.add_device("coupler", "fixed_coupler", "C1", 1, 0)
    c2 = store.add_device("coupler", "fixed_coupler", "C2", 3, 0)
    pair = store.add_coupling(q1, q2, "coupler", c1)
    link = store.links(c1)[0]["id"]
    store.set_parameter(q1, "qubit.f01", 4.9)
    record = store.set_parameter(q1, "qubit.f01", 5.2, attachments=[AttachmentInput("fit.py", b"old=5.2")])
    attachment = store.parameter_attachments(record)[0]["id"]
    store.commit_attachment_changes(record, [AttachmentChange(attachment, AttachmentInput("fit.py", b"value=5.2"))])
    store.set_parameter(link, "link.g_qc", 42)
    line = store.add_line("XY", port="P1", devices=[q1, q2])
    calibration = store.add_calibration(q1, "T1", {"T1": 30})
    store.add_attachment(calibration, "raw.dat", "application/octet-stream", b"legacy")
    store.set_chip_design("original.png", b"original-image")
    store.set_design_marker(q1, .1, .2)
    store.set_design_rotation(90)
    store.test_ids = dict(q1=q1, q2=q2, q3=q3, c1=c1, c2=c2, pair=pair, link=link,
                          record=record, attachment=attachment, line=line)
    history = EditHistory()
    history.bind(store.db)
    store.undo_history = history
    yield store
    history.detach()
    store.close()


EDITS = {
    "device_add": lambda s, i: s.add_device("qubit", "fixed_transmon", "Q4", 6, 0),
    "device_update": lambda s, i: s.update_device(i["q1"], label="renamed", subtype="tunable_transmon", role="data", x=-1, notes="changed"),
    "device_delete_cascade": lambda s, i: s.delete_device(i["q1"]),
    "coupler_delete_cascade": lambda s, i: s.delete_device(i["c1"]),
    "coupling_add": lambda s, i: s.add_coupling(i["q2"], i["q3"]),
    "coupling_delete": lambda s, i: s.delete_coupling(i["pair"]),
    "coupling_notes": lambda s, i: s.update_coupling_notes(i["pair"], "new notes"),
    "link_add": lambda s, i: s.add_link(i["q3"], i["c2"]),
    "link_delete": lambda s, i: s.delete_link(i["link"]),
    "link_notes": lambda s, i: s.update_link_notes(i["link"], "new notes"),
    "line_add": lambda s, i: s.add_line("Z", port="new", devices=[i["q3"]]),
    "line_update": lambda s, i: s.update_line(i["line"], "Z", port="P2"),
    "line_delete": lambda s, i: s.delete_line(i["line"]),
    "line_assign": lambda s, i: s.assign_line(i["line"], i["q3"]),
    "line_unassign": lambda s, i: s.unassign_line(i["line"], i["q1"]),
    "definition": lambda s, i: s.add_definition("qubit.custom", "示例", "qubit"),
    "parameter_and_attachments": lambda s, i: s.set_parameter(i["q1"], "qubit.f01", 5.5, attachments=[AttachmentInput("fit2.py", b"new=5.5")]),
    "attachment_revision": lambda s, i: s.commit_attachment_changes(i["record"], [AttachmentChange(i["attachment"], AttachmentInput("new.py", b"result=6"))]),
    "attachment_remove": lambda s, i: s.commit_attachment_changes(i["record"], [AttachmentChange(i["attachment"], removed=True)]),
    "attachment_restore": lambda s, i: s.restore_attachment(i["attachment"], 1),
    "design_replace": lambda s, i: s.set_chip_design("new.png", b"new-image"),
    "design_remove": lambda s, i: s.remove_chip_design(),
    "design_rotation": lambda s, i: s.set_design_rotation(37.5),
    "marker_move": lambda s, i: s.set_design_marker(i["q1"], .8, .7),
    "marker_add": lambda s, i: s.set_design_marker(i["q2"], .5, .5),
    "marker_remove": lambda s, i: s.remove_design_marker(i["q1"]),
}


@pytest.mark.parametrize("edit", EDITS)
def test_rows_roundtrip_every_edit_and_cascade(store, edit):
    before = snapshot(store)
    EDITS[edit](store, store.test_ids)
    after = snapshot(store)
    assert after != before
    assert len(store.undo_history.steps) == 1
    store.undo_history.undo()
    assert snapshot(store) == before
    assert store.validate() == []
    store.undo_history.redo()
    assert snapshot(store) == after
    assert store.validate() == []


def test_noops_failures_new_branch_and_persistent_result(store):
    ident = store.test_ids["q1"]
    store.update_device(ident, label="changed")
    store.undo_history.undo()
    store.update_device(ident, label="Q1")
    assert store.undo_history.redo_steps
    with pytest.raises(sqlite3.IntegrityError):
        store.update_device(ident, label="Q2")
    assert store.undo_history.redo_steps and not store.undo_history.steps
    store.update_device(ident, notes="different edit")
    assert not store.undo_history.redo_steps
    store.undo_history.undo()
    store.undo_history.redo()
    with ChipStore.open(store.path) as reopened:
        assert reopened.device(ident)["notes"] == "different edit"
    assert not store.db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'undo_%'").fetchall()


def test_failed_undo_keeps_file_and_history_for_retry(store):
    ident = store.test_ids["q1"]
    store.update_device(ident, label="renamed")
    after = snapshot(store)
    store.db.execute("""CREATE TEMP TRIGGER block_undo BEFORE UPDATE ON devices
                       WHEN NEW.label='Q1' BEGIN SELECT RAISE(ABORT,'blocked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        store.undo_history.undo()
    assert snapshot(store) == after
    assert len(store.undo_history.steps) == 1 and not store.undo_history.redo_steps
    store.db.execute("DROP TRIGGER block_undo")
    store.undo_history.undo()
    assert store.device(ident)["label"] == "Q1"


def test_updates_keep_only_changed_columns_and_merge_correctly(store):
    before = snapshot(store)
    with store.undo_history.action("调整设计图", merge_key="angle"):
        store.set_design_rotation(45)
    with store.undo_history.action("调整设计图", merge_key="angle"):
        store.set_design_rotation(30)
    step = store.undo_history.steps[-1]
    assert len(store.undo_history.steps) == 1
    values = next(value for (table, _), value in step.rows.items() if table == "chip_design")
    assert values == {3: 90}
    assert all(not isinstance(value, bytes) for value in values.values())
    store.undo_history.undo()
    assert snapshot(store) == before
    store.undo_history.redo()
    assert store.chip_design()["rotation"] == 30


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    path = tmp_path / "gui.qchip"
    with ChipStore.create(path, "界面撤回测试") as store:
        store.add_device("qubit", "fixed_transmon", "Q1", 0, 0)
        store.add_device("qubit", "fixed_transmon", "Q2", 2, 0)
    window = gui.MainWindow(str(path))
    window.show()
    app.processEvents()
    with patch.object(window, "_error", side_effect=lambda title, error: pytest.fail(f"{title}: {error}")):
        yield window
    window.drafts.clear()
    window.close()
    app.processEvents()


def shortcut(widget, redo=False, shift=False):
    modifiers = Qt.KeyboardModifier.ControlModifier
    if shift:
        modifiers |= Qt.KeyboardModifier.ShiftModifier
    QTest.keyClick(widget, Qt.Key.Key_Z if not redo or shift else Qt.Key.Key_Y, modifiers)
    QApplication.processEvents()


def test_shared_drafts_save_cancel_undo_redo_shortcuts(window):
    ident = window.store.devices("qubit")[0]["id"]
    window.on_scene_clicked("device", ident)
    window.device_popup.show_device(ident, window.view.mapToGlobal(window.view.rect().center()))
    window.label_edit.setText("renamed")
    shortcut(window.label_edit)
    assert window.label_edit.text() == "Q1"
    assert window.device_popup.label.text() == "Q1"
    shortcut(window.label_edit, redo=True, shift=True)
    assert window.device_popup.label.text() == "renamed"
    window.save_device()
    assert window.store.device(ident)["label"] == "renamed"
    shortcut(window.view)
    assert window.store.device(ident)["label"] == "Q1"
    assert window.drafts.dirty("device", ident)
    shortcut(window.view)
    assert window.label_edit.text() == "Q1" and not window.drafts.dirty_keys()
    shortcut(window.view, redo=True)
    shortcut(window.view, redo=True)
    assert window.store.device(ident)["label"] == "renamed"
    assert not window.drafts.dirty_keys()
    window.x_edit.setValue(-1)
    window.reset_selected()
    shortcut(window.view)
    assert window.x_edit.value() == -1
    shortcut(window.view, redo=True)
    assert window.x_edit.value() == 0


def test_compound_connection_deletion_and_file_switch(window, tmp_path):
    q1, q2 = [row["id"] for row in window.store.devices("qubit")]
    before = snapshot(window.store)
    window.connection_mode.setCurrentIndex(1)
    window._connect(q1, q2)
    after = snapshot(window.store)
    assert len(window.undo_history.steps) == 1
    shortcut(window.view)
    assert snapshot(window.store) == before
    shortcut(window.view, redo=True)
    assert snapshot(window.store) == after
    window.selected = ("device", q1)
    window.refresh_detail()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        window.delete_selected()
    shortcut(window.view)
    assert snapshot(window.store) == after
    shortcut(window.view, redo=True)
    assert len(window.store.devices("qubit")) == 1
    other = tmp_path / "other.qchip"
    with ChipStore.create(other, "另一个项目"):
        pass
    assert window._open_path(str(other))
    assert not window.undo_history.steps and not window.undo_history.redo_steps
    assert not window.undo_action.isEnabled() and not window.redo_action.isEnabled()


def test_dialog_fields_and_staged_attachment_order(window):
    definition = window.store.definition("qubit.f01")
    dialog = gui.ParameterDialog(definition, "default", window)
    dialog.show()
    dialog.value.setText("5.5")
    dialog.attachments.add_payload(AttachmentInput("test.txt", b"original", "text/plain", encoding="utf-8"))
    shortcut(dialog.value)
    assert dialog.attachments.drafts == [] and dialog.value.text() == "5.5"
    shortcut(dialog.value)
    assert dialog.value.text() == ""
    shortcut(dialog.value, redo=True)
    shortcut(dialog.value, redo=True)
    assert dialog.value.text() == "5.5" and len(dialog.attachments.drafts) == 1
    dialog.attachments.toggle_removed()
    shortcut(dialog.attachments.files)
    assert len(dialog.attachments.drafts) == 1
    dialog.close()
    line = gui.LineDialog(window)
    line.show()
    line.line_type.setCurrentIndex(1)
    shortcut(line.line_type)
    assert line.line_type.currentText() == "XY"
    shortcut(line.line_type, redo=True)
    assert line.line_type.currentText() == "Z"
    line.close()
    create = gui.NewChipDialog(window)
    create.show()
    rows = create.rows.value()
    create.rows.setValue(rows + 1)
    shortcut(create.rows)
    assert create.rows.value() == rows
    shortcut(create.rows, redo=True)
    assert create.rows.value() == rows + 1
    create.close()


def test_design_rotation_markers_and_embedded_image(window, tmp_path):
    path = tmp_path / "design.png"
    image = QImage(400, 300, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    image.save(str(path))
    window.store.set_chip_design(path.name, path.read_bytes())
    window.design_panel.reload_image(); window.refresh_content()
    window.design_button.click()
    panel = window.design_panel
    panel.edit_button.click()
    panel.place_marker(.2, .4)
    panel.rotate_right.click()
    shortcut(panel.view)
    assert panel.angle.value() == 0
    assert len(panel.view.markers) == 1
    shortcut(panel.view)
    assert panel.view.markers == {}
    shortcut(panel.view, redo=True)
    shortcut(panel.view, redo=True)
    assert panel.angle.value() == 90 and len(panel.view.markers) == 1
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        panel.remove_image()
    shortcut(panel.view)
    assert panel.view.image_item is not None and len(panel.view.markers) == 1
    shortcut(panel.view, redo=True)
    assert panel.view.image_item is None


def test_saved_attachment_dialog_and_text_content(window):
    ident = window.store.devices("qubit")[0]["id"]
    record = window.store.set_parameter(ident, "qubit.f01", 5.2)
    dialog = AttachmentDialog(window.store, record, window)
    dialog.show()
    panel = dialog.panel
    panel.add_payload(AttachmentInput("fit.txt", b"old", "text/plain", encoding="utf-8"))
    panel.text.setPlainText("new")
    shortcut(panel.text)
    assert panel.inputs()[0].data == b"old"
    shortcut(panel.text, redo=True)
    assert panel.inputs()[0].data == b"new"
    assert dialog.save()
    assert len(window.store.parameter_attachments(record)) == 1
    shortcut(panel.files)
    assert window.store.parameter_attachments(record) == [] and panel.drafts == []
    shortcut(panel.files, redo=True)
    assert len(window.store.parameter_attachments(record)) == 1
    assert panel.inputs()[0].data == b"new"
    dialog.close()

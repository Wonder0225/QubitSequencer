import hashlib
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QMimeData, QPoint, QPointF, QTimer, QUrl, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from qubit_sequencer.attachment_store import AttachmentInput, AttachmentChange
from qubit_sequencer.attachment_ui import AttachmentDialog, decode_text, payload_from_file
from qubit_sequencer.exports import build_tables
from qubit_sequencer.storage import ChipStore, SCHEMA_VERSION
from qubit_sequencer import gui


@pytest.fixture
def store(tmp_path):
    with ChipStore.create(tmp_path/"chip.qchip", "附件测试") as result:
        yield result


def owners(store):
    a = store.add_device("qubit", "fixed_transmon", "Q_{1}", 0, 0)
    b = store.add_device("qubit", "fixed_transmon", "Q_{2}", 1, 0)
    c = store.add_device("coupler", "fixed_coupler", "C_{12}", .5, 0)
    pair = store.add_coupling(a, b, "coupler", c)
    return [(a, "qubit.f01"), (c, "coupler.f01"),
            (pair, "coupling.g_effective"), (store.links()[0]["id"], "link.g_qc")]


def test_records_all_scopes_backup_and_content_dedup(store, tmp_path):
    payload = AttachmentInput("fit.py", b"print('fit')\n", "text/x-python", encoding="utf-8")
    for owner, key in owners(store):
        empty = store.set_parameter(owner, key, 0)
        record = store.set_parameter(owner, key, 5, attachments=(payload, replace(payload, file_name="result.txt")))
        assert store.parameter_attachments(empty) == []
        files = store.parameter_attachments(record)
        assert len(files) == 2
        assert store.read_attachment(files[0]["id"])["data"] == payload.data
    assert store.db.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 1
    backup = tmp_path/"backup.qchip"
    store.backup_to(backup)
    with ChipStore.open(backup) as copied:
        assert len(copied.attachment_counts()) == 4
        assert copied.read_attachment(files[0]["id"])["data"] == payload.data
        assert copied.validate() == []
    headers = build_tables(store, include_history=True)["参数历史"][0]
    assert len(headers) == 13 and "附件数量" not in headers


def test_parameter_and_attachment_changes_rollback_and_owner_guard(store):
    owner, key = owners(store)[0]
    prior = store.set_parameter(owner, key, 5)
    good = AttachmentInput("result.txt", b"data", "text/plain")
    with pytest.raises(ValueError):
        store.set_parameter(owner, key, 6, attachments=(good, replace(good, file_name="../bad")))
    assert store.records()[0]["id"] == prior
    assert len(store.records()) == 1
    assert store.current_values()[(owner, key)] == 5
    assert store.db.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 0
    first = store.set_parameter(owner, key, 6, attachments=(good,))
    ident = store.parameter_attachments(first)[0]["id"]
    second = store.set_parameter(owner, key, 7)
    with pytest.raises(ValueError):
        store.commit_attachment_changes(second, [AttachmentChange(ident, good)])
    before = store.read_attachment(ident)["data"]
    with pytest.raises(ValueError):
        store.commit_attachment_changes(first, [
            AttachmentChange(ident, replace(good, data=b"modified")),
            AttachmentChange("missing", good),
        ])
    assert store.read_attachment(ident)["data"] == before
    assert len(store.attachment_versions(ident)) == 1


def test_versions_restore_removal_hash_check_and_cascade(store):
    owner, key = owners(store)[0]
    payload = AttachmentInput("note.txt", "标定完成".encode("utf-8"), "text/plain", encoding="utf-8")
    record = store.set_parameter(owner, key, 5, attachments=(payload,))
    original_record = dict(store.record(record))
    ident = store.parameter_attachments(record)[0]["id"]
    store.commit_attachment_changes(record, [AttachmentChange(ident, replace(payload, data=b"second"))])
    store.commit_attachment_changes(record, [AttachmentChange(ident, replace(payload, file_name="renamed.txt", notes="恢复原内容"))])
    assert store.restore_attachment(ident, 1) == 4
    assert store.read_attachment(ident)["data"] == payload.data
    assert store.restore_attachment(ident, 4) == 5
    assert store.db.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 2
    store.commit_attachment_changes(record, [AttachmentChange(ident, removed=True)])
    assert store.parameter_attachments(record) == []
    assert store.parameter_attachments(record, True)[0]["removed"] == 1
    store.commit_attachment_changes(record, [AttachmentChange(ident)])
    assert len(store.parameter_attachments(record)) == 1
    assert dict(store.record(record)) == original_record
    with pytest.raises(ValueError):
        store.commit_attachment_changes(record, [AttachmentChange(ident, payload, expected_version=1)])
    digest = store.read_attachment(ident)["sha256"]
    with store.db:
        store.db.execute("UPDATE attachment_blobs SET data=? WHERE sha256=?", (b"corrupted", digest))
    with pytest.raises(ValueError, match="校验"):
        store.read_attachment(ident)
    store.delete_device(owner)
    assert store.db.execute("SELECT COUNT(*) FROM record_attachments").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 0


def test_v2_migration_preserves_legacy_and_backup(store):
    owner, key = owners(store)[0]
    record = store.set_parameter(owner, key, 5)
    calibration = store.add_calibration(owner, "T1", {"t1": 50})
    legacy = store.add_attachment(calibration, "old.png", "image/png", b"original")
    path = store.path
    with store.db:
        store.db.execute("DROP TABLE sample_box_bindings")
        store.db.execute("DROP TABLE sample_box_ports")
        store.db.execute("DROP TABLE readout_links")
        store.db.execute("DROP TABLE wiring_details")
        store.db.execute("DROP TABLE design_markers")
        store.db.execute("DROP TABLE chip_design")
        store.db.execute("DROP TABLE attachment_versions")
        store.db.execute("DROP TABLE record_attachments")
        store.db.execute("DROP TABLE attachment_blobs")
        store.db.execute("PRAGMA user_version=2")
    with ChipStore.open(path) as migrated:
        assert migrated.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert migrated.record(record)["numeric_value"] == 5
        assert migrated.parameter_attachments(record) == []
        assert migrated.db.execute("SELECT data FROM attachments WHERE id=?", (legacy,)).fetchone()[0] == b"original"
        assert migrated.validate() == []
    backup = path.with_name("chip.pre-v2.qchip")
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 2
        assert old.execute("SELECT numeric_value FROM parameter_records WHERE id=?", (record,)).fetchone()[0] == 5
        assert old.execute("SELECT data FROM attachments WHERE id=?", (legacy,)).fetchone()[0] == b"original"


def test_file_snapshot_and_text_encodings(tmp_path):
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        path = tmp_path/f"{encoding}.py"
        data = "结果 = 5\r\n".encode(encoding); path.write_bytes(data)
        payload = payload_from_file(str(path)); path.unlink()
        assert payload.data == data and payload.encoding == encoding
        assert decode_text(payload.data, payload.file_name, payload.mime_type, payload.encoding)[0] == "结果 = 5\r\n"
    assert decode_text(b"\x00\xff", "code.py", "text/plain") == (None, None)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_gui_clipboard_text_versions_sorting_cancel_failure(app, tmp_path):
    path = tmp_path/"ui.qchip"
    image = QImage(40, 30, QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.blue)
    app.clipboard().setImage(image)
    with ChipStore.create(path, "界面") as s:
        owner, key = owners(s)[0]
        first = s.set_parameter(owner, key, 5, observed_at="2026-10-05T12:00:00+08:00")
        second = s.set_parameter(owner, key, 6, observed_at="2026-10-05T12:00:00+08:00")
    window = gui.MainWindow(str(path))
    try:
        registration = gui.ParameterDialog(window.store.definition(key), "default", window)
        registration.show(); registration.value.setFocus()
        QTest.keyClick(registration.value, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert len(registration.attachments.inputs()) == 1
        png = registration.attachments.inputs()[0]
        assert png.data.startswith(b"\x89PNG")
        assert registration.attachments.preview.currentWidget() is registration.attachments.image
        app.clipboard().setText("粘贴文本")
        registration.notes.setFocus()
        QTest.keyClick(registration.notes, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert registration.notes.text() == "粘贴文本"
        registration.reject()
        assert window.store.parameter_attachments(first) == []
        dialog = AttachmentDialog(window.store, first, window)
        dialog.show(); app.processEvents()
        dialog.panel.add_payload(png)
        assert dialog.save()
        assert len(window.store.parameter_attachments(first)) == 1
        code = AttachmentInput("fit.py", b"# original\r\nx = 5\r\n", "text/x-python", encoding="utf-8")
        dialog.panel.add_payload(code)
        assert dialog.save()
        assert not dialog.panel.is_dirty()
        code_draft = next(d for d in dialog.panel.drafts if d.meta["file_name"] == "fit.py")
        for i in range(dialog.panel.files.count()):
            if dialog.panel.files.item(i).data(Qt.ItemDataRole.UserRole) is code_draft:
                dialog.panel.files.setCurrentRow(i); break
        app.processEvents()
        assert dialog.panel.preview.currentWidget() is dialog.panel.text
        dialog.panel.text.setPlainText("# edited\nx = 6\n")
        with patch.object(window.store, "commit_attachment_changes", side_effect=OSError("模拟失败")):
            assert not dialog.save()
        assert "# edited" in dialog.panel.text.toPlainText()
        assert "输入已保留" in dialog.error.text()
        assert dialog.save()
        assert window.store.read_attachment(code_draft.id)["data"] == b"# edited\r\nx = 6\r\n"
        # Cancellation reloads stored state, with no new revision.
        current_draft = dialog.panel.current()
        dialog.panel.text.setPlainText("cancel me")
        dialog.reset_changes()
        assert window.store.read_attachment(current_draft.id)["version"] == 2
        for i in range(dialog.panel.files.count()):
            if dialog.panel.files.item(i).data(Qt.ItemDataRole.UserRole).id == current_draft.id:
                dialog.panel.files.setCurrentRow(i); break
        dialog.panel.versions.setCurrentIndex(dialog.panel.versions.findData(1))
        assert dialog.panel.text.isReadOnly()
        dialog.panel.restore_version()
        assert dialog.save()
        assert window.store.read_attachment(current_draft.id)["version"] == 3
        assert window.store.read_attachment(current_draft.id)["data"] == code.data
        # Closing a dirty editor prompts; cancelling the prompt keeps the draft.
        dialog.panel.text.setPlainText("# pending close")
        def choose_cancel():
            for message in app.topLevelWidgets():
                if isinstance(message, QMessageBox) and message.isVisible():
                    next(button for button in message.buttons() if button.text() == "取消").click()
        QTimer.singleShot(0, choose_cancel)
        dialog.close()
        assert dialog.isVisible() and dialog.panel.is_dirty()
        dialog.reset_changes()
        dialog.close()
        window.refresh_history()
        for row in range(window.history_table.rowCount()):
            item = window.history_table.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole+2) == first:
                window.history_table.setCurrentCell(row, 1)
        window.history_table.sortItems(5, Qt.SortOrder.DescendingOrder)
        assert window._chosen_history_id() == first
        window.refresh_history()
        assert window._chosen_history_id() == first
        r = window.history_table.currentRow()
        assert window.history_table.item(r, 13).text() == "2"
        assert window.store.parameter_attachments(second) == []
    finally:
        window.drafts.clear(); window.close()
        app.clipboard().clear()


def test_gui_background_import_errors_and_snapshot(app, tmp_path):
    panel = gui.AttachmentPanel()
    file = tmp_path/"result.py"; file.write_bytes(b"x = 5\n")
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(file))])
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(panel.files.viewport(), enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(panel.files.viewport(), drop)
    for _ in range(100):
        app.processEvents()
        if not panel.busy:
            app.processEvents(); break
        QTest.qWait(10)
    file.unlink()
    assert panel.inputs()[0].data == b"x = 5\n"
    panel.import_files([str(file)])
    with pytest.raises(ValueError, match="失败"):
        panel.inputs()
    panel.clear_import_errors()
    assert len(panel.inputs()) == 1
    panel.stop_readers()
    panel.close()

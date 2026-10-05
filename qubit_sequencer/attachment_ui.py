"""Attachment snapshots, previews and staged editing for parameter history."""

from __future__ import annotations

import html
import mimetypes
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, Qt, QThread, Signal, QUrl, QSaveFile
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .attachment_store import AttachmentChange, AttachmentInput, safe_file_name
from .storage import beijing_now, display_timestamp, new_id
from .tex_labels import tex_to_html

TEXT_SUFFIXES = {
    ".txt", ".md", ".rst", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".css", ".py", ".pyw", ".ipynb",
    ".js", ".ts", ".c", ".h", ".cpp", ".hpp", ".cs", ".m", ".jl", ".r",
    ".sh", ".bat", ".ps1", ".sql", ".tex",
}
DOCUMENT_SUFFIXES = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx",
                     ".odt", ".ods", ".odp", ".txt", ".md", ".rtf"}


def decode_text(data: bytes, name: str, mime: str, encoding=None):
    if Path(name).suffix.lower() not in TEXT_SUFFIXES and not mime.startswith("text/"):
        return None, None
    if b"\x00" in data:
        return None, None
    encodings = [encoding] if encoding else ["utf-8-sig"] if data.startswith(b"\xef\xbb\xbf") else ["utf-8", "gb18030"]
    for candidate in encodings:
        try:
            text = data.decode(candidate)
            if any(ord(c) < 32 and c not in "\t\r\n" for c in text):
                continue
            if text.encode(candidate) == data:
                return text, candidate
        except (UnicodeError, LookupError):
            pass
    return None, None


def payload_from_file(path: str) -> AttachmentInput:
    file = Path(path)
    safe_file_name(file.name)
    data = file.read_bytes()
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    _, encoding = decode_text(data, file.name, mime)
    return AttachmentInput(file.name, data, mime, encoding=encoding)


def clipboard_payload() -> AttachmentInput | None:
    image = QGuiApplication.clipboard().image()
    if image.isNull():
        return None
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("无法将剪贴板图像转换为 PNG")
    return AttachmentInput(f"粘贴图像-{new_id()[:8]}.png", bytes(data), "image/png")


class ImagePasteFilter(QObject):
    def __init__(self, panel, parent):
        super().__init__(parent)
        self.panel = panel

    def eventFilter(self, watched, event):
        if (event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.matches(QKeySequence.StandardKey.Paste)
                and not QGuiApplication.clipboard().image().isNull()):
            if event.type() == QEvent.Type.KeyPress:
                self.panel.paste_image()
            event.accept()
            return True
        return super().eventFilter(watched, event)


def install_paste_shortcut(host, panel):
    host._image_paste_filter = ImagePasteFilter(panel, host)
    for widget in [host] + host.findChildren(QWidget):
        widget.installEventFilter(host._image_paste_filter)
    shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), host)
    shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    def paste():
        if not QGuiApplication.clipboard().image().isNull():
            panel.paste_image()
        else:
            focused = QApplication.focusWidget()
            if isinstance(focused, (QLineEdit, QPlainTextEdit)):
                focused.paste()
    shortcut.activated.connect(paste)
    return shortcut


def export_payload(payload, destination, protected_path=None):
    target = Path(destination)
    if protected_path and target.resolve() == Path(protected_path).resolve():
        raise ValueError("不能覆盖当前芯片文件")
    file = QSaveFile(str(target))
    if not file.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(file.errorString())
    if file.write(payload.data) != len(payload.data) or not file.commit():
        file.cancelWriting()
        raise OSError(file.errorString())


class FileReader(QObject):
    result = Signal(object)
    done = Signal()

    def __init__(self, paths):
        super().__init__()
        self.paths = paths

    def run(self):
        for path in self.paths:
            if QThread.currentThread().isInterruptionRequested():
                break
            try:
                self.result.emit((path, payload_from_file(path), None))
            except Exception as exc:
                self.result.emit((path, None, str(exc)))
        self.done.emit()


@dataclass
class AttachmentDraft:
    id: str | None
    meta: dict
    removed: bool = False
    initial_removed: bool = False
    base: AttachmentInput | None = None
    payload: AttachmentInput | None = None
    force_revision: bool = False

    def dirty(self):
        return ((self.id is None and not self.removed) or self.removed != self.initial_removed
                or self.force_revision or self.payload != self.base)


class ImageView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.image_scene = QGraphicsScene(self)
        self.setScene(self.image_scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.fitted = True

    def set_image(self, image):
        self.image_scene.clear()
        self.image_scene.addPixmap(QPixmap.fromImage(image))
        self.fit_image()

    def fit_image(self):
        self.fitted = True
        self.fitInView(self.image_scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self):
        self.fitted = False
        self.resetTransform()

    def wheelEvent(self, event):
        self.fitted = False
        factor = 1.15 if event.angleDelta().y() > 0 else 1/1.15
        scale = self.transform().m11()*factor
        if .02 < scale < 40:
            self.scale(factor, factor)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_image()


class AttachmentFileList(QListWidget):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        self.panel.dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.panel.dragEnterEvent(event)

    def dropEvent(self, event):
        self.panel.dropEvent(event)


class AttachmentPanel(QWidget):
    changed = Signal()
    busy_changed = Signal(bool)

    @staticmethod
    def toolbar_button(text, tooltip):
        button = QPushButton(text)
        button.setProperty("compact", True)
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        button.setToolTip(tooltip)
        return button

    def __init__(self, store=None, record_id=None, parent=None):
        super().__init__(parent)
        self.store, self.record_id = store, record_id
        self.drafts = []
        self._loading = False
        self._jobs = []
        self.import_errors = {}
        self._replacement_target = None
        self.selected_revision = None
        self._rendering = False
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        actions = QHBoxLayout(); actions.setSpacing(6); actions.setContentsMargins(4, 0, 4, 0)
        self.add_button = self.toolbar_button("添加文件", "选择一个或多个文件作为附件"); self.add_button.clicked.connect(self.choose_files)
        self.paste_button = self.toolbar_button("粘贴图像", "支持 Ctrl+V；普通文本仍粘贴到当前输入框"); self.paste_button.clicked.connect(self.paste_image)
        self.replace_button = self.toolbar_button("替换", "选择文件替换当前附件；保存后保留此前版本"); self.replace_button.clicked.connect(self.replace_file)
        self.remove_button = self.toolbar_button("移除", "移除当前附件；已保存的附件可恢复"); self.remove_button.clicked.connect(self.toggle_removed)
        for button in (self.add_button, self.paste_button, self.replace_button, self.remove_button):
            actions.addWidget(button)
        actions.addStretch()
        self.show_removed = QCheckBox("显示已移除"); self.show_removed.setVisible(store is not None)
        self.show_removed.setStyleSheet(
            "QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid palette(mid); "
            "border-radius: 3px; background: palette(base); } "
            "QCheckBox::indicator:checked { background: palette(highlight); border-color: palette(highlight); }"
        )
        self.show_removed.toggled.connect(self.refresh_list); actions.addWidget(self.show_removed)
        layout.addLayout(actions)
        self.splitter = QSplitter()
        self.files = AttachmentFileList(self); self.files.setMinimumWidth(160)
        self.files.setToolTip("可拖入文件或直接粘贴图像")
        self.files.currentRowChanged.connect(self.show_selection)
        self.splitter.addWidget(self.files)
        right = QWidget(); preview_layout = QVBoxLayout(right); preview_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.name = QLineEdit(); self.notes = QLineEdit()
        self.name.textEdited.connect(self.metadata_changed); self.notes.textEdited.connect(self.metadata_changed)
        form.addRow("文件名", self.name); form.addRow("说明", self.notes)
        self.versions = QComboBox(); self.versions.currentIndexChanged.connect(self.version_selected)
        form.addRow("版本", self.versions)
        preview_layout.addLayout(form)
        tools = QHBoxLayout(); tools.setSpacing(6); tools.setContentsMargins(4, 0, 4, 0)
        self.fit_button = self.toolbar_button("适配", "将整张图片适配到预览区域"); self.fit_button.clicked.connect(lambda: self.image.fit_image())
        self.actual_button = self.toolbar_button("100%", "按图片原始尺寸查看"); self.actual_button.clicked.connect(lambda: self.image.actual_size())
        self.export_button = self.toolbar_button("导出", "将当前内容保存为文件副本"); self.export_button.clicked.connect(self.save_copy)
        self.open_button = self.toolbar_button("外部打开", "用系统默认程序打开导出的副本"); self.open_button.clicked.connect(self.open_external)
        self.restore_button = self.toolbar_button("恢复版本", "恢复所选历史版本；保存后生成一个新版本"); self.restore_button.clicked.connect(self.restore_version)
        for button in (self.fit_button, self.actual_button, self.export_button, self.open_button, self.restore_button):
            tools.addWidget(button)
        tools.addStretch()
        preview_layout.addLayout(tools)
        self.preview = QStackedWidget()
        self.empty = QLabel("添加或选择附件以预览"); self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image = ImageView()
        self.text = QPlainTextEdit()
        font = QFont("Consolas"); font.setPixelSize(14); self.text.setFont(font)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.textChanged.connect(self.text_changed)
        for widget in (self.empty, self.image, self.text):
            self.preview.addWidget(widget)
        preview_layout.addWidget(self.preview, 1)
        self.info = QLabel(); self.info.setWordWrap(True); self.info.setProperty("role", "muted")
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        preview_layout.addWidget(self.info)
        self.splitter.addWidget(right); self.splitter.setSizes([230, 620])
        layout.addWidget(self.splitter, 1)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setProperty("role", "muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.clear_errors_button = QPushButton("移除失败的导入项")
        self.clear_errors_button.setVisible(False)
        self.clear_errors_button.clicked.connect(self.clear_import_errors)
        layout.addWidget(self.clear_errors_button)
        QGuiApplication.clipboard().dataChanged.connect(self.clipboard_changed)
        QApplication.instance().aboutToQuit.connect(self.stop_readers)
        self.clipboard_changed()
        self.reload()

    @property
    def busy(self):
        return any(thread.isRunning() for thread, _worker in self._jobs)

    def clipboard_changed(self):
        self.paste_button.setEnabled(not self.busy and not QGuiApplication.clipboard().image().isNull())

    def current(self):
        item = self.files.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def is_dirty(self):
        return any(draft.dirty() for draft in self.drafts)

    def reload(self):
        self.import_errors.clear()
        self.clear_errors_button.setVisible(False)
        self.status.clear()
        self.drafts = [
            AttachmentDraft(row["id"], dict(row), bool(row["removed"]), bool(row["removed"]))
            for row in self.store.parameter_attachments(self.record_id, True)
        ] if self.store else []
        self.refresh_list()
        self.changed.emit()

    def refresh_list(self, *_):
        selected = self.current()
        self.files.blockSignals(True)
        self.files.clear()
        for draft in self.drafts:
            if draft.removed and not self.show_removed.isChecked():
                continue
            payload = draft.payload
            name = payload.file_name if payload else draft.meta["file_name"]
            mime = payload.mime_type if payload else draft.meta["mime_type"]
            size = len(payload.data) if payload else draft.meta["byte_size"]
            status = " · 已移除" if draft.removed else " · 未保存" if draft.dirty() else ""
            item = QListWidgetItem(f"{name}{status}\n{mime} · {size/1024:,.1f} KB")
            item.setToolTip(f"{name}\n{mime}\n{size:,} 字节")
            item.setData(Qt.ItemDataRole.UserRole, draft)
            self.files.addItem(item)
            if draft is selected:
                self.files.setCurrentItem(item)
        if self.files.currentRow() < 0 and self.files.count():
            self.files.setCurrentRow(0)
        self.files.blockSignals(False)
        self.show_selection()

    def ensure_loaded(self, draft):
        if draft.payload is None:
            row = self.store.read_attachment(draft.id)
            draft.base = AttachmentInput(row["file_name"], row["data"], row["mime_type"], row["notes"], row["encoding"])
            draft.payload = draft.base
        return draft.payload

    def show_selection(self, *_):
        self._rendering = True
        try:
            draft = self.current()
            self.versions.blockSignals(True); self.versions.clear()
            if draft:
                self.versions.addItem("当前内容" + (" · 未保存" if draft.dirty() else ""), None)
                if draft.id:
                    for version in self.store.attachment_versions(draft.id):
                        self.versions.addItem(
                            f"v{version['version']} · {display_timestamp(version['created_at'])}", version["version"]
                        )
            self.versions.blockSignals(False)
            self.selected_revision = None
            if not draft:
                self.name.clear(); self.notes.clear(); self.info.clear()
                self.preview.setCurrentWidget(self.empty)
                self.empty.setText("添加或选择附件以预览")
            else:
                self.display_payload(self.ensure_loaded(draft), editable=not draft.removed)
        except Exception as exc:
            self.status.setText(f"无法读取附件：{exc}")
            self.preview.setCurrentWidget(self.empty)
            self.empty.setText("附件无法预览，请检查文件完整性")
        finally:
            self._rendering = False
            self.update_buttons()

    def display_payload(self, payload, editable=True):
        self.name.setText(payload.file_name); self.notes.setText(payload.notes)
        self.name.setReadOnly(not editable); self.notes.setReadOnly(not editable)
        image = QImage.fromData(payload.data)
        text, encoding = decode_text(payload.data, payload.file_name, payload.mime_type, payload.encoding)
        if not image.isNull():
            self.image.set_image(image); self.preview.setCurrentWidget(self.image)
        elif text is not None:
            # Preserve CRLF and BOM until the user actually edits content.
            self.text.setPlainText(text); self.text.setReadOnly(not editable)
            self.preview.setCurrentWidget(self.text)
            draft = self.current()
            if self.selected_revision is None and draft and draft.payload.encoding is None:
                draft.payload = replace(draft.payload, encoding=encoding)
                if draft.base is not None:
                    draft.base = replace(draft.base, encoding=encoding)
        else:
            self.preview.setCurrentWidget(self.empty)
            self.empty.setText("此附件保留原始文件内容，可保存副本或替换文件。")
        draft = self.current()
        self.info.setText(
            f"{payload.mime_type} · {len(payload.data):,} 字节"
            + (f" · {encoding}" if encoding else "")
            + (f"\n上传：{display_timestamp(draft.meta['created_at'])} · 更新：{display_timestamp(draft.meta['modified_at'])}"
               if draft and draft.id else "\n待保存")
        )

    def update_buttons(self):
        draft = self.current()
        enabled = draft is not None and not self.busy
        editable = enabled and not draft.removed and self.selected_revision is None
        self.add_button.setEnabled(not self.busy)
        self.clipboard_changed()
        self.replace_button.setEnabled(bool(editable))
        self.remove_button.setEnabled(bool(enabled and self.selected_revision is None))
        self.remove_button.setText("恢复" if draft and draft.removed else "移除")
        self.remove_button.setToolTip("恢复已移除的附件" if draft and draft.removed else "移除当前附件；已保存的附件可恢复")
        self.name.setEnabled(bool(editable)); self.notes.setEnabled(bool(editable))
        self.versions.setEnabled(bool(enabled and draft.id))
        for button in (self.export_button, self.fit_button, self.actual_button):
            button.setEnabled(bool(enabled))
        self.fit_button.setVisible(self.preview.currentWidget() is self.image)
        self.actual_button.setVisible(self.preview.currentWidget() is self.image)
        filename = self.name.text()
        self.open_button.setEnabled(bool(enabled and Path(filename).suffix.lower() in DOCUMENT_SUFFIXES))
        self.restore_button.setVisible(self.store is not None)
        self.restore_button.setEnabled(bool(enabled and self.selected_revision is not None))
        self.text.setReadOnly(not editable)

    def metadata_changed(self, *_):
        if self._rendering:
            return
        draft = self.current()
        if draft and self.selected_revision is None:
            draft.payload = replace(self.ensure_loaded(draft), file_name=self.name.text(), notes=self.notes.text())
            self.mark_changed()

    def text_changed(self):
        if self._rendering:
            return
        draft = self.current()
        if draft and self.selected_revision is None and not draft.removed:
            try:
                old = self.ensure_loaded(draft)
                encoding = old.encoding or "utf-8"
                new_text = self.text.toPlainText()
                original_bytes = draft.base.data if draft.base is not None else old.data
                if b"\r\n" in original_bytes and b"\n" not in original_bytes.replace(b"\r\n", b""):
                    new_text = new_text.replace("\n", "\r\n")
                draft.payload = replace(old, data=new_text.encode(encoding), encoding=encoding)
                self.mark_changed()
            except UnicodeError:
                self.status.setText("当前编码无法表示输入，请取消更改或替换为 UTF-8 文件。")

    def mark_changed(self):
        item, draft = self.files.currentItem(), self.current()
        if item and draft:
            payload = draft.payload
            item.setText(f"{payload.file_name if payload else draft.meta['file_name']} · 未保存\n"
                         f"{payload.mime_type if payload else draft.meta['mime_type']}")
        self.changed.emit(); self.update_buttons()

    def version_selected(self, *_):
        if self._rendering or not self.current():
            return
        self.selected_revision = self.versions.currentData()
        self._rendering = True
        try:
            if self.selected_revision is None:
                self.display_payload(self.ensure_loaded(self.current()), not self.current().removed)
            else:
                row = self.store.read_attachment(self.current().id, self.selected_revision)
                self.display_payload(AttachmentInput(row["file_name"], row["data"], row["mime_type"],
                                                     row["notes"], row["encoding"]), False)
        except Exception as exc:
            self.status.setText(f"读取版本失败：{exc}")
        finally:
            self._rendering = False; self.update_buttons()

    def restore_version(self):
        draft = self.current()
        if draft and self.selected_revision is not None:
            try:
                row = self.store.read_attachment(draft.id, self.selected_revision)
                draft.payload = AttachmentInput(row["file_name"], row["data"], row["mime_type"],
                                                row["notes"], row["encoding"])
                draft.force_revision = True; draft.removed = False
                self.refresh_list(); self.changed.emit()
                self.status.setText("恢复已暂存，保存更改后生成一个新版本。")
            except Exception as exc:
                self.status.setText(f"恢复失败：{exc}")

    def toggle_removed(self):
        draft = self.current()
        if draft:
            draft.removed = not draft.removed
            if draft.id is None and draft.removed:
                self.drafts.remove(draft)
            self.refresh_list(); self.changed.emit()

    def add_payload(self, payload):
        draft = AttachmentDraft(None, {}, base=payload, payload=payload)
        self.drafts.append(draft)
        self.refresh_list()
        self.files.setCurrentRow(self.files.count()-1)
        self.changed.emit()

    def paste_image(self):
        if self.busy:
            return
        try:
            payload = clipboard_payload()
            if payload:
                self.add_payload(payload); self.status.setText("剪贴板图像已作为 PNG 附件暂存。")
            else:
                self.status.setText("剪贴板中没有图像。")
        except Exception as exc:
            self.status.setText(str(exc))

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加标定附件", "", "所有文件 (*)")
        self.import_files(paths)

    def replace_file(self):
        if not self.current():
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择替换文件", "", "所有文件 (*)")
        if path:
            self.import_files([path], self.current())

    def import_files(self, paths, replacement=None):
        if self.busy or not paths:
            return
        accepted = []
        for path in paths:
            try:
                if Path(path).stat().st_size > 50*1024*1024 and QMessageBox.question(
                    self, "大附件", f"{Path(path).name} 超过 50 MiB。继续导入？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                ) != QMessageBox.StandardButton.Yes:
                    continue
                accepted.append(path)
            except OSError as exc:
                self.import_errors[path] = str(exc)
                self.clear_errors_button.setVisible(True)
                self.status.setText(f"无法读取 {Path(path).name}：{exc}")
        if not accepted:
            return
        self._replacement_target = replacement
        thread = QThread(self); worker = FileReader(accepted); worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result.connect(self.import_result)
        worker.done.connect(thread.quit); worker.done.connect(worker.deleteLater)
        thread.finished.connect(self.import_finished)
        self._jobs.append((thread, worker))
        self.status.setText("正在读取附件，请稍候…")
        thread.start(); self.busy_changed.emit(True); self.update_buttons()

    def import_result(self, result):
        path, payload, error = result
        if error:
            self.import_errors[path] = error
            self.clear_errors_button.setVisible(True)
            self.status.setText(f"导入失败 {Path(path).name}：{error}")
            return
        self.import_errors.pop(path, None)
        self.clear_errors_button.setVisible(bool(self.import_errors))
        if self._replacement_target:
            draft = self._replacement_target
            old = self.ensure_loaded(draft)
            draft.payload = replace(payload, notes=old.notes)
            self.refresh_list(); self.changed.emit()
        else:
            self.add_payload(payload)

    def import_finished(self):
        self._replacement_target = None
        self.busy_changed.emit(self.busy); self.update_buttons()
        if self.import_errors:
            self.status.setText("部分附件导入失败。请重试，或明确移除失败项后再保存。")
        elif self.status.text().startswith("正在读取"):
            self.status.setText("读取完成，附件已暂存。")

    def clear_import_errors(self):
        self.import_errors.clear()
        self.clear_errors_button.setVisible(False)
        self.status.setText("失败项已移除；已成功读取的附件仍保留。")

    def validate_ready(self):
        if self.busy:
            raise ValueError("附件仍在读取，请稍候")
        if self.import_errors:
            raise ValueError("存在导入失败的附件，请重试或明确移除失败项")

    def inputs(self):
        self.validate_ready()
        result = []
        for draft in self.drafts:
            if not draft.removed:
                payload = self.ensure_loaded(draft)
                safe_file_name(payload.file_name)
                result.append(payload)
        return result

    def changes(self):
        self.validate_ready()
        changes = []
        for draft in self.drafts:
            if draft.dirty():
                if draft.payload:
                    safe_file_name(draft.payload.file_name)
                changes.append(AttachmentChange(draft.id, draft.payload, draft.removed,
                                                draft.meta.get("version"), draft.force_revision))
        return changes

    def displayed_payload(self):
        draft = self.current()
        if not draft:
            return None
        if self.selected_revision is not None:
            row = self.store.read_attachment(draft.id, self.selected_revision)
            return AttachmentInput(row["file_name"], row["data"], row["mime_type"], row["notes"], row["encoding"])
        return self.ensure_loaded(draft)

    def save_copy(self):
        try:
            payload = self.displayed_payload()
            if payload is None:
                return
            safe_file_name(payload.file_name)
            path, _ = QFileDialog.getSaveFileName(self, "保存附件副本", payload.file_name, "所有文件 (*)")
            if path:
                export_payload(payload, path, self.store.path if self.store else None)
                self.status.setText("附件副本已保存。")
        except Exception as exc:
            self.status.setText(f"保存副本失败：{exc}")

    def open_external(self):
        try:
            payload = self.displayed_payload()
            if payload is None or Path(payload.file_name).suffix.lower() not in DOCUMENT_SUFFIXES:
                return
            safe_file_name(payload.file_name)
            temp = tempfile.TemporaryDirectory(prefix="qchip-attachment-", ignore_cleanup_errors=True)
            path = Path(temp.name)/payload.file_name; path.write_bytes(payload.data)
            app = QApplication.instance()
            if not hasattr(app, "_attachment_open_directories"):
                app._attachment_open_directories = []
            app._attachment_open_directories.append(temp)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError("系统未能打开此类型的文件")
            self.status.setText("已打开临时副本；外部修改需通过“替换文件”重新导入。")
        except Exception as exc:
            self.status.setText(f"打开失败：{exc}")

    def dragEnterEvent(self, event):
        if not self.busy and (event.mimeData().hasImage() or any(u.isLocalFile() for u in event.mimeData().urls())):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.import_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        elif event.mimeData().hasImage():
            image = QImage(event.mimeData().imageData())
            data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if image.save(buffer, "PNG"):
                self.add_payload(AttachmentInput(f"拖入图像-{new_id()[:8]}.png", bytes(data), "image/png"))
        event.acceptProposedAction()

    def stop_readers(self):
        # A snapshot read must finish before its QThread is destroyed.
        for thread, _worker in self._jobs:
            if thread.isRunning():
                thread.requestInterruption()
                thread.quit()
                thread.wait()


class AttachmentDialog(QDialog):
    saved = Signal()

    def __init__(self, store, record_id, parent=None):
        super().__init__(parent)
        self.store, self.record_id = store, record_id
        self.setWindowTitle("参数记录与标定附件")
        self.resize(980, 670); self.setMinimumSize(700, 450)
        layout = QVBoxLayout(self)
        record = store.record(record_id)
        definition = store.definition(record["definition_key"])
        if record["device_id"]:
            name = store.device(record["device_id"])["label"]
        elif record["coupling_id"]:
            coupling = store.coupling(record["coupling_id"])
            name = f"{store.device(coupling['qubit_a'])['label']} ↔ {store.device(coupling['qubit_b'])['label']}"
        else:
            link = store.link(record["link_id"])
            name = f"{store.device(link['qubit_id'])['label']} — {store.device(link['coupler_id'])['label']}"
        value = record["numeric_value"] if record["numeric_value"] is not None else record["text_value"]
        label = QLabel(f"<b>{tex_to_html(name)} · {tex_to_html(definition['label'])}</b><br>"
                       f"{html.escape(str(value) if value is not None else '未填写')} {html.escape(definition['unit'])}"
                       f" · 工作点 {html.escape(record['point'])}"
                       f" · {html.escape(display_timestamp(record['observed_at']))}")
        label.setWordWrap(True); layout.addWidget(label)
        details = QLabel(f"来源：{record['source'] or '未填写'} · 备注：{record['notes'] or '未填写'}")
        details.setTextFormat(Qt.TextFormat.PlainText); details.setWordWrap(True)
        details.setProperty("role", "muted"); layout.addWidget(details)
        self.panel = AttachmentPanel(store, record_id, self)
        layout.addWidget(self.panel, 1)
        self.error = QLabel(); self.error.setProperty("role", "danger"); self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        buttons = QHBoxLayout(); buttons.addStretch()
        self.save_button = QPushButton("保存更改"); self.save_button.setProperty("role", "primary")
        self.cancel_button = QPushButton("取消更改"); self.close_button = QPushButton("关闭")
        for button in (self.save_button, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.save_button.clicked.connect(self.save)
        self.cancel_button.clicked.connect(self.reset_changes)
        self.close_button.clicked.connect(self.close)
        self.panel.changed.connect(self.update_state); self.panel.busy_changed.connect(self.update_state)
        self._paste_shortcut = install_paste_shortcut(self, self.panel)
        self.update_state()

    def update_state(self, *_):
        dirty = self.panel.is_dirty()
        self.save_button.setEnabled(dirty and not self.panel.busy)
        self.cancel_button.setEnabled(dirty and not self.panel.busy)
        self.close_button.setEnabled(not self.panel.busy)

    def save(self):
        try:
            changes = self.panel.changes()
            if changes:
                self.store.commit_attachment_changes(self.record_id, changes)
                self.panel.reload(); self.saved.emit()
            self.error.clear(); self.panel.status.setText("附件更改已写入芯片文件。")
            return True
        except Exception as exc:
            self.error.setText(f"保存失败：{exc}。输入已保留。")
            return False

    def reset_changes(self):
        self.panel.reload(); self.error.clear()

    def reject(self):
        if self.panel.busy:
            self.error.setText("附件正在读取，请完成后再关闭。"); return
        if self.panel.is_dirty():
            dialog = QMessageBox(self)
            dialog.setWindowTitle("未保存的附件更改"); dialog.setText("关闭前如何处理附件更改？")
            save = dialog.addButton("保存更改", QMessageBox.ButtonRole.AcceptRole)
            discard = dialog.addButton("放弃更改", QMessageBox.ButtonRole.DestructiveRole)
            dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is save:
                if not self.save():
                    return
            elif dialog.clickedButton() is not discard:
                return
        super().reject()

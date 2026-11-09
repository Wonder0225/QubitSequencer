"""Route undo and redo to shared project edits or the active dialog's drafts."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QInputDialog, QLineEdit, QPlainTextEdit, QSpinBox, QTableWidget, QWidget,
)

from .undo import EditHistory


class FormUndo(QObject):
    def __init__(self, dialog, history):
        super().__init__(dialog)
        self.history = history
        self.controls = []
        self.values = []
        types = (QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox)
        for control in dialog.findChildren(QWidget):
            if not isinstance(control, types):
                continue
            parent = control.parent()
            while parent is not None and parent is not dialog:
                if (getattr(parent, "_is_attachment_panel", False)
                        or isinstance(parent, (QComboBox, QDoubleSpinBox, QSpinBox, QTableWidget))):
                    break
                parent = parent.parent()
            if parent is not dialog:
                continue
            index = len(self.controls)
            self.controls.append(control)
            self.values.append(self.read(control))
            signal = (control.currentTextChanged if isinstance(control, QComboBox)
                      else control.valueChanged if isinstance(control, (QDoubleSpinBox, QSpinBox))
                      else control.toggled if isinstance(control, QCheckBox) else control.textChanged)
            signal.connect(lambda *_args, i=index: self.edited(i))
        self.history.add_state(lambda: self.values.copy(), self.restore)

    @staticmethod
    def read(control):
        if isinstance(control, QComboBox):
            return control.currentIndex(), control.currentText()
        if isinstance(control, (QDoubleSpinBox, QSpinBox)):
            return control.value()
        if isinstance(control, QCheckBox):
            return control.isChecked()
        return control.toPlainText() if isinstance(control, QPlainTextEdit) else control.text()

    def edited(self, index):
        with self.history.action("编辑表单", (id(self), index)):
            self.values[index] = self.read(self.controls[index])

    def restore(self, values):
        for control, value in zip(self.controls, values):
            if isinstance(control, QComboBox):
                control.setCurrentIndex(value[0])
                if control.isEditable():
                    control.setEditText(value[1])
            elif isinstance(control, (QDoubleSpinBox, QSpinBox)):
                control.setValue(value)
            elif isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, QPlainTextEdit):
                control.setPlainText(value)
            else:
                control.setText(value)
        self.values = values.copy()


def install_form_undo(dialog):
    panel = next((widget for widget in dialog.findChildren(QWidget)
                  if getattr(widget, "_is_attachment_panel", False)), None)
    history = panel.undo_history if panel is not None else EditHistory()
    dialog._form_undo = FormUndo(dialog, history)
    for widget in dialog.findChildren(QTableWidget):
        if hasattr(widget, "attach_undo"):
            widget.attach_undo(history)


class UndoController(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        QApplication.instance().installEventFilter(self)

    def dispose(self):
        QApplication.instance().removeEventFilter(self)

    def belongs(self, widget):
        while widget is not None:
            if widget is self.host:
                return True
            widget = widget.parent()
        return False

    def eventFilter(self, watched, event):
        if not isinstance(watched, QWidget) or not self.belongs(watched):
            return False
        if event.type() == QEvent.Type.Show and isinstance(watched, QInputDialog):
            if not hasattr(watched, "_form_undo"):
                install_form_undo(watched)
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        redo = event.matches(QKeySequence.StandardKey.Redo) or (
            event.key() == Qt.Key.Key_Z
            and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if not redo and not event.matches(QKeySequence.StandardKey.Undo):
            return False
        dialog = watched.window()
        form = getattr(dialog, "_form_undo", None)
        if form is not None and isinstance(watched, QLineEdit):
            parent = watched.parent()
            while parent is not None and parent is not dialog:
                if isinstance(parent, QTableWidget) and hasattr(parent, "attach_undo"):
                    return False
                parent = parent.parent()
        if form is None:
            parent = watched
            shared = False
            while parent is not None and parent is not self.host:
                shared = shared or hasattr(parent, "registry") or parent is self.host.design_panel.angle
                parent = parent.parent()
            # View filters and unrelated text fields retain Qt's native text undo.
            if not shared and isinstance(watched, (QLineEdit, QPlainTextEdit)):
                return False
        event.accept()
        if event.type() == QEvent.Type.KeyPress:
            if form is None:
                self.host.redo_edit() if redo else self.host.undo_edit()
            else:
                panel = getattr(dialog, "panel", getattr(dialog, "attachments", None))
                if panel is not None and panel.busy:
                    panel.status.setText("附件正在读取，请完成后再撤回或恢复。")
                elif (form.history.redo_steps if redo else form.history.steps):
                    try:
                        form.history.redo() if redo else form.history.undo()
                    except Exception as exc:
                        self.host._error("恢复失败" if redo else "撤回失败", exc)
                elif getattr(dialog, "_is_attachment_dialog", False):
                    self.host.redo_edit() if redo else self.host.undo_edit()
                    try:
                        dialog.store.record(dialog.record_id)
                    except KeyError:
                        QDialog.accept(dialog)
                    else:
                        dialog.panel.reload()
        return True

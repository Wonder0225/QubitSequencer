"""Behavioral acceptance checks for the 0.3 workbench.

Every window uses a temporary chip and an INI settings file. The tests exercise
selection and editing through the public controls retained by the GUI.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QStackedWidget, QToolButton

from qubit_sequencer import gui
from qubit_sequencer.storage import ChipStore


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "workbench.qchip"
        self.settings = QSettings(str(Path(self.tmp.name) / "window.ini"), QSettings.Format.IniFormat)
        self.settings.setFallbacksEnabled(False)
        self.settings_patch = patch.object(gui, "QSettings", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        with ChipStore.create(self.path, "工作台验收", "custom") as store:
            self.alpha = store.add_device("qubit", "fixed_transmon", "Q_alpha", 0, 0)
            self.beta = store.add_device("qubit", "tunable_transmon", "Q_beta", 2, 0)
            self.gamma = store.add_device("qubit", "fixed_transmon", "Q_gamma", 4, 0)
            self.coupler = store.add_device("coupler", "tunable_coupler", "C_main", 1, 0)
            self.link = store.add_link(self.alpha, self.coupler, notes="target-link")
            self.direct = store.add_coupling(self.beta, self.gamma, notes="target-coupling")
            self.line = store.add_line("XY", "DR-A", "P-alpha", devices=[self.alpha])
            store.set_parameter(self.alpha, "qubit.f01", 5.1, point="default")
            store.set_parameter(self.alpha, "qubit.f01", 5.7, point="idle")
        self.window = self.make_window()

    def make_window(self):
        window = gui.MainWindow(str(self.path))
        self.addCleanup(self.close_window, window)
        error_patch = patch.object(window, "_error", side_effect=lambda title, error: self.fail(f"{title}: {error}"))
        error_patch.start()
        self.addCleanup(error_patch.stop)
        window.show()
        self.process_events()
        return window

    def close_window(self, window):
        # Closing a dirty document deliberately prompts; cleanup must not prompt.
        if hasattr(window, "drafts"):
            window.drafts.clear()
        window.close()
        self.app.processEvents()

    def process_events(self):
        self.app.processEvents()
        self.app.processEvents()

    def select_device(self, ident):
        self.window.on_scene_clicked("device", ident)
        self.process_events()

    def bind_popup(self, ident):
        anchor = self.window.view.mapToGlobal(self.window.view.rect().center())
        self.window.device_popup.show_device(ident, anchor)
        self.process_events()

    def parameter_row(self, key):
        return next(i for i, definition in enumerate(self.window.visible_definitions) if definition["key"] == key)

    def table_header(self, column):
        item = self.window.data_table.horizontalHeaderItem(column)
        return str(item.data(Qt.ItemDataRole.UserRole) or item.text()) if item else ""

    def next_chip(self):
        path = Path(self.tmp.name) / "next.qchip"
        with ChipStore.create(path, "下一个文件", "custom") as store:
            store.add_device("qubit", "fixed_transmon", "Q_next", 0, 0)
        return path

    def choose_leave_action(self, choice):
        class ChoiceMessageBox(QMessageBox):
            def exec(prompt):
                prompt.choice = next(button for button in prompt.buttons() if button.text() == choice)
                return 0

            def clickedButton(prompt):
                return prompt.choice

        return patch.object(gui, "QMessageBox", ChoiceMessageBox)

    def assert_current_frequency(self, value):
        row = self.parameter_row("qubit.f01")
        self.assertAlmostEqual(float(self.window.parameter_table.item(row, 1).text()), value)
        row = next(i for i, definition in enumerate(self.window.device_popup.definitions)
                   if definition["key"] == "qubit.f01")
        self.assertAlmostEqual(float(self.window.device_popup.parameters.item(row, 1).text()), value)

    def test_navigation_keeps_global_working_point_visible(self):
        window = self.window
        self.assertIsInstance(window.tabs, QStackedWidget)
        self.assertEqual(window.tabs.count(), 4)
        self.assertEqual(window.navigation.count(), 4)
        window.point_combo.setCurrentText("idle")
        for page in range(4):
            with self.subTest(page=page):
                window.navigation.setCurrentRow(page)
                self.process_events()
                self.assertEqual(window.tabs.currentIndex(), page)
                self.assertTrue(window.point_combo.isVisible())
                self.assertEqual(window.point_combo.currentText(), "idle")
                window.tabs.setCurrentIndex((page + 1) % 4)
                self.process_events()
                self.assertEqual(window.navigation.currentRow(), (page + 1) % 4)

    def test_canvas_modes_are_exclusive_and_leaving_canvas_clears_endpoint(self):
        window = self.window
        actions = (window.place_action, window.place_coupler_action, window.connect_action)
        for chosen in actions:
            chosen.setChecked(True)
            self.assertEqual([action.isChecked() for action in actions], [action is chosen for action in actions])
        window.on_scene_clicked("device", self.alpha)
        self.assertEqual(window.first_endpoint, self.alpha)
        window.place_action.setChecked(True)
        self.assertIsNone(window.first_endpoint)
        window.connect_action.setChecked(True)
        window.on_scene_clicked("device", self.alpha)
        window.tabs.setCurrentIndex(1)
        self.process_events()
        self.assertIsNone(window.first_endpoint)
        self.assertFalse(any(action.isChecked() for action in actions))
        window.tabs.setCurrentIndex(0)
        window.connect_action.setChecked(True)
        window.on_scene_clicked("device", self.beta)
        self.assertEqual(window.first_endpoint, self.beta)
        self.assertEqual(len(window.store.couplings()), 1)

    def test_editors_share_draft_across_refresh_pages_and_selection(self):
        window = self.window
        self.select_device(self.alpha)
        self.bind_popup(self.alpha)
        self.assertEqual(window.detail_editor.bound, ("device", self.alpha))
        self.assertEqual(window.device_popup.editor.bound, ("device", self.alpha))
        window.label_edit.setText("Q_alpha_draft")
        self.assertEqual(window.device_popup.label.text(), "Q_alpha_draft")
        window.device_popup.notes.setText("两处共享草稿")
        self.assertEqual(window.notes_edit.text(), "两处共享草稿")
        window.device_popup.x.setValue(0.5)
        self.assertEqual(window.x_edit.value(), 0.5)
        self.assertTrue(window.drafts.dirty("device", self.alpha))
        self.assertEqual(window.store.device(self.alpha)["label"], "Q_alpha")
        window.refresh_content()
        window.point_combo.setCurrentText("idle")
        window.field_combo.setCurrentIndex(window.field_combo.findData("qubit.f01"))
        window.tabs.setCurrentIndex(1)
        window.tabs.setCurrentIndex(0)
        self.select_device(self.beta)
        self.select_device(self.alpha)
        self.bind_popup(self.alpha)
        self.assertEqual(window.label_edit.text(), "Q_alpha_draft")
        self.assertEqual(window.device_popup.notes.text(), "两处共享草稿")
        self.assertEqual(window.device_popup.x.value(), 0.5)
        window.device_popup.save()
        self.assertFalse(window.drafts.dirty("device", self.alpha))
        saved = window.store.device(self.alpha)
        self.assertEqual((saved["label"], saved["notes"], saved["x"]),
                         ("Q_alpha_draft", "两处共享草稿", 0.5))
        self.assertEqual(window.label_edit.text(), "Q_alpha_draft")

    def test_parameter_registration_preserves_unsaved_device_fields(self):
        window = self.window
        self.select_device(self.alpha)
        self.bind_popup(self.alpha)
        window.label_edit.setText("Q_parameter_draft")
        window.device_popup.notes.setText("登记参数前的未保存备注")
        definition = window.visible_definitions[self.parameter_row("qubit.f01")]
        dialog = gui.ParameterDialog(definition, "default", window)

        def accept_parameter():
            dialog.value.setText("6.25")
            dialog.point.setText("idle")
            dialog.source.setText("acceptance-test")
            return QDialog.DialogCode.Accepted

        with patch.object(gui, "ParameterDialog", return_value=dialog), \
             patch.object(dialog, "exec", side_effect=accept_parameter):
            window.device_popup.edit_parameter(self.parameter_row("qubit.f01"), 0)
        self.process_events()
        self.assertEqual(window.point_combo.currentText(), "idle")
        self.assertEqual(window.store.current_values("idle")[(self.alpha, "qubit.f01")], 6.25)
        self.assertEqual(window.store.device(self.alpha)["label"], "Q_alpha")
        self.assertTrue(window.drafts.dirty("device", self.alpha))
        self.assertEqual(window.label_edit.text(), "Q_parameter_draft")
        self.assertEqual(window.device_popup.notes.text(), "登记参数前的未保存备注")
        self.assert_current_frequency(6.25)

    def test_parameter_write_failure_reuses_inputs_and_cancel_creates_no_record(self):
        window = self.window
        self.select_device(self.alpha)
        window.notes_edit.setText("参数失败时保留的器件草稿")
        records_before = [dict(record) for record in window.store.records()]
        definition = window.visible_definitions[self.parameter_row("qubit.f01")]
        dialog = gui.ParameterDialog(definition, "default", window)
        executions = []

        def fail_then_cancel():
            executions.append(dialog)
            if len(executions) == 1:
                dialog.value.setText("6.25")
                dialog.point.setText("custom_point")
                dialog.bias.setText("0.33")
                dialog.bias_unit.setText("mA")
                dialog.source.setText("retry-source")
                dialog.notes.setText("失败后保留的参数备注")
                return QDialog.DialogCode.Accepted
            self.assertEqual(dialog.value.text(), "6.25")
            self.assertEqual(dialog.point.text(), "custom_point")
            self.assertEqual(dialog.bias.text(), "0.33")
            self.assertEqual(dialog.bias_unit.text(), "mA")
            self.assertEqual(dialog.source.text(), "retry-source")
            self.assertEqual(dialog.notes.text(), "失败后保留的参数备注")
            self.assertIn("simulated write failure", dialog.error.text())
            return QDialog.DialogCode.Rejected

        with patch.object(gui, "ParameterDialog", return_value=dialog) as constructor, \
             patch.object(dialog, "exec", side_effect=fail_then_cancel), \
             patch.object(window.store, "set_parameter", side_effect=OSError("simulated write failure")) as write:
            window.edit_parameter(self.parameter_row("qubit.f01"), 0)
        constructor.assert_called_once()
        write.assert_called_once()
        self.assertEqual(executions, [dialog, dialog])
        self.assertEqual([dict(record) for record in window.store.records()], records_before)
        self.assertEqual(window.point_combo.currentText(), "default")
        self.assertTrue(window.drafts.dirty("device", self.alpha))
        self.assertEqual(window.notes_edit.text(), "参数失败时保留的器件草稿")

    def test_failed_save_keeps_draft_and_reset_updates_both_editors(self):
        window = self.window
        self.select_device(self.alpha)
        self.bind_popup(self.alpha)
        window.label_edit.setText("")
        window.notes_edit.setText("失败后保留")
        with self.assertRaises(ValueError):
            window.drafts.save("device", self.alpha)
        self.assertTrue(window.drafts.dirty("device", self.alpha))
        self.assertEqual(window.device_popup.label.text(), "")
        self.assertEqual(window.device_popup.notes.text(), "失败后保留")
        self.assertEqual(window.store.device(self.alpha)["label"], "Q_alpha")
        window.drafts.reset("device", self.alpha)
        self.process_events()
        self.assertFalse(window.drafts.dirty("device", self.alpha))
        self.assertEqual(window.label_edit.text(), "Q_alpha")
        self.assertEqual(window.device_popup.label.text(), "Q_alpha")
        self.assertEqual(window.notes_edit.text(), "")
        self.assertEqual(window.device_popup.notes.text(), "")

    def test_opening_next_file_saves_all_object_drafts(self):
        window = self.window
        next_path = self.next_chip()
        self.select_device(self.alpha)
        window.notes_edit.setText("保存全部：器件")
        window.on_scene_clicked("link", self.link)
        window.notes_edit.setText("保存全部：连接")
        self.assertEqual(len(window.drafts.dirty_keys()), 2)
        with self.choose_leave_action("保存全部"):
            self.assertTrue(window._open_path(str(next_path)))
        self.assertEqual(window.store.path.resolve(), next_path.resolve())
        self.assertFalse(window.drafts.dirty_keys())
        with ChipStore.open(self.path) as saved:
            self.assertEqual(saved.device(self.alpha)["notes"], "保存全部：器件")
            self.assertEqual(saved.link(self.link)["notes"], "保存全部：连接")

    def test_opening_next_file_can_discard_all_drafts(self):
        window = self.window
        next_path = self.next_chip()
        self.select_device(self.alpha)
        window.notes_edit.setText("放弃的器件草稿")
        self.select_device(self.beta)
        window.label_edit.setText("放弃的名称")
        with self.choose_leave_action("放弃更改"):
            self.assertTrue(window._open_path(str(next_path)))
        self.assertEqual(window.store.path.resolve(), next_path.resolve())
        self.assertFalse(window.drafts.dirty_keys())
        self.assertIsNone(window.detail_editor.bound)
        self.assertIsNone(window.device_popup.editor.bound)
        with ChipStore.open(self.path) as saved:
            self.assertEqual(saved.device(self.alpha)["notes"], "")
            self.assertEqual(saved.device(self.beta)["label"], "Q_beta")

    def test_canceling_file_open_or_close_preserves_file_and_all_drafts(self):
        window = self.window
        next_path = self.next_chip()
        original_store = window.store
        self.select_device(self.alpha)
        window.notes_edit.setText("取消后保留的备注")
        self.select_device(self.beta)
        window.label_edit.setText("Q_beta_pending")
        with self.choose_leave_action("取消"):
            self.assertFalse(window._open_path(str(next_path)))
            self.assertFalse(window.close())
        self.assertIs(window.store, original_store)
        self.assertTrue(window.isVisible())
        self.assertEqual(set(window.drafts.dirty_keys()), {("device", self.alpha), ("device", self.beta)})
        self.assertEqual(window.drafts.values("device", self.alpha)["notes"], "取消后保留的备注")
        self.assertEqual(window.label_edit.text(), "Q_beta_pending")
        self.assertEqual(window.store.device(self.alpha)["notes"], "")
        self.assertEqual(window.store.device(self.beta)["label"], "Q_beta")

    def test_save_all_failure_blocks_open_and_close_and_keeps_remaining_drafts(self):
        window = self.window
        next_path = self.next_chip()
        original_store = window.store
        self.select_device(self.alpha)
        window.notes_edit.setText("失败前已保存")
        self.select_device(self.beta)
        window.label_edit.setText("")
        self.select_device(self.gamma)
        window.notes_edit.setText("尚未保存的草稿")
        with self.choose_leave_action("保存全部"), patch.object(window, "_error") as error:
            self.assertFalse(window._open_path(str(next_path)))
            error.assert_called_once()
            self.assertFalse(window.close())
            self.assertEqual(error.call_count, 2)
        self.assertIs(window.store, original_store)
        self.assertTrue(window.isVisible())
        self.assertEqual(window.store.device(self.alpha)["notes"], "失败前已保存")
        self.assertFalse(window.drafts.dirty("device", self.alpha))
        self.assertEqual(set(window.drafts.dirty_keys()), {("device", self.beta), ("device", self.gamma)})
        self.assertTrue(window.drafts.error("device", self.beta))
        self.assertEqual(window.drafts.values("device", self.beta)["label"], "")
        self.assertEqual(window.notes_edit.text(), "尚未保存的草稿")
        self.assertEqual(window.store.device(self.beta)["label"], "Q_beta")
        self.assertEqual(window.store.device(self.gamma)["notes"], "")

    def test_closing_saves_all_drafts_before_releasing_store(self):
        window = self.window
        self.select_device(self.alpha)
        window.label_edit.setText("Q_alpha_on_close")
        self.select_device(self.beta)
        window.notes_edit.setText("关闭时保存")
        with self.choose_leave_action("保存全部"):
            self.assertTrue(window.close())
        self.assertIsNone(window.store)
        self.assertFalse(window.drafts.dirty_keys())
        with ChipStore.open(self.path) as saved:
            self.assertEqual(saved.device(self.alpha)["label"], "Q_alpha_on_close")
            self.assertEqual(saved.device(self.beta)["notes"], "关闭时保存")

    def test_global_working_point_refreshes_scene_and_current_tables(self):
        window = self.window
        self.select_device(self.alpha)
        self.bind_popup(self.alpha)
        window.field_combo.setCurrentIndex(window.field_combo.findData("qubit.f01"))
        window.point_combo.setCurrentText("idle")
        self.process_events()
        self.assertEqual(window.scene.point, "idle")
        self.assert_current_frequency(5.7)
        labels = [item.toPlainText() for item in window.scene.items()
                  if hasattr(item, "toPlainText") and item.data(0) == ("device", self.alpha)]
        self.assertIn("5.7", labels)
        frequency_column = next(c for c in range(window.data_table.columnCount())
                                if "qubit.f01" in self.table_header(c))
        alpha_row = next(r for r in range(window.data_table.rowCount())
                         if window.data_table.item(r, 0).text() == self.alpha)
        self.assertAlmostEqual(float(window.data_table.item(alpha_row, frequency_column).text()), 5.7)
        window.tabs.setCurrentIndex(3)
        self.process_events()
        points = {window.history_table.item(r, 6).text() for r in range(window.history_table.rowCount())}
        self.assertEqual(points, {"default", "idle"})
        window.point_combo.setCurrentText("default")
        self.process_events()
        self.bind_popup(self.alpha)
        self.assert_current_frequency(5.1)
        self.assertEqual(window.scene.point, "default")

    def table_targets(self):
        return (("比特", "Q_beta", "device", self.beta),
                ("耦合器", "C_main", "device", self.coupler),
                ("比特-耦合器连接", "target-link", "link", self.link),
                ("耦合", "target-coupling", "coupling", self.direct))

    def test_horizontal_filter_double_click_keeps_object_identity(self):
        window = self.window
        for title, needle, kind, ident in self.table_targets():
            with self.subTest(table=title):
                window.tabs.setCurrentIndex(1)
                window.table_kind.setCurrentText(title)
                window.table_orientation.setCurrentIndex(window.table_orientation.findData("horizontal"))
                window.filter_edit.setText(needle)
                visible = [r for r in range(window.data_table.rowCount()) if not window.data_table.isRowHidden(r)]
                self.assertEqual(len(visible), 1)
                window.data_table.cellDoubleClicked.emit(visible[0], 1)
                self.process_events()
                self.assertEqual(window.selected, (kind, ident))
                self.assertEqual(window.tabs.currentIndex(), 0)

    def test_vertical_object_and_field_filters_keep_double_click_identity(self):
        window = self.window
        for title, needle, kind, ident in self.table_targets():
            with self.subTest(table=title):
                window.tabs.setCurrentIndex(1)
                window.table_kind.setCurrentText(title)
                window.table_orientation.setCurrentIndex(window.table_orientation.findData("vertical"))
                window.filter_edit.setText(needle)
                visible = [c for c in range(1, window.data_table.columnCount())
                           if not window.data_table.isColumnHidden(c)]
                self.assertEqual(len(visible), 1)
                window.data_table.cellDoubleClicked.emit(0, visible[0])
                self.process_events()
                self.assertEqual(window.selected, (kind, ident))
        window.tabs.setCurrentIndex(1)
        window.table_kind.setCurrentText("比特")
        window.filter_edit.setText("比特频率")
        visible_rows = [r for r in range(window.data_table.rowCount()) if not window.data_table.isRowHidden(r)]
        self.assertEqual(len(visible_rows), 1)
        self.assertFalse(any(window.data_table.isColumnHidden(c)
                             for c in range(1, window.data_table.columnCount())))
        beta_column = window._table_object_ids.index(self.beta) + 1
        window.data_table.cellDoubleClicked.emit(visible_rows[0], beta_column)
        self.assertEqual(window.selected, ("device", self.beta))

    def test_delete_is_scoped_to_canvas_and_does_not_cross_pages(self):
        window = self.window
        self.select_device(self.gamma)
        self.assertEqual(window.delete_action.shortcutContext(), Qt.ShortcutContext.WidgetShortcut)
        self.assertIn(window.delete_action, window.view.actions())
        for page, focus in ((1, window.filter_edit), (2, window.wiring_table), (3, window.history_table)):
            with self.subTest(page=page), \
                 patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as confirm:
                window.tabs.setCurrentIndex(page)
                focus.setFocus()
                QTest.keyClick(focus, Qt.Key.Key_Delete)
                window.delete_selected()
                self.assertEqual(window.store.device(self.gamma)["label"], "Q_gamma")
                confirm.assert_not_called()
        window.tabs.setCurrentIndex(0)
        self.process_events()
        window.view.setFocus()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as confirm:
            QTest.keyClick(window.view, Qt.Key.Key_Delete)
            self.process_events()
        confirm.assert_called_once()
        with self.assertRaises(KeyError):
            window.store.device(self.gamma)

    def test_wiring_actions_require_line_selection(self):
        window = self.window
        window.tabs.setCurrentIndex(2)
        window.wiring_table.clearSelection()
        window.wiring_table.setCurrentCell(-1, -1)
        self.process_events()
        dependent = ("编辑线路", "关联器件", "解除关联", "删除线路")
        self.assertTrue(window.wiring_buttons["新增线路"].isEnabled())
        for title in dependent:
            self.assertFalse(window.wiring_buttons[title].isEnabled(), title)
        window.wiring_table.selectRow(0)
        self.process_events()
        for title in dependent:
            self.assertTrue(window.wiring_buttons[title].isEnabled(), title)
        window.wiring_table.clearSelection()
        self.process_events()
        for title in dependent:
            self.assertFalse(window.wiring_buttons[title].isEnabled(), title)

    def test_inspector_toggle_releases_canvas_space(self):
        window = self.window
        self.assertIsInstance(window.inspector_toggle, QToolButton)
        self.assertTrue(window.inspector_container.isVisible())
        initial_width = window.view.width()
        window.inspector_toggle.click()
        self.process_events()
        self.assertFalse(window.inspector_container.isVisible())
        self.assertGreater(window.view.width(), initial_width)
        window.inspector_toggle.click()
        self.process_events()
        self.assertTrue(window.inspector_container.isVisible())

    def test_canvas_toolbar_wraps_below_640_and_expands_when_commands_fit(self):
        toolbar = self.window.canvas_toolbar
        wide_width = max(1000, toolbar.modes.minimumSizeHint().width()
                         + toolbar.layer.minimumSizeHint().width()
                         + toolbar.fit.minimumSizeHint().width() + 80)

        def field_is_below_buttons():
            buttons = [button for button in toolbar.findChildren(QToolButton) if button.isVisible()]
            self.assertTrue(buttons)
            button_bottom = max(button.mapTo(toolbar, QPoint(0, button.height())).y() for button in buttons)
            return self.window.field_combo.mapTo(toolbar, QPoint(0, 0)).y() >= button_bottom

        toolbar.setFixedWidth(wide_width)
        self.process_events()
        self.assertFalse(toolbar.narrow)
        self.assertFalse(field_is_below_buttons())
        toolbar.setFixedWidth(639)
        self.process_events()
        self.assertTrue(toolbar.narrow)
        self.assertTrue(field_is_below_buttons())
        toolbar.setFixedWidth(wide_width)
        self.process_events()
        self.assertFalse(toolbar.narrow)
        self.assertFalse(field_is_below_buttons())

    def test_window_size_is_reasonable_and_restored_from_isolated_settings(self):
        window = self.window
        area = window.screen().availableGeometry()
        self.assertLessEqual(window.width(), area.width())
        self.assertLessEqual(window.height(), area.height())
        width = min(area.width(), max(window.minimumWidth(), area.width() - 30))
        height = min(area.height(), max(window.minimumHeight(), area.height() - 80))
        window.resize(width, height)
        self.process_events()
        saved_size = window.size()
        self.close_window(window)
        self.settings.sync()
        reopened = self.make_window()
        self.assertEqual(reopened.size(), saved_size)
        self.assertLessEqual(reopened.width(), area.width())
        self.assertLessEqual(reopened.height(), area.height())
        self.assertTrue(Path(self.settings.fileName()).is_relative_to(Path(self.tmp.name)))


if __name__ == "__main__":
    unittest.main()

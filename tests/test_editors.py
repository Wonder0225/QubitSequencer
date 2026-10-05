import math
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from qubit_sequencer.editors import DeviceEditor, DraftRegistry, combo_value
from qubit_sequencer.storage import ChipStore


class SharedEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChipStore.create(Path(self.tmp.name) / "editors.qchip", "编辑测试")
        self.a = self.store.add_device("qubit", "fixed_transmon", "Q_A", 2, 3)
        self.b = self.store.add_device("qubit", "tunable_transmon", "Q_B", 5, 6)
        self.c = self.store.add_device("coupler", "tunable_coupler", "C_A", 0.5, 0)
        self.registry = DraftRegistry(lambda: self.store)
        self.left = DeviceEditor(self.registry)
        self.right = DeviceEditor(self.registry)
        self.left.bind("device", self.a)
        self.right.bind("device", self.a)

    def tearDown(self):
        for editor in (self.left, self.right):
            editor.close()
            editor.deleteLater()
        self.app.processEvents()
        self.store.close()
        self.tmp.cleanup()

    def test_two_editors_sync_all_fields_and_save_zero_position(self):
        self.left.label.setText("  Q_new  ")
        self.left.subtype.setCurrentIndex(self.left.subtype.findData("tunable_transmon"))
        self.left.role.setCurrentIndex(self.left.role.findData("measure_x"))
        self.left.x.setValue(0)
        self.left.y.setValue(0)
        self.left.notes.setText("共享备注")
        self.assertEqual(self.right.label.text(), "  Q_new  ")
        self.assertEqual(self.right.subtype.currentData(), "tunable_transmon")
        self.assertEqual(self.right.role.currentText(), "X 测量")
        self.assertEqual(combo_value(self.right.role), "measure_x")
        self.assertEqual((self.right.x.value(), self.right.y.value()), (0, 0))
        self.assertEqual(self.right.notes.text(), "共享备注")
        self.assertEqual(self.registry.dirty_keys(), [("device", self.a)])
        self.assertEqual(self.store.device(self.a)["label"], "Q_A")

        self.registry.save("device", self.a)
        saved = self.store.device(self.a)
        self.assertEqual(saved["label"], "Q_new")
        self.assertEqual(saved["role"], "measure_x")
        self.assertEqual((saved["x"], saved["y"]), (0, 0))
        self.assertEqual(self.left.label.text(), "Q_new")
        self.assertEqual(self.right.label.text(), "Q_new")
        self.assertFalse(self.registry.dirty("device", self.a))

    def test_custom_role_preserves_input_and_typing_cursor(self):
        self.left.role.setEditText("自定义角色")
        self.assertEqual(combo_value(self.right.role), "自定义角色")
        self.left.label.setText("Q_suffix")
        self.left.label.setCursorPosition(2)
        QTest.keyClicks(self.left.label, "new_")
        self.assertEqual(self.left.label.text(), "Q_new_suffix")
        self.assertEqual(self.right.label.text(), "Q_new_suffix")
        self.assertEqual(self.left.label.cursorPosition(), 6)
        self.right.notes.setText("另一个编辑器改动")
        self.assertEqual(self.left.label.cursorPosition(), 6)
        self.registry.save("device", self.a)
        self.assertEqual(self.store.device(self.a)["role"], "自定义角色")

    def test_narrow_editor_keeps_both_coordinates_inside_form(self):
        self.left.show()
        for width in (236, 260, 320):
            with self.subTest(width=width):
                self.left.resize(width, 320)
                self.app.processEvents()
                self.assertEqual(self.left.width(), width)
                for spin in (self.left.x, self.left.y):
                    self.assertGreaterEqual(spin.width(), 60)
                    self.assertLessEqual(spin.mapTo(self.left, spin.rect().topRight()).x(), width)

    def test_clean_draft_refreshes_external_saved_fields(self):
        self.store.update_device(self.a, label="数据库名称", role="data", notes="已保存")
        self.registry.ensure("device", self.a)
        self.assertEqual(self.left.label.text(), "数据库名称")
        self.assertEqual(self.right.role.currentData(), "data")
        self.assertEqual(self.right.notes.text(), "已保存")
        self.assertFalse(self.registry.dirty("device", self.a))

    def test_parameter_and_editor_refresh_preserve_draft(self):
        self.left.label.setText("Q_draft")
        self.right.notes.setText("未保存")
        self.store.set_parameter(self.a, "qubit.f01", 0, point="idle")
        self.left.bind("device", self.a)
        self.right.bind("device", self.a)
        self.assertEqual(self.registry.values("device", self.a)["label"], "Q_draft")
        self.assertEqual(self.left.notes.text(), "未保存")
        self.assertEqual(self.store.current_values("idle")[(self.a, "qubit.f01")], 0)
        self.assertEqual(self.store.device(self.a)["label"], "Q_A")
        self.assertTrue(self.registry.dirty("device", self.a))

    def test_switch_clear_save_and_reset_preserve_each_object(self):
        self.left.label.setText("A_draft")
        self.left.bind("device", self.b)
        self.left.notes.setText("B_draft")
        self.assertEqual(self.right.label.text(), "A_draft")
        self.right.clear()
        self.right.bind("device", self.b)
        self.assertEqual(self.right.notes.text(), "B_draft")
        self.registry.save("device", self.b)
        self.assertEqual(self.left.notes.text(), "B_draft")
        self.assertFalse(self.registry.dirty("device", self.b))
        self.left.bind("device", self.a)
        self.right.bind("device", self.a)
        self.assertEqual(self.left.label.text(), "A_draft")
        self.registry.reset("device", self.a)
        self.assertEqual(self.left.label.text(), "Q_A")
        self.assertEqual(self.right.label.text(), "Q_A")
        self.assertEqual(self.registry.dirty_keys(), [])

    def test_save_failure_keeps_values_and_reports_to_both_editors(self):
        self.left.label.setText("Q_draft")
        with patch.object(self.store, "update_device", side_effect=sqlite3.OperationalError("disk error")):
            with self.assertRaises(sqlite3.OperationalError):
                self.registry.save("device", self.a)
        self.assertEqual(self.registry.values("device", self.a)["label"], "Q_draft")
        self.assertTrue(self.registry.dirty("device", self.a))
        self.assertIn("disk error", self.left.error_label.text())
        self.assertIn("disk error", self.right.error_label.text())
        self.assertEqual(self.store.device(self.a)["label"], "Q_A")
        self.registry.save("device", self.a)
        self.assertEqual(self.registry.error("device", self.a), "")
        self.assertEqual(self.right.error_label.text(), "")
        self.assertFalse(self.registry.dirty("device", self.a))

    def test_save_rejects_empty_name_and_nonfinite_coordinates(self):
        self.left.label.setText("   ")
        with self.assertRaisesRegex(ValueError, "名称"):
            self.registry.save("device", self.a)
        self.assertEqual(self.left.label.text(), "   ")
        self.assertTrue(self.registry.dirty("device", self.a))
        self.registry.reset("device", self.a)
        for value in (math.inf, -math.inf, math.nan):
            self.registry.update("device", self.a, "x", value)
            with self.assertRaisesRegex(ValueError, "有限"):
                self.registry.save("device", self.a)
            self.assertTrue(self.registry.dirty("device", self.a))
            self.assertEqual(self.store.device(self.a)["x"], 2)
            self.registry.reset("device", self.a)

    def test_connection_notes_share_save_and_reset(self):
        coupling = self.store.add_coupling(self.a, self.b)
        link = self.store.add_link(self.a, self.c)
        for kind, ident, reader in (("coupling", coupling, self.store.coupling),
                                    ("link", link, self.store.link)):
            self.left.bind(kind, ident)
            self.right.bind(kind, ident)
            self.assertTrue(self.left.label.isHidden())
            self.assertTrue(self.left.coordinates.isHidden())
            self.left.notes.setText("连接草稿")
            self.assertEqual(self.right.notes.text(), "连接草稿")
            self.registry.save(kind, ident)
            self.assertEqual(reader(ident)["notes"], "连接草稿")
            self.right.notes.setText("放弃的草稿")
            self.registry.reset(kind, ident)
            self.assertEqual(self.left.notes.text(), "连接草稿")
            self.assertEqual(self.right.notes.text(), "连接草稿")

    def test_save_preserves_external_changes_to_untouched_fields(self):
        self.left.notes.setText("编辑备注")
        self.store.update_device(self.a, label="外部名称", x=1.5)
        self.registry.save("device", self.a)
        saved = self.store.device(self.a)
        self.assertEqual(saved["label"], "外部名称")
        self.assertEqual(saved["x"], 1.5)
        self.assertEqual(saved["notes"], "编辑备注")
        self.assertEqual(self.right.label.text(), "外部名称")
        self.assertEqual(self.left.x.value(), 1.5)

    def test_deleted_objects_and_registry_clear_unbind_editors(self):
        self.left.notes.setText("草稿")
        self.store.delete_device(self.a)
        self.registry.discard_missing()
        self.assertEqual(self.registry.dirty_keys(), [])
        self.assertIsNone(self.left.bound)
        self.assertIsNone(self.right.bound)
        self.left.bind("device", self.b)
        self.left.notes.setText("另一个草稿")
        self.registry.clear()
        self.assertIsNone(self.left.bound)
        self.assertEqual(self.registry.dirty_keys(), [])


if __name__ == "__main__":
    unittest.main()

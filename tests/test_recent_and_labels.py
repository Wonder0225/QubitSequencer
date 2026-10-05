import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication
from openpyxl import load_workbook

from qubit_sequencer import gui
from qubit_sequencer.exports import build_tables, export_xlsx
from qubit_sequencer.recent_files import RecentFiles, RecentFilesDialog
from qubit_sequencer.storage import ChipStore, beijing_timestamp


class TimeAndLabelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "chip.qchip"
        self.store = ChipStore.create(self.path, "时区与标签")
        self.addCleanup(self.store.close)
        self.q = self.store.add_device("qubit", "fixed_transmon", "Q0", 0, 0)

    def test_default_timestamps_are_beijing_for_all_objects(self):
        s = self.store
        b = s.add_device("qubit", "fixed_transmon", "Q1", 1, 0)
        c = s.add_device("coupler", "fixed_coupler", "C0", .5, 0)
        coupling = s.add_coupling(self.q, b, "coupler", c)
        s.set_parameter(self.q, "qubit.f01", 5.0)
        calibration = s.add_calibration(self.q, "test", {})
        times = [s.meta()["created_at"], s.device(self.q)["created_at"],
                 s.coupling(coupling)["created_at"], s.links()[0]["created_at"],
                 s.records()[0]["observed_at"],
                 s.db.execute("SELECT measured_at FROM calibration_records WHERE id=?", (calibration,)).fetchone()[0]]
        self.assertTrue(all(datetime.fromisoformat(value).utcoffset() == timedelta(hours=8) for value in times))

    def test_explicit_offset_naive_and_invalid_inputs(self):
        self.assertEqual(beijing_timestamp("2026-10-05T01:02:03Z"), "2026-10-05T09:02:03+08:00")
        self.assertEqual(beijing_timestamp("2026-10-05T01:02:03"), "2026-10-05T01:02:03+08:00")
        ident = self.store.set_parameter(self.q, "qubit.f01", 5.0)
        with self.assertRaises(ValueError):
            self.store.set_parameter(self.q, "qubit.f01", 6.0, observed_at="invalid")
        self.assertEqual(self.store.records(current_only=True)[0]["id"], ident)

    def test_legacy_read_labels_and_times_preserve_storage_and_chronological_sort(self):
        s = self.store
        with s.db:
            s.db.execute("UPDATE parameter_definitions SET label='比特频率 f01' WHERE key='qubit.f01'")
        old = s.set_parameter(self.q, "qubit.f01", 5.0)
        with s.db:
            s.db.execute("UPDATE parameter_records SET observed_at=? WHERE id=?",
                         ("2026-10-05T10:00:00+00:00", old))
        s.set_parameter(self.q, "qubit.f01", 6.0, observed_at="2026-10-05T12:00:00+08:00")
        self.assertEqual(s.records()[0]["id"], old)
        self.assertEqual(s.definition("qubit.f01")["label"], "比特频率 f_{10}")
        self.assertEqual(s.db.execute("SELECT label FROM parameter_definitions WHERE key='qubit.f01'").fetchone()[0], "比特频率 f01")
        history = build_tables(s, include_history=True)["参数历史"][1]
        self.assertEqual(history[0][9], "2026-10-05T18:00:00+08:00")
        self.assertEqual(s.records()[0]["observed_at"], "2026-10-05T10:00:00+00:00")
        xlsx = Path(self.tmp.name) / "parameters.xlsx"
        export_xlsx(s, xlsx, include_history=True)
        with closing(load_workbook(xlsx, read_only=True)) as wb:
            self.assertEqual(wb["参数历史"].cell(2, 10).value, "2026-10-05T18:00:00+08:00")

    def test_custom_labels_are_not_replaced(self):
        with self.store.db:
            self.store.db.execute("UPDATE parameter_definitions SET label=? WHERE key='qubit.f01'", (r"\nu_{q}^{idle}",))
        self.assertEqual(self.store.definition("qubit.f01")["label"], r"\nu_{q}^{idle}")


class RecentAndFormulaGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = QSettings(str(self.root / "recent.ini"), QSettings.Format.IniFormat)
        self.settings.setFallbacksEnabled(False)
        self.settings_patch = patch.object(gui, "QSettings", return_value=self.settings)
        self.settings_patch.start(); self.addCleanup(self.settings_patch.stop)
        self.paths = [self.root / name / "chip.qchip" for name in ("one", "two")]
        for path in self.paths:
            with ChipStore.create(path, path.parent.name) as store:
                store.add_device("qubit", "fixed_transmon", "Q0", 0, 0)

    def make_window(self):
        window = gui.MainWindow(str(self.paths[0]))
        self.addCleanup(self.close_window, window)
        return window

    def close_window(self, window):
        window.drafts.clear(); window.close()

    def test_recent_dedup_order_persistence_limit_and_clear_only_preferences(self):
        recent = RecentFiles(self.settings)
        for i in range(25):
            recent.record(self.root / f"{i}.qchip")
        recent.record(self.paths[0]); recent.record(self.paths[1]); recent.record(self.paths[0])
        saved = RecentFiles(QSettings(str(self.root / "recent.ini"), QSettings.Format.IniFormat))
        self.assertEqual(len(saved.paths()), 20)
        self.assertEqual(saved.paths()[:2], [str(self.paths[0].resolve()), str(self.paths[1].resolve())])
        recent.clear()
        self.assertEqual(recent.paths(), [])
        self.assertTrue(all(path.is_file() for path in self.paths))

    def test_recent_open_success_cancel_failure_menu_and_filter(self):
        w = self.make_window()
        self.assertTrue(w._open_path(str(self.paths[1])))
        w.selected = ("device", w.store.devices()[0]["id"]); w.refresh_detail()
        w.label_edit.setText("draft")
        with patch.object(w, "_leave_file", return_value=False):
            self.assertFalse(w._open_path(str(self.paths[0])))
        self.assertEqual(w.recent_files.paths()[0], str(self.paths[1].resolve()))
        w.drafts.clear()
        with patch.object(w, "_error") as error:
            w._open_path(str(self.root / "missing.qchip"))
        error.assert_called_once()
        self.assertEqual(len(w.recent_files.paths()), 2)
        w._refresh_recent_menu()
        self.assertIn("two", w.recent_menu.actions()[0].text())
        self.assertEqual(w.recent_action.shortcut().toString(), "Ctrl+R")
        dialog = RecentFilesDialog(w.recent_files, w)
        dialog.search.setText("one")
        self.assertEqual(dialog.files.count(), 1)
        self.assertEqual(dialog.selected_path(), str(self.paths[0].resolve()))
        self.paths[0].unlink()
        dialog.selection_changed()
        self.assertFalse(dialog.open_button.isEnabled())
        dialog.clear_history()
        self.assertTrue(self.paths[1].is_file())

    def test_formula_names_render_everywhere_and_layers_group_by_scope(self):
        w = self.make_window()
        w.store.add_definition("qubit.custom_omega", r"\omega_{q}^{idle}", "qubit", unit="MHz")
        w._refresh_field_options()
        order = [w.store.definition(w.field_combo.itemData(i))["scope"]
                 for i in range(1, 1+len(w.store.definitions()))]
        ranks = {"qubit": 0, "coupling": 1, "link": 2, "coupler": 3}
        self.assertEqual([ranks[scope] for scope in order], sorted(ranks[scope] for scope in order))
        self.assertIsInstance(w.field_combo, gui.TexComboBox)
        definition = w.store.definition("qubit.custom_omega")
        dialog = gui.ParameterDialog(definition, "default", w)
        self.assertIn("<sub>q</sub>", dialog.parameter_title.text())
        self.assertIn("<sup>idle</sup>", dialog.parameter_title.text())
        custom = gui.DefinitionDialog("qubit", w)
        custom.label.setText(r"f_{10}")
        self.assertEqual(custom.preview.text(), "f<sub>10</sub>")
        qid = w.store.devices()[0]["id"]
        w.store.set_parameter(qid, definition["key"], 5.1)
        w.field_combo.setCurrentIndex(w.field_combo.findData(definition["key"]))
        w.show(); self.app.processEvents()
        self.assertFalse(w.field_combo.grab().isNull())
        texts = [item.toHtml() for item in w.scene.items() if hasattr(item, "toHtml")]
        self.assertTrue(any("ω" in text and "vertical-align:sub" in text for text in texts))

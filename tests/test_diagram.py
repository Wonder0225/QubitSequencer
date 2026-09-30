import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QInputDialog

from qubit_sequencer.diagram import ChipScene, export_diagram
from qubit_sequencer.gui import MainWindow
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.templates import rotated_surface_code


class DiagramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_gui_and_image_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chip.qchip"
            with ChipStore.create(path, "图像测试", "surface") as store:
                rotated_surface_code(store, 3, "direct")
                q = store.devices("qubit")[0]
                store.set_parameter(q["id"], "qubit.f01", 5.1)
                store.add_line("XY", "DR-A", "P12", devices=[q["id"]])
                for field, ext in (("", "png"), ("qubit.f01", "png"),
                                   ("coupling.g_direct", "svg"), ("device.role", "svg"),
                                   ("wiring.port", "png")):
                    out = Path(tmp) / f"{field or 'topology'}.{ext}"
                    export_diagram(store, out, field, width=1200)
                    self.assertGreater(out.stat().st_size, 1000)
                    if ext == "svg":
                        self.assertIn("<svg", out.read_text(encoding="utf-8"))
            window = MainWindow(str(path))
            window.show()
            self.app.processEvents()
            self.assertEqual(window.data_table.rowCount(), 17)
            self.assertTrue(window.data_table.isColumnHidden(0))
            self.assertIsNotNone(window.scene)
            selected_id = window.store.devices("qubit")[0]["id"]
            center = window.scene._device_items[selected_id].boundingRect().center()
            QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton,
                             pos=window.view.mapFromScene(center))
            self.assertEqual(window.selected, ("device", selected_id))
            self.assertEqual(window.parameter_table.rowCount(), len(window.store.definitions("qubit")))
            self.assertIn(selected_id, window.scene._animations)
            QTest.qWait(420)
            self.assertNotIn(selected_id, window.scene._animations)
            self.assertEqual(window.scene._device_items[selected_id].scale(), 1.0)
            window.close()

    def test_custom_gui_place_connect_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.qchip"
            with ChipStore.create(path, "自定义", "custom") as store:
                store.set_meta("grid_rows", "4")
                store.set_meta("grid_columns", "4")
            window = MainWindow(str(path))
            window.show()
            self.app.processEvents()
            initial_scale = window.view.transform().m11()
            window.view.zoom_by(1.25)
            self.assertGreater(window.view.transform().m11(), initial_scale)
            window.fit_diagram()
            old_left = window.scene.sceneRect().left()
            window.view.horizontalScrollBar().setValue(window.view.horizontalScrollBar().minimum())
            self.app.processEvents()
            self.assertLess(window.scene.sceneRect().left(), old_left)
            window.place_action.setChecked(True)
            with patch.object(QInputDialog, "getText", side_effect=[("Q_A", True), ("Q_B", True)]), \
                 patch.object(QInputDialog, "getItem", return_value=("可调 Transmon", True)):
                window.on_blank_clicked(0, 0)
                window.on_blank_clicked(8, 6)
            qubits = window.store.devices("qubit")
            self.assertEqual({q["label"] for q in qubits}, {"Q_A", "Q_B"})
            self.assertIn((8, 6), {(q["x"], q["y"]) for q in qubits})
            window.connect_action.setChecked(True)
            window.on_scene_clicked("device", qubits[0]["id"])
            window.on_scene_clicked("device", qubits[1]["id"])
            window.connection_mode.setCurrentIndex(1)
            window.on_scene_clicked("device", qubits[0]["id"])
            window.on_scene_clicked("device", qubits[1]["id"])
            self.assertEqual({c["mode"] for c in window.store.couplings()}, {"direct", "coupler"})
            self.assertEqual(len(window.store.links()), 2)
            link = window.store.links()[0]
            window.on_scene_clicked("link", link["id"])
            self.assertEqual(window.selected, ("link", link["id"]))
            self.assertEqual(window.parameter_table.rowCount(), len(window.store.definitions("link")))
            window.connect_action.setChecked(False)
            window.on_scene_clicked("device", qubits[0]["id"])
            window.label_edit.setText("Q_A_renamed")
            window.save_device()
            window.close()
            with ChipStore.open(path) as reopened:
                self.assertEqual({q["label"] for q in reopened.devices("qubit")}, {"Q_A_renamed", "Q_B"})
                self.assertEqual(len(reopened.devices("coupler")), 1)
                self.assertEqual(len(reopened.links()), 2)
                self.assertEqual(reopened.validate(), [])

    def test_manual_coupler_link_builds_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual.qchip"
            with ChipStore.create(path, "手工耦合", "custom") as store:
                a = store.add_device("qubit", "fixed_transmon", "A", 0, 0)
                b = store.add_device("qubit", "fixed_transmon", "B", 1, 0)
                c = store.add_device("coupler", "fixed_coupler", "C", .5, 0)
            window = MainWindow(str(path))
            window.connect_action.setChecked(True)
            window.on_scene_clicked("device", a)
            window.on_scene_clicked("device", c)
            self.assertEqual(len(window.store.links()), 1)
            self.assertEqual(len(window.store.couplings()), 0)
            window.on_scene_clicked("device", c)
            window.on_scene_clicked("device", b)
            self.assertEqual(len(window.store.links()), 2)
            self.assertEqual(len(window.store.couplings()), 1)
            window.store.set_parameter(window.store.links()[0]["id"], "link.g_qc", 35.0)
            window.refresh_content()
            window.connect_action.setChecked(False)
            window.show()
            self.app.processEvents()
            center = window.scene._device_items[c].boundingRect().center()
            QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton,
                             pos=window.view.mapFromScene(center))
            self.app.processEvents()
            self.assertEqual(window.selected, ("device", c))
            self.assertIn(c, window.scene._animations)
            self.assertTrue(window.device_popup.isVisible())
            window.device_popup.notes.setText("鼠标旁编辑")
            window.device_popup.save()
            self.assertEqual(window.store.device(c)["notes"], "鼠标旁编辑")
            first_link = next(link for link in window.store.links() if link["qubit_id"] == a)
            from qubit_sequencer.diagram import CELL, PAD
            midpoint = window.view.mapFromScene(PAD + .29 * CELL, PAD)
            QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, pos=midpoint)
            self.assertEqual(window.selected, ("link", first_link["id"]))
            window.close()
            with ChipStore.open(path) as reopened:
                self.assertEqual(len(reopened.links()), 2)
                self.assertEqual(reopened.validate(), [])

    def test_legend_only_lists_present_components_and_parameter_is_centered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.qchip"
            with ChipStore.create(path, "单比特", "custom") as store:
                qid = store.add_device("qubit", "fixed_transmon", r"Q_{1}", 0, 0)
                store.set_parameter(qid, "qubit.f01", 5.0)
                topology = ChipScene(store)
                labels = {item.toPlainText() for item in topology.items() if hasattr(item, "toPlainText")}
                self.assertIn("● 比特", labels)
                self.assertNotIn("◆ 耦合器", labels)
                self.assertNotIn("● 数据比特", labels)
                parameter = ChipScene(store, "qubit.f01")
                values = [item for item in parameter.items()
                          if hasattr(item, "toPlainText") and item.data(0) == ("device", qid)]
                self.assertEqual([item.toPlainText() for item in values], ["5"])
                self.assertAlmostEqual(values[0].sceneBoundingRect().center().x(),
                                       parameter._device_items[qid].sceneBoundingRect().center().x(), delta=2)
            window = MainWindow(str(path))
            window.on_scene_clicked("device", qid)
            self.assertIn("<sub>1</sub>", window.selected_title.text())
            window.device_popup.device_id = qid
            window.device_popup.reload()
            self.assertIn("<sub>1</sub>", window.device_popup.title.text())
            window.close()


if __name__ == "__main__":
    unittest.main()

"""Calibrated spectra must keep missing data, workpoints and read-only viewing distinct."""

import os
from xml.etree import ElementTree

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from qubit_sequencer import gui
from qubit_sequencer.energy_levels import EnergyScene, EnergyLevelsPanel, QubitLevels, qubit_levels
from qubit_sequencer.storage import ChipStore


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "spectra.qchip"
    with ChipStore.create(path, "能级模拟测试") as store:
        ids = {label: store.add_device("qubit", "tunable_transmon", label, i, 0)
               for i, label in enumerate(("Q10", "Q2", "Q1", "Q0", "Q3", "Q4"))}
        store.add_device("coupler", "tunable_coupler", "C1", .5, 0)
        store.set_parameter(ids["Q1"], "qubit.f01", 5.2)
        store.set_parameter(ids["Q1"], "qubit.anharmonicity", -200)
        store.set_parameter(ids["Q2"], "qubit.f01", 5.3)
        store.set_parameter(ids["Q2"], "qubit.anharmonicity", 150)
        store.set_parameter(ids["Q0"], "qubit.f01", 0)
        store.set_parameter(ids["Q0"], "qubit.anharmonicity", 0)
        store.set_parameter(ids["Q10"], "qubit.f01", 5.5)
        store.set_parameter(ids["Q3"], "qubit.design_f01", 5.1)
        store.set_parameter(ids["Q4"], "qubit.f01", 5.4)
        store.set_parameter(ids["Q4"], "qubit.f01", None)
        store.set_parameter(ids["Q1"], "qubit.f01", 4.8, point="idle")
    return path, ids


def test_current_calibrated_values_signed_alpha_and_no_design_fallback(project):
    path, ids = project
    with ChipStore.open(path) as store:
        before = store.db.total_changes
        rows = qubit_levels(store)
        assert [row.label for row in rows] == ["Q0", "Q1", "Q2", "Q3", "Q4", "Q10"]
        values = {row.label: row for row in rows}
        assert values["Q1"].f21 == pytest.approx(5.0)
        assert values["Q2"].f21 == pytest.approx(5.45)
        assert values["Q0"].f10 == 0 and values["Q0"].f21 == 0
        assert values["Q3"].f10 is None  # Design data does not count as a calibrated frequency.
        assert values["Q4"].f10 is None  # A cleared current value does not reuse the old measurement.
        assert values["Q10"].f10 == 5.5 and values["Q10"].f21 is None
        idle = {row.label: row for row in qubit_levels(store, "idle")}
        assert idle["Q1"].f10 == 4.8 and idle["Q1"].alpha is None
        assert all(row.f10 is None for label, row in idle.items() if label != "Q1")
        assert store.db.total_changes == before


def test_button_default_optional_transition_and_live_updates(project, tmp_path):
    app = QApplication.instance() or QApplication([])
    path, ids = project
    window = gui.MainWindow(str(path)); window.resize(1200, 800); window.show()
    try:
        app.processEvents()
        assert window.energy_button.x() > window.design_button.x()
        window.energy_button.click(); app.processEvents()
        panel = window.energy_panel
        assert panel.isVisible() and not panel.isWindow() and not panel.show_f21.isChecked()
        assert window.chip_views.currentWidget() is panel
        assert window.energy_button.text() == '返回构型图'
        assert not window.place_action.isEnabled()
        assert len(panel.figure.legend_items) == 6
        window.design_button.click()
        assert window.chip_views.currentWidget() is window.design_panel
        assert window.energy_button.text() == "查看能级图"
        window.energy_button.click()
        assert window.chip_views.currentWidget() is panel
        assert len(panel.figure.level_items) == 4
        assert all(key[1] == "f10" for key in panel.figure.level_items)
        for item in panel.figure.level_items.values():
            assert item.line().y1() == item.line().y2()
            assert item.pen().style() == Qt.PenStyle.SolidLine
        assert "未标定" in panel.figure.legend_items[ids["Q3"]].toPlainText()
        history_size = len(window.undo_history.steps)
        panel.show_f21.setChecked(True)
        assert len(panel.figure.level_items) == 7
        assert panel.figure.level_items[(ids["Q1"], "f21")].pen().style() == Qt.PenStyle.DashLine
        assert "非谐未登记" in panel.figure.legend_items[ids["Q10"]].toPlainText()
        panel.style_combo.setCurrentIndex(1)
        assert all(item.line().x1() == item.line().x2() for item in panel.figure.level_items.values())
        assert len(window.undo_history.steps) == history_size
        panel.spacing_slider.setValue(40)
        assert panel.spacing.value() == 40 and panel.figure.spacing == 40
        assert len(window.undo_history.steps) == history_size
        window.point_combo.setCurrentText("idle")
        assert panel.point == "idle" and len(panel.figure.level_items) == 1
        window.point_combo.setCurrentText("default")
        window.store.update_device(ids["Q1"], label="Q_{renamed}")
        window.refresh_content()
        assert "renamed" in panel.figure.legend_items[ids["Q1"]].toPlainText()
        window.store.set_parameter(ids["Q10"], "qubit.anharmonicity", -250)
        window.refresh_parameter_views()
        assert (ids["Q10"], "f21") in panel.figure.level_items
        window.undo_edit()
        assert (ids["Q10"], "f21") not in panel.figure.level_items
        window.set_theme("dark", persist=False)
        assert panel.theme == "dark"
        other = tmp_path / "empty.qchip"
        with ChipStore.create(other, "无比特测试"):
            pass
        assert window._open_path(str(other))
        assert panel.rows == [] and not panel.export_button.isEnabled()
        window.energy_button.click()
        assert window.chip_views.currentIndex() == 0
        window.energy_button.click()
        assert window.energy_panel is panel and window.chip_views.currentWidget() is panel
    finally:
        window.drafts.clear(); window.close(); app.processEvents()


def test_exports_include_all_names_and_statuses(project, tmp_path):
    app = QApplication.instance() or QApplication([])
    path, ids = project
    with ChipStore.open(path) as store:
        scene = EnergyScene(qubit_levels(store), "能级模拟测试", "default", show_f21=True)
    scene.export(tmp_path / "spectrum.png")
    scene.export(tmp_path / "spectrum.svg")
    image = QImage(str(tmp_path / "spectrum.png"))
    assert not image.isNull() and image.width() == 2000
    text = "".join(ElementTree.parse(tmp_path / "spectrum.svg").getroot().itertext())
    assert all(label in text for label in ids)
    assert "未标定" in text and "已标定" in text and "非谐未登记" in text
    assert "计算值" in text


def test_spacing_controls_both_layouts_and_fit_displays_full_figure():
    app = QApplication.instance() or QApplication([])
    host = QWidget(); host.resize(1100, 720)
    layout = QVBoxLayout(host)
    panel = EnergyLevelsPanel(host); layout.addWidget(panel)
    rows = [QubitLevels(str(i), f"Q{i}", 5.1+i*.003 if i != 7 else None, -200) for i in range(40)]
    panel.refresh(rows, "四十比特模拟芯片", "default", "light")
    host.show(); app.processEvents()
    try:
        panel.spacing.setValue(200); app.processEvents()
        wide = panel.figure.content_rect.width()
        panel.view.fit_graph(); app.processEvents()
        rendered = panel.view.mapFromScene(panel.figure.content_rect).boundingRect()
        assert panel.view.viewport().rect().adjusted(-4, -4, 4, 4).contains(rendered)
        panel.spacing_slider.setValue(24); panel.show_values.setChecked(False)
        assert panel.figure.content_rect.width() < wide
        assert len(panel.figure.legend_items) == 40
        first = panel.figure.level_items[("0", "f10")].line()
        second = panel.figure.level_items[("1", "f10")].line()
        assert (second.x1()+second.x2()-first.x1()-first.x2())/2 == pytest.approx(24)
        panel.show_f21.setChecked(True)
        panel.style_combo.setCurrentIndex(1)
        first = panel.figure.level_items[("0", "f10")].line()
        second = panel.figure.level_items[("1", "f10")].line()
        assert (second.y1()+second.y2()-first.y1()-first.y2())/2 == pytest.approx(24)
        compact_height = panel.figure.content_rect.height()
        panel.spacing.setValue(96)
        assert panel.spacing_slider.value() == 96
        assert panel.figure.content_rect.height() > compact_height
        assert len(panel.figure.legend_items) == 40
        assert panel.figure.rows == rows
    finally:
        host.close(); app.processEvents()

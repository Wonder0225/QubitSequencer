"""Capture the current workbench with isolated settings and a copied d=3 file."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings
from PySide6.QtWidgets import QApplication

import qubit_sequencer.gui as gui
from qubit_sequencer.diagram import export_diagram
from qubit_sequencer.recent_files import RecentFilesDialog


def _settle(app: QApplication) -> None:
    for _ in range(4):
        app.processEvents()


def _save(widget, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(target)):
        raise OSError(f"Could not save screenshot: {target}")


def main() -> None:
    example = ROOT / "examples" / "surface_d3_complete.qchip"
    source_hash = hashlib.sha256(example.read_bytes()).digest()
    pictures = ROOT / "docs" / "images"
    qa = ROOT / "output" / "qa" / "workbench"
    qa.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    original_settings = gui.QSettings
    window = None
    with tempfile.TemporaryDirectory(prefix="capture-", dir=qa) as temporary:
        scratch = Path(temporary)
        copied = scratch / example.name
        shutil.copy2(example, copied)
        settings_path = scratch / "settings.ini"
        gui.QSettings = lambda *_args, **_kwargs: QSettings(
            str(settings_path), QSettings.Format.IniFormat)
        try:
            window = gui.MainWindow(str(copied))
            window.resize(1450, 900)
            window.show()
            _settle(app)
            window.set_inspector_visible(True, persist=False)
            window.diagram_splitter.setSizes([900, 340])
            ident = next(device["id"] for device in window.store.devices()
                         if device["kind"] == "qubit" and device["role"] == "data")
            window.on_scene_clicked("device", ident)
            field = window.field_combo.findData("qubit.f01")
            if field < 0:
                raise RuntimeError("The complete d=3 demo has no qubit.f01 layer")
            window.field_combo.setCurrentIndex(field)
            window.table_orientation.setCurrentIndex(1)
            window.wiring_table.selectRow(0)
            window.recent_files.clear()
            for name in ("square_lattice.qchip", "surface_d3.qchip", "surface_d3_complete.qchip"):
                window.recent_files.record(ROOT / "examples" / name)
            for mode in ("light", "dark"):
                window.set_theme(mode, persist=False)
                window.tabs.setCurrentIndex(0)
                window.refresh_detail()
                window.fit_diagram()
                _settle(app)
                window.statusBar().clearMessage()
                recent = RecentFilesDialog(window.recent_files, window)
                recent.show(); _settle(app)
                _save(recent, pictures / ("recent-files.png" if mode == "light" else "recent-files-dark.png"))
                recent.close()
                window._refresh_recent_menu()
                window.recent_menu.popup(window.mapToGlobal(QPoint(36, 60)))
                _settle(app)
                _save(window.recent_menu, pictures / ("recent-menu.png" if mode == "light" else "recent-menu-dark.png"))
                window.recent_menu.hide()
                main_name = "main-window.png" if mode == "light" else "dark-mode.png"
                _save(window, pictures / main_name)
                preview = "surface_d3_complete_preview.png" if mode == "light" else "surface_d3_complete_dark_preview.png"
                _save(window, example.with_name(preview))
                for page, name in ((window.data_page, "data-table"), (window.fridge_page, "wiring"),
                                   (window.history_page, "history")):
                    window.tabs.setCurrentWidget(page)
                    _settle(app)
                    window.statusBar().clearMessage()
                    suffix = "" if mode == "light" else "-dark"
                    _save(window, pictures / f"{name}{suffix}.png")
                    if page is window.data_page:
                        table_name = "surface_d3_complete_table_vertical.png" if mode == "light" else "surface_d3_complete_dark_table_vertical.png"
                        _save(window, example.with_name(table_name))
                window.tabs.setCurrentIndex(0)
                window.on_scene_clicked("device", ident)
                window.device_popup.show_device(ident, window.mapToGlobal(QPoint(560, 140)))
                _settle(app)
                popup_name = "device-popup.png" if mode == "light" else "device-popup-dark.png"
                _save(window.device_popup, pictures / popup_name)
                window.device_popup.close()
            export_diagram(window.store, example.with_name("surface_d3_complete_topology.png"))
            export_diagram(window.store, example.with_name("surface_d3_complete_topology.svg"))
            export_diagram(window.store, example.with_name("surface_d3_complete_qubit_f01.png"), "qubit.f01")
            export_diagram(window.store, example.with_name("surface_d3_complete_dark_qubit_f01.png"),
                           "qubit.f01", theme="dark")
            export_diagram(window.store, example.with_name("surface_d3_complete_dark_wiring_ports.png"),
                           "wiring.port", theme="dark")
            export_diagram(window.store, example.with_name("surface_d3_complete_dark_coupling_g_effective.png"),
                           "coupling.g_effective", theme="dark")
        finally:
            if window is not None:
                window.close()
                _settle(app)
            gui.QSettings = original_settings
    if hashlib.sha256(example.read_bytes()).digest() != source_hash:
        raise RuntimeError("Screenshot capture changed the source demo file")
    print("Captured 1450x900 light/dark workbench pages and device popups.")
    print("Source d=3 file and personal QSettings were preserved.")


if __name__ == "__main__":
    main()

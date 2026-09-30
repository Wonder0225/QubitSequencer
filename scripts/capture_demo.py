"""Capture the complete d=3 example in the desktop UI for the guide/release."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from qubit_sequencer.diagram import export_diagram
from qubit_sequencer.gui import MainWindow


def main() -> None:
    example = ROOT / "examples" / "surface_d3_complete.qchip"
    app = QApplication.instance() or QApplication([])
    window = MainWindow(str(example))
    window.resize(1500, 920)
    window.show()
    app.processEvents()
    index = window.field_combo.findData("qubit.f01")
    window.field_combo.setCurrentIndex(index)
    app.processEvents()
    if not window.grab().save(str(example.with_name("surface_d3_complete_preview.png"))):
        raise OSError("Could not save demo preview")
    window.tabs.setCurrentIndex(1)
    window.table_orientation.setCurrentIndex(1)
    app.processEvents()
    if not window.grab().save(str(example.with_name("surface_d3_complete_table_vertical.png"))):
        raise OSError("Could not save vertical table preview")
    window.set_theme("dark", persist=False)
    window.tabs.setCurrentIndex(0)
    app.processEvents()
    if not window.grab().save(str(example.with_name("surface_d3_complete_dark_preview.png"))):
        raise OSError("Could not save dark demo preview")
    export_diagram(window.store, example.with_name("surface_d3_complete_dark_qubit_f01.png"),
                   "qubit.f01", theme="dark")
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    if not window.grab().save(str(example.with_name("surface_d3_complete_dark_table_vertical.png"))):
        raise OSError("Could not save dark vertical table preview")
    window.close()
    print("Captured complete d=3 desktop previews")


if __name__ == "__main__":
    main()

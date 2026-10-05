"""Reproduce workbench layout checks using isolated Qt offscreen scale simulations.

Run with ``python scripts/verify_workbench_layout.py``. This does not change the
Windows display scale, the example database, or the user's application settings.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SIZES = ((1450, 900), (960, 650))
SCALES = (1, 1.5, 2)
PAGES = ("chip", "parameters", "wiring", "history")
SIMULATION = "Qt缩放模拟；未修改或手工验证Windows系统DPI"
SCREEN_CONFIG_SOURCE = "https://raw.githubusercontent.com/qt/qtbase/dev/src/plugins/platforms/offscreen/qoffscreenintegration.cpp"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rect_data(rect) -> dict:
    return {name: getattr(rect, name)() for name in ("x", "y", "width", "height")}


def capture_scale(scale: float, output: Path, example: Path) -> dict:
    """Launch a separate Qt process because scale is read at application startup."""
    destination = output / f"layout-scale-{scale:g}"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qubit-workbench-layout-") as scratch:
        temporary = Path(scratch)
        database = temporary / "surface_d3_complete.qchip"
        shutil.copy2(example, database)
        # Qt divides this native geometry by QT_SCALE_FACTOR. The available
        # logical area therefore remains 1920x1200 at every simulated scale.
        config = {"windowFrameMargins": False, "screens": [{
            "name": "layout-simulation", "x": 0, "y": 0,
            "width": round(1920 * scale), "height": round(1200 * scale),
            "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1.0,
        }]}
        (temporary / "offscreen-screen.json").write_text(json.dumps(config), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(QT_QPA_PLATFORM="offscreen:configfile=offscreen-screen.json",
                           QT_SCALE_FACTOR=f"{scale:g}", QT_SCALE_FACTOR_ROUNDING_POLICY="PassThrough",
                           QT_ENABLE_HIGHDPI_SCALING="1", PYTHONIOENCODING="utf-8")
        for name in ("QT_SCREEN_SCALE_FACTORS", "QT_FONT_DPI", "QT_AUTO_SCREEN_SCALE_FACTOR"):
            environment.pop(name, None)
        command = [sys.executable, str(Path(__file__).resolve()), "--child-scale", str(scale),
                   "--database", str(database), "--settings", str(temporary / "isolated-window.ini"),
                   "--output", str(destination)]
        completed = subprocess.run(command, cwd=temporary, env=environment, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=180)
        (destination / "capture.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        report_path = destination / "geometry.json"
        if not report_path.exists():
            raise RuntimeError(f"Scale {scale:g} failed before producing a report: {completed.stderr[-2000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["exit_code"] = completed.returncode
        return report


def child(args) -> int:
    sys.path.insert(0, str(ROOT))
    from unittest.mock import patch
    from PySide6.QtCore import QPoint, QRect, QSettings, Qt, qVersion
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import (
        QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QScrollArea, QTableWidget, QWidget,
    )
    from qubit_sequencer import gui

    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(args.settings), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    screen = app.primaryScreen()
    report = {
        "method": SIMULATION, "qt_version": qVersion(), "requested_scale": args.child_scale,
        "platform": app.platformName(), "environment_scale": os.environ["QT_SCALE_FACTOR"],
        "screen_geometry": rect_data(screen.geometry()),
        "screen_available_geometry": rect_data(screen.availableGeometry()),
        "screen_device_pixel_ratio": screen.devicePixelRatio(),
        "screen_logical_dpi": screen.logicalDotsPerInch(),
        "temporary_database_copy": True, "temporary_ini_settings": True,
        "table_horizontal_scrolling_allowed": True,
        "inspector_vertical_scrolling_allowed": True,
        "cases": [], "issues": [],
    }

    def settle():
        for _ in range(4):
            app.processEvents()

    def inspect_widget(widget, root):
        bounds = QRect(widget.mapTo(root, QPoint()), widget.size())
        clipped = bounds.intersected(root.rect())
        scroll_parent = False
        ancestor = widget.parentWidget()
        while ancestor is not None and ancestor is not root:
            if isinstance(ancestor, QScrollArea):
                scroll_parent = True
                viewport = ancestor.viewport()
                visible = QRect(viewport.mapTo(root, QPoint()), viewport.size())
                clipped = clipped.intersected(visible)
            ancestor = ancestor.parentWidget()
        return bounds, clipped, scroll_parent

    def screenshot(root, name, requested_size=None):
        settle()
        pixmap = root.grab()
        path = output / f"{name}.png"
        if not pixmap.save(str(path)):
            raise OSError(f"Could not save {path}")
        case = {
            "name": name, "screenshot": str(path), "logical_size": [root.width(), root.height()],
            "window_geometry": rect_data(root.geometry()), "window_frame_geometry": rect_data(root.frameGeometry()),
            "image_pixel_size": [pixmap.width(), pixmap.height()],
            "widget_device_pixel_ratio": root.devicePixelRatioF(),
            "image_device_pixel_ratio": pixmap.devicePixelRatio(),
            "controls": [], "headers": [], "issues": [],
        }
        if requested_size and case["logical_size"] != list(requested_size):
            case["issues"].append(f"Window grew from requested {requested_size} to {case['logical_size']}")
        if abs(pixmap.devicePixelRatio() - args.child_scale) > .01:
            case["issues"].append("Measured image DPR differs from QT_SCALE_FACTOR")
        if not screen.availableGeometry().contains(root.frameGeometry()):
            case["issues"].append("Window or popup lies outside the virtual screen")
        for widget in root.findChildren(QWidget):
            if (not isinstance(widget, (QAbstractButton, QComboBox, QAbstractSpinBox))
                    or widget.window() is not root or not widget.isVisibleTo(root)):
                continue
            bounds, visible, scroll = inspect_widget(widget, root)
            text = widget.text() if isinstance(widget, QAbstractButton) else widget.currentText() if isinstance(widget, QComboBox) else widget.text()
            control = {"class": type(widget).__name__, "text": text, "rect": rect_data(bounds),
                       "visible_rect": rect_data(visible), "scroll_reachable": scroll,
                       "enabled": widget.isEnabled()}
            case["controls"].append(control)
            # Scroll areas can deliberately place reachable controls below their
            # viewport. Horizontal clipping of controls is never permitted.
            horizontal_clip = not visible.isEmpty() and (visible.left() > bounds.left() or visible.right() < bounds.right())
            if horizontal_clip or (not scroll and visible != bounds):
                case["issues"].append(f"Clipped control: {control['class']} {text!r}")
            if isinstance(widget, QAbstractButton) and not visible.isEmpty():
                if widget.width() < widget.minimumSizeHint().width():
                    case["issues"].append(f"Button narrower than minimum text/icon size: {text!r}")
            if isinstance(widget, QAbstractSpinBox) and not visible.isEmpty():
                edit = widget.lineEdit()
                if edit.width() < edit.fontMetrics().horizontalAdvance(edit.text()) + 3:
                    case["issues"].append(f"Spinbox value is not fully readable: {text!r}")
        for table in root.findChildren(QTableWidget):
            if table.window() is not root or not table.isVisibleTo(root):
                continue
            header = table.horizontalHeader()
            if not header.isVisible():
                continue
            header_bounds, header_visible, _scroll = inspect_widget(header, root)
            if not header_visible.isEmpty() and header_visible.height() < header_bounds.height():
                case["issues"].append("Visible table header is vertically clipped")
            entry = {"rows": table.rowCount(), "columns": table.columnCount(),
                     "height": header.height(), "horizontal_scroll_max": table.horizontalScrollBar().maximum(),
                     "sections": []}
            for column in range(table.columnCount()):
                item = table.horizontalHeaderItem(column)
                if item is None or table.isColumnHidden(column):
                    continue
                raw = item.data(Qt.ItemDataRole.UserRole) or item.text()
                doc = QTextDocument()
                doc.setDocumentMargin(0)
                doc.setDefaultFont(header.font())
                doc.setHtml(gui._header_html(str(raw), "#888888"))
                doc.setTextWidth(max(20, table.columnWidth(column) - 10))
                needed_height = doc.size().height()
                entry["sections"].append({"title": str(raw), "width": table.columnWidth(column),
                                          "text_height": needed_height})
                if needed_height > header.height() + 1:
                    case["issues"].append(f"Table header text taller than header: {raw!r}")
            case["headers"].append(entry)
        report["cases"].append(case)
        report["issues"].extend(f"{name}: {issue}" for issue in case["issues"])

    with patch.object(gui, "QSettings", return_value=settings):
        window = gui.MainWindow(str(args.database))
        try:
            if window.store is None:
                raise RuntimeError("Example copy could not be opened")
            ident = window.store.devices("qubit")[0]["id"]
            window.set_inspector_visible(True, persist=False)
            window.selected = ("device", ident)
            window.refresh_detail()
            for width, height in SIZES:
                window.resize(width, height)
                window.move(20, 20)
                window.diagram_splitter.setSizes([width - 160 - 320, 320])
                window.show()
                settle()
                for theme in ("light", "dark"):
                    window.set_theme(theme, persist=False)
                    window.field_combo.setCurrentIndex(window.field_combo.findData("qubit.f01"))
                    for page, label in enumerate(PAGES):
                        window.tabs.setCurrentIndex(page)
                        screenshot(window, f"{theme}-{width}x{height}-{label}", (width, height))
                    window.tabs.setCurrentIndex(0)
                    for field, label in (("wiring.port", "ports"), ("coupling.g_effective", "coupling")):
                        window.field_combo.setCurrentIndex(window.field_combo.findData(field))
                        screenshot(window, f"{theme}-{width}x{height}-{label}", (width, height))
                    window.device_popup.show_device(ident, window.view.mapToGlobal(window.view.rect().center()))
                    screenshot(window.device_popup, f"{theme}-{width}x{height}-popup")
                    window.device_popup.close()
        finally:
            window.drafts.clear()
            window.close()
            settle()
    (output / "geometry.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Qt scale {args.child_scale:g}: {len(report['cases'])} captures; {len(report['issues'])} geometry issues")
    return bool(report["issues"])


def contact_sheet(directory: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    files = sorted(path for path in directory.glob("*.png") if path.stem != "contact-sheet")
    cell_w, cell_h, columns = 400, 280, 4
    sheet = Image.new("RGB", (cell_w * columns, cell_h * ((len(files) + columns - 1) // columns)), "#E3E7EB")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14) if Path("C:/Windows/Fonts/arial.ttf").exists() else ImageFont.load_default()
    for index, path in enumerate(files):
        with Image.open(path) as source:
            source.thumbnail((cell_w - 12, cell_h - 34))
            x, y = (index % columns) * cell_w, (index // columns) * cell_h
            sheet.paste(source, (x + (cell_w - source.width) // 2, y + 26))
            draw.text((x + 6, y + 6), path.stem, fill="#172A3D", font=font)
    sheet.save(directory / "contact-sheet.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/qa/workbench")
    parser.add_argument("--example", type=Path, default=ROOT / "examples/surface_d3_complete.qchip")
    parser.add_argument("--child-scale", type=float)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args()
    if args.child_scale is not None:
        return child(args)
    args.output = args.output.resolve()
    args.example = args.example.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    before = digest(args.example)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(capture_scale, scale, args.output, args.example) for scale in SCALES]
        reports = [future.result() for future in futures]
    for scale in SCALES:
        contact_sheet(args.output / f"layout-scale-{scale:g}")
    unchanged = before == digest(args.example)
    summary = {"method": SIMULATION, "virtual_screen_config_source": SCREEN_CONFIG_SOURCE,
               "source_database": str(args.example), "source_sha256": before,
               "source_database_unchanged": unchanged, "sizes": SIZES, "scales": SCALES,
               "source_code_sha256": {name: digest(ROOT / "qubit_sequencer" / name)
                                      for name in ("gui.py", "workbench.py", "theme.py", "editors.py", "diagram.py")},
               "total_captures": sum(len(report["cases"]) for report in reports),
               "issues": [issue for report in reports for issue in report["issues"]],
               "reports": [str(args.output / f"layout-scale-{scale:g}/geometry.json") for scale in SCALES]}
    (args.output / "layout-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"method": SIMULATION, "captures": summary["total_captures"],
                      "issues": len(summary["issues"]), "source_unchanged": unchanged,
                      "report": str(args.output / "layout-summary.json")}, ensure_ascii=False))
    return bool(summary["issues"] or not unchanged)


if __name__ == "__main__":
    raise SystemExit(main())

"""Render the app's vector mark into PNG and a multi-resolution Windows ICO."""

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "qubit_sequencer" / "assets"


def main() -> None:
    app = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(str(ASSETS / "app-icon.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid app icon SVG")
    image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    png = ASSETS / "app-icon.png"
    ico = ASSETS / "app-icon.ico"
    if not image.save(str(png), "PNG"):
        raise OSError(png)
    with Image.open(png) as source:
        source.save(ico, format="ICO", sizes=[(16, 16), (24, 24), (32, 32),
                                               (48, 48), (64, 64), (128, 128),
                                               (256, 256)])
    app.processEvents()
    print(png)
    print(ico)


if __name__ == "__main__":
    main()

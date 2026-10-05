"""Small single-stroke SVG icons rendered at the requested device resolution."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, qVersion
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


# A shared 20-unit view box keeps stroke weight consistent at 16 and 20 px.
_SHAPES = {
    "new": '<path d="M5 2.5h7l3 3v12H5z M12 2.5v3h3 M7.5 10h5 M10 7.5v5"/>',
    "open": '<path d="M2.5 6V4.5h6l2 2h7v2 M2.5 8.5h15l-2.5 8h-13z"/>',
    "backup": '<path d="M3 8h14v9H3z M2.5 4h15v4h-15z M8 11h4"/>',
    "export": '<path d="M10 12V2.5 M6.5 6 10 2.5 13.5 6 M3.5 11v6h13v-6"/>',
    "chip": '<rect x="5" y="5" width="10" height="10" rx="1.5"/><rect x="8" y="8" width="4" height="4" rx=".5"/><path d="M7 2v3 M13 2v3 M7 15v3 M13 15v3 M2 7h3 M2 13h3 M15 7h3 M15 13h3"/>',
    "table": '<rect x="2.5" y="3.5" width="15" height="13" rx="1"/><path d="M2.5 7.5h15 M2.5 12h15 M7.5 7.5v9"/>',
    "wiring": '<rect x="2" y="3" width="5" height="4" rx="1"/><rect x="13" y="13" width="5" height="4" rx="1"/><path d="M7 5h7v5H6v5h7"/>',
    "history": '<path d="M3 6a7 7 0 1 1-0.1 8 M3 2.5V6h3.5 M10 6v4l3 2"/>',
    "select": '<path d="m4 2 12 8-6 1-3 6z M10 11l4 6"/>',
    "qubit": '<circle cx="10" cy="10" r="5.5"/><path d="M10 2v2.5 M10 15.5V18 M2 10h2.5 M15.5 10H18 M8 10h4 M10 8v4"/>',
    "coupler": '<circle cx="4.5" cy="10" r="2"/><circle cx="15.5" cy="10" r="2"/><path d="M6.5 10H8l1-3 2 6 1-3h1.5"/>',
    "connect": '<path d="m7.5 12.5 5-5 M6 11l-2 2a3.5 3.5 0 0 0 5 5l3-3a3.5 3.5 0 0 0 0-5 M14 9l2-2a3.5 3.5 0 0 0-5-5L8 5a3.5 3.5 0 0 0 0 5"/>',
    "fit": '<path d="M7 3H3v4 M13 3h4v4 M3 13v4h4 M17 13v4h-4"/><rect x="7" y="7" width="6" height="6" rx=".5"/>',
    "chevron": '<path d="m7 4 6 6-6 6"/>',
    "delete": '<path d="M3 5h14 M7 5V2.5h6V5 M5 5l1 12.5h8L15 5 M8 8v6.5 M12 8v6.5"/>',
    "moon": '<path d="M17 12a7.5 7.5 0 0 1-9-9 7.5 7.5 0 1 0 9 9z"/>',
    "sun": '<circle cx="10" cy="10" r="3.5"/><path d="M10 1.5V3 M10 17v1.5 M1.5 10H3 M17 10h1.5 M4 4l1 1 M15 15l1 1 M4 16l1-1 M15 5l1-1"/>',
    "more": '<circle cx="4" cy="10" r=".75"/><circle cx="10" cy="10" r=".75"/><circle cx="16" cy="10" r=".75"/>',
    "close": '<path d="m5 5 10 10 M15 5 5 15"/>',
    "save": '<path d="M3 2.5h12l2 2v13H3z M6 2.5V7h7V2.5 M6 17.5V11h8v6.5"/>',
    "undo": '<path d="M3 8h8a5 5 0 0 1 0 10 M7 4 3 8l4 4"/>',
    "help": '<circle cx="10" cy="10" r="7.5"/><path d="M7.5 7.5a2.5 2.5 0 1 1 4 2c-1 .6-1.5 1-1.5 2 M10 14.5v.1"/>',
}

# Qt 6.8 corrected scaledPixmap to pass a logical size to custom engines.
_LOGICAL_SCALED_SIZE = tuple(int(part) for part in qVersion().split(".")[:2]) >= (6, 8)


class _SvgIconEngine(QIconEngine):
    """Retain SVG source so Qt can request crisp pixmaps for any DPI."""

    def __init__(self, svg: bytes) -> None:
        super().__init__()
        self._svg = svg
        self._renderer = QSvgRenderer(QByteArray(svg))

    def clone(self) -> QIconEngine:
        return _SvgIconEngine(self._svg)

    def key(self) -> str:
        return "QubitSequencerSvgIcon"

    def paint(self, painter: QPainter, rect, mode: QIcon.Mode, state: QIcon.State) -> None:
        painter.save()
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(painter.opacity() * 0.42)
        side = min(rect.width(), rect.height())
        target = QRectF(rect.x() + (rect.width() - side) / 2,
                        rect.y() + (rect.height() - side) / 2, side, side)
        self._renderer.render(painter, target)
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        factor = scale if _LOGICAL_SCALED_SIZE else 1.0
        physical = QSize(max(1, round(size.width() * factor)), max(1, round(size.height() * factor)))
        pixmap = QPixmap(physical)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        return pixmap


@lru_cache(maxsize=256)
def _cached_icon(name: str, color: str, opacity: float) -> QIcon:
    shape = _SHAPES[name]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" '
        f'fill="none" stroke="{color}" stroke-opacity="{opacity:.4f}" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'{shape}</svg>'
    )
    return QIcon(_SvgIconEngine(svg.encode("utf-8")))


def icon(name: str, color: str) -> QIcon:
    """Return an independent QIcon sharing a cached scalable SVG engine.

    Names are the small action vocabulary in _SHAPES. A QColor-compatible
    color is required; unknown names or invalid colors raise ValueError.
    """
    if name not in _SHAPES:
        raise ValueError(f"Unknown UI icon: {name!r}")
    parsed = QColor(color)
    if not parsed.isValid():
        raise ValueError(f"Invalid UI icon color: {color!r}")
    return QIcon(_cached_icon(name, parsed.name(), parsed.alphaF()))

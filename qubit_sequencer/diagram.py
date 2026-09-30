"""Scientific chip maps shared by the interactive editor and PNG/SVG exports."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, QVariantAnimation
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QImage, QPainter,
                           QPen, QPolygonF, QTransform)
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsLineItem,
                               QGraphicsPolygonItem, QGraphicsScene, QGraphicsTextItem)

from .storage import ChipStore
from .tex_labels import tex_to_html
from .theme import DIAGRAM_COLORS

CELL = 92
PAD = 90
INK = "#172A3D"
MUTED = "#6C7D8E"
PAPER = "#F7FAFC"
DATA = "#E5AD48"
X_CHECK = "#3980AF"
Z_CHECK = "#70B4CE"
COUPLER = "#39A88E"
DIRECT = "#577A9A"
MISSING = "#C7D1DA"
PALETTE = ["#397EAE", "#D99538", "#39A88E", "#9866A9", "#C15F73", "#8C993F"]
_FONT_FAMILY: str | None = None


def cjk_font_family() -> str:
    global _FONT_FAMILY
    if _FONT_FAMILY is None:
        for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/NotoSansSC-VF.ttf",
                     "C:/Windows/Fonts/simhei.ttf"):
            if Path(path).is_file():
                ident = QFontDatabase.addApplicationFont(path)
                if ident >= 0:
                    families = QFontDatabase.applicationFontFamilies(ident)
                    if families:
                        _FONT_FAMILY = ("Microsoft YaHei UI" if "Microsoft YaHei UI" in families
                                        else families[0])
                        break
        if _FONT_FAMILY is None:
            _FONT_FAMILY = "Microsoft YaHei"
    return _FONT_FAMILY


class ChipScene(QGraphicsScene):
    clicked = Signal(str, str)
    blank_clicked = Signal(float, float)

    def __init__(self, store: ChipStore, field: str = "", point: str = "default",
                 theme: str = "light"):
        super().__init__()
        self.store = store
        self.field = field
        self.point = point
        self.theme = theme if theme in DIAGRAM_COLORS else "light"
        self.colors = DIAGRAM_COLORS[self.theme]
        self.content_rect = QRectF()
        self.last_click_screen_pos = None
        self._device_items = {}
        self._animations = {}
        self._build()

    def logical_at(self, pos) -> tuple[float, float]:
        return ((pos.x() - PAD) / CELL, (pos.y() - PAD) / CELL)

    def grow_to_include(self, rect: QRectF) -> None:
        current = self.sceneRect()
        margin = 12 * CELL
        if (rect.left() < current.left() + CELL * 2 or
                rect.right() > current.right() - CELL * 2 or
                rect.top() < current.top() + CELL * 2 or
                rect.bottom() > current.bottom() - CELL * 2):
            self.setSceneRect(current.united(rect.adjusted(-margin, -margin, margin, margin)))

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(self.colors["paper"]))
        if self.store.meta().get("template") not in ("custom", "square"):
            return
        left = math.floor((rect.left() - PAD) / CELL)
        right = math.ceil((rect.right() - PAD) / CELL)
        top = math.floor((rect.top() - PAD) / CELL)
        bottom = math.ceil((rect.bottom() - PAD) / CELL)
        count = (right - left + 1) * (bottom - top + 1)
        stride = max(1, math.ceil(math.sqrt(count / 6000)))
        first_x = math.ceil(left / stride) * stride
        first_y = math.ceil(top / stride) * stride
        painter.setPen(Qt.PenStyle.NoPen)
        for row in range(first_y, bottom + 1, stride):
            for col in range(first_x, right + 1, stride):
                size = 3.1 if col % 5 == 0 and row % 5 == 0 else 1.9
                painter.setBrush(QColor(self.colors["dot_major" if size > 2 else "dot_minor"]))
                painter.drawEllipse(QPointF(PAD + col * CELL, PAD + row * CELL), size, size)

    def mousePressEvent(self, event):
        self._press_pos = event.screenPos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not hasattr(self, "_press_pos") or (event.screenPos() - self._press_pos).manhattanLength() > 4:
            super().mouseReleaseEvent(event)
            return
        item = self.itemAt(event.scenePos(), self.views()[0].transform() if self.views() else QTransform())
        while item is not None and not item.data(0):
            item = item.parentItem()
        if item is not None:
            kind, ident = item.data(0)
            self.last_click_screen_pos = event.screenPos()
            if kind == "device":
                self.animate_device(ident)
            self.clicked.emit(kind, ident)
        else:
            self.blank_clicked.emit(*self.logical_at(event.scenePos()))
        super().mouseReleaseEvent(event)

    def animate_device(self, ident: str) -> None:
        glyph = self._device_items.get(ident)
        if glyph is None:
            return
        previous = self._animations.pop(ident, None)
        if previous:
            previous[0].stop()
            self.removeItem(previous[1])
        bounds = glyph.boundingRect()
        center = bounds.center()
        radius = max(bounds.width(), bounds.height()) * 0.68
        halo = QGraphicsEllipseItem(center.x()-radius, center.y()-radius, radius*2, radius*2)
        halo.setPen(QPen(QColor(self.colors["halo"]), 2.5))
        halo.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        halo.setZValue(1.5)
        halo.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        halo.setTransformOriginPoint(center)
        self.addItem(halo)
        glyph.setTransformOriginPoint(center)
        animation = QVariantAnimation(self)
        animation.setDuration(360)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)

        def update(value):
            t = float(value)
            glyph.setScale(1 + 0.14 * math.sin(math.pi * t))
            halo.setScale(1 + 0.55 * t)
            halo.setOpacity(1 - t)

        def finish():
            glyph.setScale(1)
            if halo.scene() is self:
                self.removeItem(halo)
            self._animations.pop(ident, None)

        animation.valueChanged.connect(update)
        animation.finished.connect(finish)
        self._animations[ident] = (animation, halo)
        animation.start()

    def _text(self, value: str, x: float, y: float, size: int = 9,
              color: str | None = None, bold: bool = False, target=None, tex: bool = False):
        color = color or self.colors["ink"]
        item = QGraphicsTextItem()
        font = QFont(cjk_font_family(), size)
        font.setBold(bold)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        item.setFont(font)
        item.setDefaultTextColor(QColor(color))
        if tex:
            item.setHtml(f'<span style="color:{color}">{tex_to_html(value)}</span>')
            item.setToolTip(value)
        else:
            item.setPlainText(value)
        item.document().setDocumentMargin(0)
        item.setPos(x, y)
        item.setZValue(4)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if target:
            item.setData(0, target)
        self.addItem(item)
        return item

    def _build(self) -> None:
        INK = self.colors["ink"]
        MUTED = self.colors["muted"]
        PAPER = self.colors["paper"]
        DATA = self.colors["data"]
        X_CHECK = self.colors["x_check"]
        Z_CHECK = self.colors["z_check"]
        COUPLER = self.colors["coupler"]
        DIRECT = self.colors["direct"]
        MISSING = self.colors["missing"]
        PALETTE = self.colors["categories"]
        devices = self.store.devices()
        couplings = self.store.couplings()
        links = self.store.links()
        meta = self.store.meta()
        by_id = {d["id"]: d for d in devices}
        xs = [float(d["x"] + d["dx"]) for d in devices]
        ys = [float(d["y"] + d["dy"]) for d in devices]
        if meta.get("template") in ("custom", "square"):
            xs += [0, float(meta.get("grid_columns", "5")) - 1]
            ys += [0, float(meta.get("grid_rows", "5")) - 1]
        if not xs:
            xs, ys = [0, 4], [0, 4]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        left, right = PAD + minx * CELL, PAD + maxx * CELL
        top, bottom = PAD + miny * CELL, PAD + maxy * CELL
        footer_space = 210 if self.field else 150
        self.content_rect = QRectF(left - 80, top - 60, max(450, right - left + 160),
                                   max(440 if self.field else 380, bottom - top + footer_space))
        self.setSceneRect(self.content_rect.adjusted(-8 * CELL, -8 * CELL, 8 * CELL, 8 * CELL))
        self.setBackgroundBrush(QBrush(QColor(PAPER)))

        def pos(device):
            return (PAD + (device["x"] + device["dx"]) * CELL,
                    PAD + (device["y"] + device["dy"]) * CELL)

        definition = None
        if self.field and self.field not in ("device.subtype", "device.role", "coupling.mode") and not self.field.startswith("wiring."):
            try:
                definition = self.store.definition(self.field)
            except KeyError:
                pass
        values = self.store.current_values(self.point)
        raw: dict[str, object] = {}
        if definition:
            if definition["scope"] in ("qubit", "coupler"):
                raw = {d["id"]: values.get((d["id"], self.field))
                       for d in devices if d["kind"] == definition["scope"]}
            elif definition["scope"] == "coupling":
                raw = {c["id"]: values.get((c["id"], self.field)) for c in couplings}
            else:
                raw = {link["id"]: values.get((link["id"], self.field)) for link in links}
        elif self.field == "device.subtype":
            raw = {d["id"]: d["subtype"] for d in devices}
        elif self.field == "device.role":
            raw = {d["id"]: d["role"] for d in devices}
        elif self.field == "coupling.mode":
            raw = {c["id"]: c["mode"] for c in couplings}
        elif self.field.startswith("wiring."):
            attr = self.field.split(".", 1)[1]
            for line in self.store.lines():
                for dev_id in self.store.line_devices(line["id"]):
                    old, val = raw.get(dev_id), line[attr]
                    raw[dev_id] = f"{old}, {val}" if old and val and val not in str(old).split(", ") else (old or val)

        numeric = [float(v) for v in raw.values() if isinstance(v, (int, float)) and math.isfinite(float(v))]
        lo, hi = (min(numeric), max(numeric)) if numeric else (0.0, 1.0)
        categories = sorted({str(v) for v in raw.values() if v is not None and not isinstance(v, (int, float))})
        category_color = {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(categories)}

        def color(value, default: str) -> QColor:
            if not self.field:
                return QColor(default)
            if value is None or value == "":
                return QColor(MISSING)
            if isinstance(value, (int, float)):
                t = 0.5 if hi == lo else (float(value) - lo) / (hi - lo)
                stops = (QColor(self.colors["heat_low"]), QColor(self.colors["heat_mid"]),
                         QColor(self.colors["heat_high"]))
                a, b, f = (stops[0], stops[1], t * 2) if t < 0.5 else (stops[1], stops[2], (t - 0.5) * 2)
                return QColor.fromRgbF(*(a.getRgbF()[i] * (1 - f) + b.getRgbF()[i] * f for i in range(3)))
            return QColor(category_color.get(str(value), MUTED))

        def fmt(value) -> str:
            return "—" if value is None or value == "" else f"{value:g}" if isinstance(value, (int, float)) else str(value)

        mediated = {c["coupler_id"]: c for c in couplings if c["coupler_id"]}
        for coupling in couplings:
            if coupling["mode"] != "direct":
                continue
            a, b = pos(by_id[coupling["qubit_a"]]), pos(by_id[coupling["qubit_b"]])
            relevant = (definition is not None and definition["scope"] == "coupling") or self.field == "coupling.mode"
            edge = color(raw.get(coupling["id"]), DIRECT) if relevant else QColor(DIRECT)
            line = QGraphicsLineItem(*a, *b)
            line.setPen(QPen(edge, 5 if relevant else 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            line.setData(0, ("coupling", coupling["id"]))
            line.setZValue(-1)
            self.addItem(line)
            if definition and definition["scope"] == "coupling" and raw.get(coupling["id"]) is not None:
                self._text(fmt(raw[coupling["id"]]), (a[0] + b[0]) / 2 + 5, (a[1] + b[1]) / 2 - 19, 9)

        for link in links:
            a, b = pos(by_id[link["qubit_id"]]), pos(by_id[link["coupler_id"]])
            pair = mediated.get(link["coupler_id"])
            if definition and definition["scope"] == "link":
                edge = color(raw.get(link["id"]), COUPLER)
            elif pair and ((definition and definition["scope"] == "coupling") or self.field == "coupling.mode"):
                edge = color(raw.get(pair["id"]), COUPLER)
            else:
                edge = QColor(COUPLER)
            line = QGraphicsLineItem(*a, *b)
            line.setPen(QPen(edge, 5 if definition and definition["scope"] == "link" else 3.5,
                             Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            line.setData(0, ("link", link["id"]))
            line.setZValue(-1)
            self.addItem(line)
            if definition and definition["scope"] == "link":
                self._text(fmt(raw.get(link["id"])), (a[0] + b[0]) / 2 + 3, (a[1] + b[1]) / 2 - 19,
                           9, INK)

        linked_qubits: dict[str, list] = {}
        for link in links:
            linked_qubits.setdefault(link["coupler_id"], []).append(by_id[link["qubit_id"]])
        for device in devices:
            x, y = pos(device)
            is_qubit = device["kind"] == "qubit"
            base = ({"data": DATA, "measure_x": X_CHECK, "measure_z": Z_CHECK}.get(
                device["role"], self.colors["qubit"])
                    if is_qubit else COUPLER)
            relevant = (definition is not None and definition["scope"] == device["kind"] or
                        self.field in ("device.subtype", "device.role") or self.field.startswith("wiring."))
            fill = color(raw.get(device["id"]), base) if relevant else QColor(base)
            if is_qubit:
                radius = 20
                item = QGraphicsEllipseItem(x-radius, y-radius, 2*radius, 2*radius)
            else:
                radius = 12
                item = QGraphicsPolygonItem(QPolygonF([QPointF(x, y-radius), QPointF(x+radius, y),
                                                       QPointF(x, y+radius), QPointF(x-radius, y)]))
            item.setPen(QPen(QColor(INK), 1.8))
            item.setBrush(QBrush(fill))
            item.setData(0, ("device", device["id"]))
            item.setZValue(2)
            self.addItem(item)
            self._device_items[device["id"]] = item
            if self.field and relevant:
                original = fmt(raw.get(device["id"]))
                displayed = original
                if self.field == "device.subtype":
                    displayed = "可调" if "tunable" in original else "固定"
                elif self.field == "device.role":
                    displayed = {"data": "D", "measure_x": "X", "measure_z": "Z"}.get(original, "—")
                value_color = INK if fill.lightnessF() > 0.64 else "#FFFFFF"
                label = self._text(displayed, 0, 0, 10 if is_qubit else 8, value_color, True,
                                   ("device", device["id"]))
                max_extent = (34 if is_qubit else 17)
                factor = min(1.0, max_extent / max(1, label.boundingRect().width()),
                             max_extent / max(1, label.boundingRect().height()))
                label.setScale(factor)
                label.setPos(x - label.boundingRect().width()*factor/2,
                             y - label.boundingRect().height()*factor/2)
                label.setToolTip(f"{device['label']} · {original}")
            elif not self.field and is_qubit:
                label_color = INK if fill.lightnessF() > 0.64 else "#FFFFFF"
                label = self._text(device["label"], 0, 0, 10, label_color, True,
                                   ("device", device["id"]), tex=True)
                factor = min(1.0, 37 / max(1, label.boundingRect().width()))
                label.setScale(factor)
                label.setPos(x - label.boundingRect().width()*factor/2,
                             y - label.boundingRect().height()*factor/2)
            elif not self.field:
                neighbors = linked_qubits.get(device["id"], [])
                horizontal = len(neighbors) == 2 and abs(neighbors[0]["y"] - neighbors[1]["y"]) < 0.01
                if horizontal:
                    label = self._text(device["label"], 0, 0, 8, INK, False,
                                       ("device", device["id"]), tex=True)
                    factor = min(1.0, 70 / max(1, label.boundingRect().width()))
                    label.setScale(factor)
                    label.setPos(x - label.boundingRect().width()*factor/2, y + 13)
                else:
                    self._text(device["label"], x + 13, y - 11, 8, INK, False,
                               ("device", device["id"]), tex=True)
        qubits = [d for d in devices if d["kind"] == "qubit"]
        roles = {d["role"] for d in qubits}
        legend = []
        if any(d["role"] not in ("data", "measure_x", "measure_z") for d in qubits):
            legend.append(("● 比特", "#648DAF"))
        if "data" in roles:
            legend.append(("● 数据比特", DATA))
        if "measure_x" in roles:
            legend.append(("● X 测量", X_CHECK))
        if "measure_z" in roles:
            legend.append(("● Z 测量", Z_CHECK))
        if any(d["kind"] == "coupler" for d in devices):
            legend.append(("◆ 耦合器", COUPLER))
        if any(c["mode"] == "direct" for c in couplings):
            legend.append(("━━ 直接电容", DIRECT))
        if links:
            legend.append(("━━ 比特—耦合器", COUPLER))
        legend_items = []
        cursor_x, row = self.content_rect.left()+20, 0
        for caption, swatch in legend:
            item = self._text(caption, 0, 0, 9, swatch, True)
            width = item.boundingRect().width()
            if cursor_x + width > self.content_rect.right()-20 and cursor_x > self.content_rect.left()+20:
                cursor_x, row = self.content_rect.left()+20, row+1
            legend_items.append((item, cursor_x, row))
            cursor_x += width + 15
        rows = row+1 if legend_items else 0
        legend_y = self.content_rect.bottom() - 16 - max(1, rows)*18
        for item, x, line_no in legend_items:
            item.setPos(x, legend_y + line_no*18)
        if self.field:
            field_label = definition["label"] if definition else {
                "device.subtype": "器件类型", "device.role": "表面码角色",
                "coupling.mode": "耦合方式", "wiring.line_type": "接线类型",
                "wiring.cryostat": "制冷机", "wiring.port": "制冷机端口",
                "wiring.channel": "线路通道",
            }.get(self.field, self.field)
            self._text(f"{field_label}  ·  {self.point}", self.content_rect.left()+20,
                       legend_y-56, 11, INK, True)
            scale_y = legend_y-31
            self._text("灰色 = 未填写", self.content_rect.left()+20, scale_y, 9, MUTED)
            if numeric:
                for i in range(30):
                    t = i / 29
                    self.addRect(self.content_rect.left()+135+i*5, scale_y+4, 5, 10,
                                 QPen(Qt.PenStyle.NoPen), QBrush(color(lo+(hi-lo)*t, MISSING)))
                unit = f" {definition['unit']}" if definition and definition["unit"] else ""
                self._text(f"{lo:g} → {hi:g}{unit}", self.content_rect.left()+295, scale_y, 9, MUTED)
            elif categories:
                self._text("  ·  ".join(categories[:4]), self.content_rect.left()+135, scale_y, 9, MUTED)


def export_diagram(store: ChipStore, path: str | Path, field: str = "",
                   point: str = "default", width: int = 1800,
                   theme: str = "light") -> None:
    """Export a tight crop of the vector scene; the editor canvas remains unbounded."""
    path = Path(path)
    if width < 400 or width > 10000:
        raise ValueError("image width must be 400..10000")
    scene = ChipScene(store, field, point, theme)
    source = scene.content_rect
    height = max(400, round(width * source.height() / source.width()))
    if path.suffix.lower() == ".png":
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(scene.colors["paper"]))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        scene.render(painter, QRectF(0, 0, width, height), source)
        painter.end()
        if not image.save(str(path), "PNG"):
            raise OSError(f"could not save {path}")
    elif path.suffix.lower() == ".svg":
        svg = QSvgGenerator()
        svg.setFileName(str(path))
        svg.setSize(QSize(width, height))
        svg.setViewBox(QRectF(0, 0, width, height).toRect())
        svg.setTitle(store.meta()["chip_name"])
        painter = QPainter(svg)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        scene.render(painter, QRectF(0, 0, width, height), source)
        painter.end()
    else:
        raise ValueError("only PNG and SVG are supported")

"""Scientific chip maps shared by the interactive editor and PNG/SVG exports."""

from __future__ import annotations

import hashlib
from html import escape
import math
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal, QVariantAnimation
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QImage, QPainter,
                           QPen, QPolygonF, QPainterPath, QPainterPathStroker, QTextOption, QTransform)
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsLineItem,
                               QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsPathItem,
                               QGraphicsScene, QGraphicsTextItem, QGraphicsItem)

from .storage import ChipStore
from .device_types import DEVICE_COLORS, DEVICE_NAMES
from .readout_store import readout_route, project_to_readout
from .tex_labels import tex_to_html
from .theme import DIAGRAM_COLORS, custom_device_colors

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


class _LabelObstacles:
    """Index occupied rectangles locally, including on large sparse chips."""

    def __init__(self):
        self.cells = {}
        self.rects = []

    @staticmethod
    def keys(rect):
        for row in range(math.floor(rect.top() / CELL), math.floor(rect.bottom() / CELL) + 1):
            for col in range(math.floor(rect.left() / CELL), math.floor(rect.right() / CELL) + 1):
                yield col, row

    def add(self, rect):
        index = len(self.rects)
        self.rects.append(QRectF(rect))
        for key in self.keys(rect):
            self.cells.setdefault(key, []).append(index)

    def overlap(self, rect):
        indices = {index for key in self.keys(rect) for index in self.cells.get(key, [])}
        return sum(intersection.width() * intersection.height()
                   for index in indices
                   if not (intersection := rect.intersected(self.rects[index])).isEmpty())

    def leader_crossings(self, start, end):
        box = QRectF(start, end).normalized()
        indices = {index for key in self.keys(box) for index in self.cells.get(key, [])}
        count = 0
        for index in indices:
            rect = self.rects[index]
            if rect.contains(start):
                continue
            lower, upper = 0.0, 1.0
            for origin, delta, minimum, maximum in (
                    (start.x(), end.x() - start.x(), rect.left(), rect.right()),
                    (start.y(), end.y() - start.y(), rect.top(), rect.bottom())):
                if abs(delta) < 1e-8:
                    if not minimum <= origin <= maximum:
                        upper = -1
                        break
                else:
                    first, last = sorted(((minimum - origin) / delta, (maximum - origin) / delta))
                    lower, upper = max(lower, first), min(upper, last)
            count += lower <= upper
        return count

    def place(self, width, height, candidates):
        best, best_score = None, math.inf
        for center, anchor in candidates:
            rect = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)
            end = QPointF(min(max(anchor.x(), rect.left()), rect.right()),
                          min(max(anchor.y(), rect.top()), rect.bottom()))
            score = (self.overlap(rect.adjusted(-3, -3, 3, 3)) * 1000
                     + self.leader_crossings(anchor, end))
            if score < best_score:
                best, best_score = (rect, anchor), score
            if score == 0:
                break
        self.add(best[0].adjusted(-3, -3, 3, 3))
        return best


def _qubit_role_color(role: str, colors: dict) -> str:
    key = {"data": "data", "measure_x": "x_check", "measure_z": "z_check",
           "X": "x_check", "Z": "z_check", "": "qubit"}.get(role)
    if key:
        return colors[key]
    # Keep custom identities the same color across edits and application runs.
    index = int.from_bytes(hashlib.sha256(role.encode("utf-8")).digest()[:4], "big")
    return colors["categories"][index % len(colors["categories"])]


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


class ReadoutBusItem(QGraphicsPathItem):
    def shape(self):
        stroke = QPainterPathStroker(); stroke.setWidth(12)
        return stroke.createStroke(self.path())

    def boundingRect(self):
        return self.path().boundingRect().adjusted(-6, -6, 6, 6)


class ChipScene(QGraphicsScene):
    clicked = Signal(str, str)
    blank_clicked = Signal(float, float)
    device_context_requested = Signal(str, QPoint)

    def __init__(self, store: ChipStore, field: str = "", point: str = "default",
                 theme: str = "light", *, readout_visible=True):
        super().__init__()
        self.store = store
        self.field = field
        self.point = point
        self.theme = theme if theme in DIAGRAM_COLORS else "light"
        self.colors = DIAGRAM_COLORS[self.theme]
        self.content_rect = QRectF()
        self.readout_visible = readout_visible
        self.deemphasize_couplers = readout_visible and bool(store.devices("readout"))
        self._coupler_ids = {row["id"] for row in store.devices("coupler")}
        self.last_click_scene_pos = None
        self.last_click_screen_pos = None
        self._device_items = {}
        self._animations = {}
        self.readout_start = None
        self.readout_end = None
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
        painter.save()
        painter.fillRect(rect, QColor(self.colors["paper"]))
        if self.store.meta().get("template") not in ("custom", "square"):
            painter.restore()
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
        painter.restore()

    def mousePressEvent(self, event):
        self._press_pos = event.screenPos()
        super().mousePressEvent(event)

    def set_readout_start(self, point):
        self.readout_start = point
        self.readout_end = point
        self.update()

    def mouseMoveEvent(self, event):
        if self.readout_start is not None:
            x, y = self.logical_at(event.scenePos())
            self.readout_end = (round(x*2)/2, round(y*2)/2)
            self.update()
        super().mouseMoveEvent(event)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if self.readout_start is None:
            return
        start = QPointF(PAD+self.readout_start[0]*CELL, PAD+self.readout_start[1]*CELL)
        end = QPointF(PAD+self.readout_end[0]*CELL, PAD+self.readout_end[1]*CELL)
        painter.save()
        painter.setPen(QPen(QColor(DEVICE_COLORS["readout"]), 2, Qt.PenStyle.DashLine))
        painter.drawLine(start, end)
        painter.setPen(QPen(QColor(DEVICE_COLORS["readout"]), 2))
        painter.setBrush(QColor(self.colors["paper"]))
        for point in (start, end):
            painter.drawEllipse(point, 5, 5)
        painter.restore()

    def _hit_item(self, position):
        transform = self.views()[0].viewportTransform() if self.views() else QTransform()
        for item in self.items(position, deviceTransform=transform):
            while item is not None:
                if item.data(0) or item.data(1) == "footer":
                    return item
                item = item.parentItem()
        return None

    def contextMenuEvent(self, event):
        item = self._hit_item(event.scenePos())
        target = item.data(0) if item is not None else None
        if target and target[0] == "device":
            event.accept()
            self.device_context_requested.emit(target[1], event.screenPos())
            return
        super().contextMenuEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() != Qt.MouseButton.LeftButton or not hasattr(self, "_press_pos")
                or (event.screenPos() - self._press_pos).manhattanLength() > 4):
            super().mouseReleaseEvent(event)
            return
        item = self._hit_item(event.scenePos())
        if item is not None and not item.data(0):
            super().mouseReleaseEvent(event)
            return
        if item is not None:
            kind, ident = item.data(0)
            self.last_click_screen_pos = event.screenPos()
            self.last_click_scene_pos = event.scenePos()
            if kind == "device" and self.store.device(ident)["kind"] != "readout":
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

    def _text(self, value: str, x: float, y: float, size: float = 9,
              color: str | None = None, bold: bool = False, target=None, tex: bool = False):
        color = color or self.colors["ink"]
        item = QGraphicsTextItem()
        font = QFont(cjk_font_family())
        font.setPointSizeF(size)
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
            if self.deemphasize_couplers and (target[0] == "link" or
                    target[0] == "device" and target[1] in self._coupler_ids):
                item.setOpacity(.4)
        self.addItem(item)
        return item

    def _parameter_text(self, value, target, tex=False):
        label = self._text(value, 0, 0, 11, self.colors["ink"], True, target, tex)
        if tex:
            label.setHtml(tex_to_html(value))
        option = label.document().defaultTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignCenter)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        label.document().setDefaultTextOption(option)
        return label

    def _callout(self, label, rect, anchor, radius=0):
        """Readable full text with a paper backing and a discreet leader."""
        label.setDefaultTextColor(QColor(self.colors["ink"]))
        label.setPos(rect.topLeft())
        background = QGraphicsRectItem(rect.adjusted(-3, -2, 3, 2))
        background.setBrush(QBrush(QColor(self.colors["paper"])))
        background.setPen(QPen(Qt.PenStyle.NoPen))
        background.setZValue(3)
        background.setData(0, label.data(0))
        background.setOpacity(label.opacity())
        background.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addItem(background)
        end = QPointF(min(max(anchor.x(), rect.left()), rect.right()),
                      min(max(anchor.y(), rect.top()), rect.bottom()))
        delta = end - anchor
        distance = math.hypot(delta.x(), delta.y())
        if distance > radius + 3:
            start = anchor + delta * (radius / distance)
            leader = QGraphicsLineItem(start.x(), start.y(), end.x(), end.y())
            leader.setPen(QPen(QColor(self.colors["muted"]), 0.7))
            leader.setOpacity(label.opacity())
            leader.setZValue(0)
            leader.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.addItem(leader)

    def _place_device_parameter(self, label, center, radius, is_qubit, obstacles, value_color):
        # Fit with actual font metrics rather than scaling arbitrarily long text
        # down to a few pixels. Circle/diamond geometry includes corner clearance.
        for size in (12, 11, 10, 9):
            font = label.font()
            font.setPointSizeF(size)
            label.setFont(font)
            label.setTextWidth(-1)
            bounds = label.boundingRect()
            half_w, half_h = bounds.width() / 2, bounds.height() / 2
            extent = math.hypot(half_w, half_h) if is_qubit else half_w + half_h
            if extent <= radius - 2:
                label.setTextWidth(bounds.width())
                label.setDefaultTextColor(QColor(value_color))
                label.setPos(center - QPointF(half_w, half_h))
                return
        font = label.font()
        font.setPointSizeF(9 if self.field.startswith("wiring.") and not is_qubit else 10)
        label.setFont(font)
        label.setTextWidth(-1)
        if label.boundingRect().width() > 140:
            label.setTextWidth(140)
        bounds = label.boundingRect()

        def candidates():
            # Prefer the immediate right/left or above/below the component.
            for gap in (7, 12, 18, 24, 32, 50, 76, 110, 155, 210):
                for dx, dy in ((1, 0), (-1, 0), (0, -1), (0, 1),
                               (1, -1), (-1, -1), (1, 1), (-1, 1),
                               (.5, -1), (-.5, -1), (.5, 1), (-.5, 1),
                               (1, -.5), (-1, -.5), (1, .5), (-1, .5)):
                    offset = QPointF(dx * (radius + gap + bounds.width() / 2),
                                     dy * (radius + gap + bounds.height() / 2))
                    yield center + offset, center
        rect, anchor = obstacles.place(bounds.width(), bounds.height(), candidates())
        self._callout(label, rect, anchor, radius)

    def _place_edge_parameter(self, value, a, b, target, obstacles):
        label = self._parameter_text(value, target)
        font = label.font()
        font.setPointSizeF(10)
        label.setFont(font)
        if label.boundingRect().width() > 140:
            label.setTextWidth(140)
        bounds = label.boundingRect()
        start, finish = QPointF(*a), QPointF(*b)
        delta = finish - start
        length = math.hypot(delta.x(), delta.y()) or 1
        normal = QPointF(-delta.y() / length, delta.x() / length)
        # Account for the label's width as well as its height on diagonal edges.
        clearance = (abs(normal.x()) * bounds.width() + abs(normal.y()) * bounds.height()) / 2 + 7

        def candidates():
            for extra in (0, 10, 24, 44, 72, 110, 160, 220):
                for fraction in (.5, .4, .6, .3, .7):
                    anchor = start + delta * fraction
                    for side in (-1, 1):
                        yield anchor + normal * ((clearance + extra) * side), anchor
        rect, anchor = obstacles.place(bounds.width(), bounds.height(), candidates())
        self._callout(label, rect, anchor)

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
        devices = [device for device in self.store.devices()
                   if self.readout_visible or device["kind"] not in ("readout", "filter")]
        custom_names = {}
        for device in devices:
            if device["kind"] == "custom":
                custom_names.setdefault(device["subtype"], []).append(device["label"])
        custom_colors = custom_device_colors(custom_names)
        couplings = self.store.couplings()
        links = self.store.links()
        meta = self.store.meta()
        by_id = {d["id"]: d for d in devices}
        readout_routes = {d["id"]: readout_route(d, devices) for d in devices if d["kind"] == "readout"}
        xs = [float(d["x"] + d["dx"]) for d in devices]
        ys = [float(d["y"] + d["dy"]) for d in devices]
        if not devices and meta.get("template") in ("custom", "square"):
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
            if definition["scope"] in DEVICE_NAMES:
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

        edge_parameters = []
        mediated = {c["coupler_id"]: c for c in couplings if c["coupler_id"]}
        for coupling in couplings:
            if coupling["mode"] != "direct":
                if definition and definition["scope"] == "coupling":
                    edge_parameters.append((fmt(raw.get(coupling["id"])),
                                            pos(by_id[coupling["qubit_a"]]),
                                            pos(by_id[coupling["qubit_b"]]),
                                            ("coupling", coupling["id"])))
                continue
            a, b = pos(by_id[coupling["qubit_a"]]), pos(by_id[coupling["qubit_b"]])
            relevant = (definition is not None and definition["scope"] == "coupling") or self.field == "coupling.mode"
            edge = color(raw.get(coupling["id"]), DIRECT) if relevant else QColor(DIRECT)
            line = QGraphicsLineItem(*a, *b)
            line.setPen(QPen(edge, 5 if relevant else 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            line.setData(0, ("coupling", coupling["id"]))
            line.setZValue(-1)
            self.addItem(line)
            if definition and definition["scope"] == "coupling":
                edge_parameters.append((fmt(raw.get(coupling["id"])), a, b,
                                        ("coupling", coupling["id"])))

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
            if self.deemphasize_couplers:
                line.setOpacity(.22)
            line.setZValue(-1)
            self.addItem(line)
            if definition and definition["scope"] == "link":
                edge_parameters.append((fmt(raw.get(link["id"])), a, b, ("link", link["id"])))

        if self.readout_visible:
            for link in self.store.readout_links():
                if link["position"] is not None:
                    continue
                readout, qubit = by_id[link["readout_id"]], by_id[link["device_id"]]
                _, point = project_to_readout(readout, qubit["x"]+qubit["dx"], qubit["y"]+qubit["dy"], route=readout_routes[readout["id"]])
                start, end = pos(qubit), (PAD+point[0]*CELL, PAD+point[1]*CELL)
                branch = QGraphicsLineItem(*start, *end)
                branch.setPen(QPen(QColor(DEVICE_COLORS["readout"]), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                branch.setData(0, ("readout_link", link["id"])); branch.setZValue(-.7)
                self.addItem(branch)

        linked_qubits: dict[str, list] = {}
        for link in links:
            linked_qubits.setdefault(link["coupler_id"], []).append(by_id[link["qubit_id"]])
        centers = {d["id"]: QPointF(*pos(d)) for d in devices}
        bins = {}
        for ident, center in centers.items():
            if by_id[ident]["kind"] == "readout":
                continue
            key = math.floor(center.x() / CELL), math.floor(center.y() / CELL)
            bins.setdefault(key, []).append(ident)
        obstacles = _LabelObstacles()
        desired_radii = {}
        for device in devices:
            ident = device["id"]
            is_qubit = device["kind"] == "qubit"
            relevant = (definition is not None and definition["scope"] == device["kind"] or
                        self.field in ("device.subtype", "device.role") or self.field.startswith("wiring."))
            radius = 20 if is_qubit else 12
            if self.field and relevant:
                value = raw.get(ident)
                textual = isinstance(value, str) and self.field != "device.subtype"
                multiline = textual and self.field.startswith("wiring.") and "," in value
                radius = (33 if multiline else 30 if textual else 24) if is_qubit else (18 if textual else 22)
            desired_radii[ident] = radius
        radii = {}
        for device in devices:
            ident = device["id"]
            center = centers[ident]
            radius = desired_radii[ident]
            if self.field and device["kind"] != "readout":
                col, row = math.floor(center.x() / CELL), math.floor(center.y() / CELL)
                # Divide limited space in proportion to each glyph's requested
                # size, so a multiline qubit can be bigger than its coupler.
                factor = min((max(0, math.hypot(center.x() - centers[other].x(), center.y() - centers[other].y()) - 6)
                              / (radius + desired_radii[other])
                              for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                              for other in bins.get((col + dx, row + dy), []) if other != ident),
                             default=1)
                radius = max(4, radius * min(1, factor))
            radii[ident] = radius
            if device["kind"] == "readout":
                continue
            obstacles.add(QRectF(center.x() - radius - 3, center.y() - radius - 3,
                                 2 * radius + 6, 2 * radius + 6))
        device_parameters = []
        draw_order = {"qubit": 0, "coupler": 1, "filter": 2, "custom": 3, "readout": 4}
        for device in sorted(devices, key=lambda d: draw_order[d["kind"]]):
            x, y = pos(device)
            is_qubit = device["kind"] == "qubit"
            is_custom = device["kind"] == "custom"
            base = (_qubit_role_color(device["role"], self.colors) if is_qubit else
                    COUPLER if device["kind"] == "coupler" else
                    custom_colors[device["subtype"]] if is_custom else DEVICE_COLORS[device["kind"]])
            relevant = (definition is not None and definition["scope"] == device["kind"] or
                        self.field in ("device.subtype", "device.role") or self.field.startswith("wiring."))
            fill = (color(raw.get(device["id"]), base)
                    if relevant and not (is_custom and self.field == "device.subtype") else QColor(base))
            radius = radii[device["id"]]
            if device["kind"] == "readout":
                path = QPainterPath()
                ax, ay = readout_routes[device["id"]](0)
                bx, by = readout_routes[device["id"]](1)
                path.moveTo(PAD+ax*CELL, PAD+ay*CELL)
                path.lineTo(PAD+bx*CELL, PAD+by*CELL)
                item = ReadoutBusItem(path)
            elif is_qubit:
                item = QGraphicsEllipseItem(x-radius, y-radius, 2*radius, 2*radius)
            elif device["kind"] == "coupler":
                item = QGraphicsPolygonItem(QPolygonF([QPointF(x, y-radius), QPointF(x+radius, y),
                                                       QPointF(x, y+radius), QPointF(x-radius, y)]))
            elif is_custom:
                vertices = ((-.5, -.85), (.5, -.85), (1, 0), (.5, .85), (-.5, .85), (-1, 0))
                item = QGraphicsPolygonItem(QPolygonF([QPointF(x+dx*radius, y+dy*radius) for dx, dy in vertices]))
            else:
                path = QPainterPath()
                path.addRoundedRect(QRectF(x-radius, y-radius, 2*radius, 2*radius), 3, 3)
                item = QGraphicsPathItem(path)
            if device["kind"] == "readout":
                item.setPen(QPen(fill, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            else:
                item.setPen(QPen(QColor(INK), 1.8))
                item.setBrush(QBrush(fill))
            item.setData(0, ("device", device["id"]))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            caption = device["subtype"] if is_custom else DEVICE_NAMES[device["kind"]]
            item.setToolTip(f"{caption} · {device['label']}")
            item.setZValue(.5 if device["kind"] == "readout" else 2)
            if device["kind"] == "coupler" and self.deemphasize_couplers:
                item.setOpacity(.28)
            self.addItem(item)
            self._device_items[device["id"]] = item
            if self.field and relevant:
                original = fmt(raw.get(device["id"]))
                displayed = original
                if self.field == "device.subtype" and not is_custom:
                    displayed = ("读取线" if original == "readout_line" else
                                 "可调" if "tunable" in original else "固定")
                elif self.field == "device.role":
                    displayed = {"data": "D", "measure_x": "X", "measure_z": "Z"}.get(original, original)
                elif self.field.startswith("wiring."):
                    parts = [part.strip() for part in original.split(",") if part.strip()]
                    if not is_qubit:
                        # Compact compound port names without dropping their
                        # separator or suffix (e.g. CFLUX- / 01).
                        parts = [part.rsplit("-", 1)[0] + "-\n" + part.rsplit("-", 1)[1]
                                 if len(part) > 7 and "-" in part else part for part in parts]
                    displayed = "\n".join(parts)
                value_color = INK if fill.lightnessF() > 0.64 else "#FFFFFF"
                label = self._parameter_text(displayed, ("device", device["id"]),
                                             tex=self.field == "device.role")
                if device["kind"] == "readout":
                    ax, ay = readout_routes[device["id"]](.5)
                    anchor = QPointF(PAD+ax*CELL, PAD+ay*CELL)
                    bounds = label.boundingRect()
                    candidates = [(anchor+QPointF(dx*(bounds.width()/2+12), dy*(bounds.height()/2+12)), anchor)
                                  for dx, dy in ((0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1))]
                    rect, _ = obstacles.place(bounds.width(), bounds.height(), candidates)
                    self._callout(label, rect, anchor, 3)
                else:
                    device_parameters.append((label, QPointF(x, y), radius, is_qubit, value_color))
                label.setToolTip(f"{device['label']} · {original}")
            elif not self.field and is_qubit:
                label_color = INK if fill.lightnessF() > 0.64 else "#FFFFFF"
                label = self._text(device["label"], 0, 0, 10, label_color, True,
                                   ("device", device["id"]), tex=True)
                factor = min(1.0, 37 / max(1, label.boundingRect().width()))
                label.setScale(factor)
                label.setPos(x - label.boundingRect().width()*factor/2,
                             y - label.boundingRect().height()*factor/2)
            elif not self.field and device["kind"] == "filter":
                anchor = QPointF(x, y)
                label = self._parameter_text(device["label"], ("device", device["id"]), tex=True)
                bounds = label.boundingRect()
                candidates = ((anchor + QPointF(dx*(radius+bounds.width()/2+8), dy*(radius+bounds.height()/2+8)), anchor)
                              for dx, dy in ((0,1),(0,-1),(1,0),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1)))
                rect, _ = obstacles.place(bounds.width(), bounds.height(), candidates)
                self._callout(label, rect, anchor, radius)
            elif not self.field and device["kind"] == "readout":
                end_x, end_y = readout_routes[device["id"]](1)
                anchor = QPointF(PAD+end_x*CELL, PAD+end_y*CELL)
                label = self._parameter_text(device["label"], ("device", device["id"]), tex=True)
                bounds = label.boundingRect()
                candidates = [(anchor+QPointF(dx*(bounds.width()/2+10), dy*(bounds.height()/2+12)), anchor)
                              for dx, dy in ((1,-1),(-1,-1),(1,1),(-1,1))]
                rect, _ = obstacles.place(bounds.width(), bounds.height(), candidates)
                self._callout(label, rect, anchor, 3)
            elif not self.field and not is_custom:
                neighbors = linked_qubits.get(device["id"], [])
                horizontal = len(neighbors) == 2 and abs(neighbors[0]["y"] - neighbors[1]["y"]) < 0.01
                if horizontal:
                    label = self._text(device["label"], 0, 0, 8, INK, False,
                                       ("device", device["id"]), tex=True)
                    factor = min(1.0, 70 / max(1, label.boundingRect().width()))
                    label.setScale(factor)
                    label.setPos(x - label.boundingRect().width()*factor/2, y + 13)
                else:
                    label_x = x + 13 if device["kind"] == "coupler" else x + radius + 5
                    label = self._text(device["label"], label_x, y - 11, 8, INK, False,
                                       ("device", device["id"]), tex=True)
                obstacles.add(label.sceneBoundingRect().adjusted(-3,-3,3,3))
        def crowding(center):
            col, row = math.floor(center.x() / CELL), math.floor(center.y() / CELL)
            return sum(math.hypot(center.x() - centers[other].x(), center.y() - centers[other].y()) < 1.5 * CELL
                       for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
                       for other in bins.get((col + dx, row + dy), []))

        # Reserve scarce interior whitespace first; boundary annotations have
        # more options to move outward without crossing the chip.
        device_parameters.sort(key=lambda entry: -crowding(entry[1]))
        edge_parameters.sort(key=lambda entry: -crowding(QPointF((entry[1][0] + entry[2][0]) / 2,
                                                                (entry[1][1] + entry[2][1]) / 2)))
        for label, center, radius, is_qubit, value_color in device_parameters:
            self._place_device_parameter(label, center, radius, is_qubit, obstacles, value_color)
        for value, a, b, target in edge_parameters:
            self._place_edge_parameter(value, a, b, target, obstacles)
        # Capture only persistent topology items, excluding click animations and
        # the infinite grid. Fitting and exports use the same body/footer layout.
        self.body_rect = self.itemsBoundingRect() if devices else QRectF(
            left - 20, top - 20, max(CELL, right - left + 40), max(CELL, bottom - top + 40))
        self.footer_item = QGraphicsRectItem()
        self.footer_item.setPen(QPen(Qt.PenStyle.NoPen))
        self.footer_item.setBrush(QBrush(QColor(PAPER)))
        self.footer_item.setZValue(5)
        self.footer_item.setData(1, "footer")
        self.footer_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addItem(self.footer_item)
        qubits = [d for d in devices if d["kind"] == "qubit"]
        roles = {d["role"] for d in qubits}
        legend = []
        role_labels = {"": "比特", "data": "数据比特", "measure_x": "X 测量",
                       "measure_z": "Z 测量"}
        ordered_roles = [role for role in role_labels if role in roles]
        ordered_roles += sorted(roles - role_labels.keys())
        for role in ordered_roles:
            legend.append((f"● {role_labels.get(role, role)}", _qubit_role_color(role, self.colors)))
        if any(d["kind"] == "coupler" for d in devices):
            legend.append(("◆ 耦合器", COUPLER))
        for kind, symbol in (("readout", "━"), ("filter", "■")):
            if any(d["kind"] == kind for d in devices):
                legend.append((f"{symbol} {DEVICE_NAMES[kind]}", DEVICE_COLORS[kind]))
        custom_legend = {}
        for subtype, swatch in custom_colors.items():
            names = custom_names[subtype]
            caption = f"⬢ {subtype} · {'、'.join(names)}"
            names_html = "、".join(map(tex_to_html, names))
            custom_legend[caption] = f'<span style="color:{swatch}">⬢</span> {escape(subtype)} · {names_html}'
            legend.append((caption, swatch))
        if any(c["mode"] == "direct" for c in couplings):
            legend.append(("━━ 直接电容", DIRECT))
        if links:
            legend.append(("━━ 比特—耦合器", COUPLER))
        self.legend_entries = legend
        self._legend_items = []
        for caption, swatch in legend:
            if caption in custom_legend:
                item = self._text(caption, 0, 0, 9, INK, True)
                item.setHtml(custom_legend[caption])
                max_width = max(410, self.body_rect.width())
                if item.boundingRect().width() > max_width:
                    item.setTextWidth(max_width)
            else:
                item = self._text(caption, 0, 0, 9, swatch, True, tex=True)
                if self.deemphasize_couplers and swatch == COUPLER:
                    item.setOpacity(.4)
            item.setParentItem(self.footer_item)
            self._legend_items.append(item)
        self._parameter_items = []
        if self.field:
            field_label = definition["label"] if definition else {
                "device.subtype": "器件类型", "device.role": "表面码角色",
                "coupling.mode": "耦合方式", "wiring.line_type": "接线类型",
                "wiring.cryostat": "制冷机", "wiring.port": "制冷机端口",
                "wiring.channel": "线路通道",
            }.get(self.field, self.field)
            self._parameter_items.append(self._text(f"{field_label}  ·  {self.point}",
                                                    0, 0, 11, INK, True, tex=True))
            scale_y = 30
            self._parameter_items.append(self._text("灰色 = 未填写", 0, scale_y, 9, MUTED))
            if numeric:
                for i in range(30):
                    t = i / 29
                    self._parameter_items.append(self.addRect(135+i*5, scale_y+4, 5, 10,
                                                               QPen(Qt.PenStyle.NoPen),
                                                               QBrush(color(lo+(hi-lo)*t, MISSING))))
                unit = f" {definition['unit']}" if definition and definition["unit"] else ""
                self._parameter_items.append(self._text(f"{lo:g} → {hi:g}{unit}", 295, scale_y, 9, MUTED))
            elif categories:
                category_labels = {
                    "device.subtype": {"fixed_transmon": "固定 Transmon", "tunable_transmon": "可调 Transmon",
                                       "fixed_coupler": "固定耦合器", "tunable_coupler": "可调耦合器",
                                       "readout_line": "读取线", "tunable_filter": "可调 filter"},
                    "coupling.mode": {"direct": "直接电容", "coupler": "经耦合器"},
                    "device.role": {"": "未指定", "data": "数据比特",
                                    "measure_x": "X 测量", "measure_z": "Z 测量"},
                }.get(self.field, {})
                captions = [category_labels.get(value, value) for value in categories[:4]]
                self._parameter_items.append(self._text("  ·  ".join(captions),
                                                        135, scale_y, 9, MUTED, tex=True))
            for item in self._parameter_items:
                item.setParentItem(self.footer_item)
        self.layout_footer()
        self.setSceneRect(self.content_rect.adjusted(-8 * CELL, -8 * CELL, 8 * CELL, 8 * CELL))

    def layout_footer(self) -> None:
        """Place annotations beneath the actual chip and include all wrapped rows."""
        width = max(450, self.body_rect.width() + 64,
                    max((item.boundingRect().width() + 40 for item in self._legend_items), default=0),
                    max((item.x() + item.boundingRect().right() + 40
                         for item in self._parameter_items), default=0))
        left = self.body_rect.center().x() - width / 2
        legend_y = max((item.y() + item.boundingRect().bottom()
                        for item in self._parameter_items), default=-16) + 16
        cursor_x, row_y, row_height = 0.0, legend_y, 0.0
        for item in self._legend_items:
            bounds = item.boundingRect()
            if cursor_x and cursor_x + bounds.width() > width - 40:
                cursor_x = 0
                row_y += row_height + 8
                row_height = 0
            item.setPos(cursor_x, row_y)
            cursor_x += bounds.width() + 18
            row_height = max(row_height, bounds.height())
        height = max(row_y + row_height, legend_y if self._parameter_items else 0)
        self.footer_item.setRect(QRectF(-12, -12, width - 16, height + 24))
        self.footer_item.setPos(left + 20, self.body_rect.bottom() + 32)
        top = self.body_rect.top() - 24
        bottom = self.footer_item.sceneBoundingRect().bottom() + 16
        self.content_rect = QRectF(left, top, width, bottom - top)


def export_diagram(store: ChipStore, path: str | Path, field: str = "",
                   point: str = "default", width: int = 1800,
                   theme: str = "light", *, readout_visible=True) -> None:
    """Export a tight crop of the vector scene; the editor canvas remains unbounded."""
    path = Path(path)
    if width < 400 or width > 10000:
        raise ValueError("image width must be 400..10000")
    scene = ChipScene(store, field, point, theme, readout_visible=readout_visible)
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

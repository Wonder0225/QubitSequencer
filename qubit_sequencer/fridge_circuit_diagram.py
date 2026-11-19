"""Temperature-aligned circuit diagrams for one or more physical ports."""

from collections import Counter
from typing import NamedTuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView

from .fridge_components import (BIAS_TEE_KIND, STAGES, SWITCH_DIRECTIONS, SWITCH_KIND, bias_tee_input_labels,
                               component_summary, component_text)
from .fridge_store import display_line_type
from .graphics_interaction import RightPanMixin
from .theme import DIAGRAM_COLORS, UI_COLORS


def ordered_stages(stages):
    known = [stage for stage in STAGES if stage in stages]
    custom = sorted(set(stages) - set(STAGES) - {""}, key=str.casefold)
    return known + custom + ([""] if "" in stages else [])


class CircuitLane(NamedTuple):
    route: str | None
    parent: str | None
    depth: int
    endpoint: tuple
    symbols: list
    merge_inputs: tuple = ()


def stage_slots(lane):
    stages = ordered_stages({component.get("stage") or "" for _, component in lane.symbols})
    # A switch can precede or follow components at another stage. Use a
    # separate column with the recorded stage when that would change the order.
    slots = []
    for key, component in lane.symbols:
        stage = component.get("stage") or ""
        edge = -1 if key == "merge" and stage != stages[0] else 1 if key == "switch" and stage != stages[-1] else 0
        slots.append(((stage, edge), key, component))
    return slots


class CircuitView(RightPanMixin, QGraphicsView):
    clicked = Signal(object)
    edit_requested = Signal(object)
    reorder_requested = Signal(object, object)

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.colors = DIAGRAM_COLORS[theme]
        self.ui = UI_COLORS[theme]
        self.setBackgroundBrush(QColor(self.colors["paper"]))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.visible_stages = ()
        self.reorderable = False
        self.reorder_groups = {}
        self.drag_source = None

    def fit(self):
        bounds = self.scene().sceneRect()
        scale = min(1.25, (self.viewport().width() - 8) / max(1, bounds.width()))
        self.resetTransform()
        self.scale(max(.35, scale), max(.35, scale))
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            factor = 1.15 if delta > 0 else 1 / 1.15
            if .22 <= self.transform().m11() * factor <= 4:
                self.scale(factor, factor)
        event.accept()

    def item_key(self, event):
        for item in self.items(event.position().toPoint()):
            if item.data(0) is not None:
                return item.data(0)
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            key = self.item_key(event)
            if key is not None:
                if key in self.reorder_groups:
                    self.drag_source = (key, event.position().toPoint())
                self.clicked.emit(key)
                event.accept()
                return
        super().mousePressEvent(event)

    def reorder_target(self, source, event):
        target = self.item_key(event)
        group = self.reorder_groups.get(source)
        if group is not None and source != target and group == self.reorder_groups.get(target):
            return target
        return None

    def mouseMoveEvent(self, event):
        if self.drag_source and event.buttons() & Qt.MouseButton.LeftButton:
            source, start = self.drag_source
            if (event.position().toPoint() - start).manhattanLength() >= QApplication.startDragDistance():
                target = self.reorder_target(source, event)
                cursor = Qt.CursorShape.ClosedHandCursor if target else Qt.CursorShape.ForbiddenCursor
                self.viewport().setCursor(cursor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_source:
            source, start = self.drag_source
            self.drag_source = None
            self.viewport().unsetCursor()
            if (event.position().toPoint() - start).manhattanLength() >= QApplication.startDragDistance():
                if target := self.reorder_target(source, event):
                    self.reorder_requested.emit(source, target)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_source = None
            key = self.item_key(event)
            if key is not None and key[2] != "toggle":
                self.edit_requested.emit(key)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def text(self, value, x, y, width, role="ink", size=15, bold=False):
        item = self.scene().addSimpleText("")
        font = item.font()
        font.setPixelSize(size)
        font.setBold(bold)
        item.setFont(font)
        value = str(value).replace("\n", " ")
        item.setText(QFontMetrics(font).elidedText(value, Qt.TextElideMode.ElideRight, int(width)))
        item.setBrush(QColor(self.colors[role]))
        item.setPos(x, y)
        return item

    @staticmethod
    def symbol_path(kind):
        shape = QPainterPath()
        if kind == SWITCH_KIND:
            points = ((-11, 0), (0, 0), (10, -8), None, (0, 0), (10, 8))
        elif kind == BIAS_TEE_KIND:
            points = ((-11, 0), (-6, 0), None, (-6, -4), (-6, 4), None, (-3, -4), (-3, 4),
                      None, (-3, 0), (11, 0), None, (4, 0), (4, -9))
        elif kind in ("放大器", "HEMT", "LNA", "TWPA", "amplifier"):
            points = ((-9, -8), (10, 0), (-9, 8), (-9, -8))
        elif kind in ("隔离器", "环行器"):
            shape.addEllipse(-8, -8, 16, 16)
            points = ((-4, 2), (0, -3), (5, 1))
        elif "滤波器" in kind or kind == "filter":
            shape.addRoundedRect(-10, -8, 20, 16, 3, 3)
            points = ((-6, 3), (-1, 3), (2, -3), (6, -3))
        elif kind == "电阻":
            points = ((-10, 0), (-7, -5), (-3, 5), (1, -5), (5, 5), (10, 0))
        elif kind in ("终端负载", "定向耦合器"):
            shape.addRect(-10, -7, 20, 14)
            points = ((-5, 3), (5, -3))
        else:
            shape.addRoundedRect(-10, -7, 20, 14, 2, 2)
            points = ()
        previous = None
        for point in points:
            if point is not None:
                if previous is None:
                    shape.moveTo(*point)
                else:
                    shape.lineTo(*point)
            previous = point
        return shape

    def symbol_pen(self, kind, selected=False):
        if selected:
            color = self.ui["accent"]
        elif kind == SWITCH_KIND:
            color = self.colors["coupler"]
        elif kind in ("放大器", "HEMT", "LNA", "TWPA", "amplifier"):
            color = self.colors["data"]
        else:
            color = self.colors["direct"]
        return QPen(QColor(color), 2)

    def legend_icon(self, kind):
        pixmap = QPixmap(26, 22)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(13, 11)
        painter.setPen(self.symbol_pen(kind))
        painter.drawPath(self.symbol_path(kind))
        painter.end()
        return pixmap

    def symbol(self, component, x, y, key, selected, inputs=()):
        scene = self.scene()
        kind = component.get("kind", "")
        # Opaque background keeps the wire from running through symbol labels and interiors.
        scene.addRect(x - 12, y - 11, 24, 22, QPen(Qt.PenStyle.NoPen), QBrush(QColor(self.colors["paper"])))
        shape = self.symbol_path(kind)
        if key[2] == "merge":
            shape = QTransform.fromScale(-1, 1).map(shape)
        glyph = scene.addPath(shape, self.symbol_pen(kind, selected))
        glyph.setPos(x, y)
        top = kind if kind != "标记" else component.get("mark", "") or kind
        if key[2] == "merge":
            top = "合路开关"
        detail = component_summary(component) if kind != "标记" else ""
        if kind == SWITCH_KIND:
            detail = " · ".join(filter(None, (component.get("stage") or "温级未登记", detail)))
            single_side = x + 16 if key[2] == "merge" else x - 26
            self.text("C", single_side, y - 17, 13, "muted", 12, bold=True)
        elif not detail and component.get("wire_number"):
            detail = f"电线 {component['wire_number']}"
        labels = ((top, y + 12), (detail, y + 31)) if inputs else ((top, y - 34), (detail, y + 14))
        for label, line_y in labels:
            text = self.text(label, x - 45, line_y, 90, size=14)
            text.moveBy((90 - text.boundingRect().width()) / 2, 0)
        hit = scene.addRect(x - 46, y - 34, 92, 84 if inputs else 69,
                            QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0, 0)))
        hit.setCursor(Qt.CursorShape.OpenHandCursor if key in self.reorder_groups else Qt.CursorShape.PointingHandCursor)
        hit.setData(0, key)
        hit.setToolTip("\n".join((component_text(component), *inputs)))
        if inputs:
            self.bias_tee_inputs(x, y, inputs, key)

    def bias_tee_inputs(self, x, y, inputs, key):
        scene = self.scene()
        rf_pen = QPen(QColor(self.colors["direct"]), 1.5)
        dc_pen = QPen(QColor(self.colors["coupler"]), 1.5)
        scene.addLine(x - 45, y - 34, x - 45, y, rf_pen)
        scene.addEllipse(x - 47, y - 2, 4, 4, QPen(Qt.PenStyle.NoPen), QBrush(rf_pen.color()))
        scene.addLine(x + 4, y - 8, x + 4, y - 21, dc_pen)
        scene.addLine(x + 4, y - 21, x + 45, y - 21, dc_pen)
        scene.addLine(x + 45, y - 21, x + 45, y - 34, dc_pen)
        for offset, label in zip((-45, 45), inputs):
            item = self.text(label, x + offset - 43, y - 56, 86, size=14, bold=True)
            item.moveBy((86 - item.boundingRect().width()) / 2, 0)
            item.setToolTip(label)
            item.setData(0, key)
            item.setCursor(Qt.CursorShape.PointingHandCursor)

    def signal_arrow(self, x, y, direction, pen):
        step = 1 if direction == "fan_out" else -1
        path = QPainterPath()
        path.moveTo(x - 5 * step, y - 4)
        path.lineTo(x, y)
        path.lineTo(x - 5 * step, y + 4)
        self.scene().addPath(path, pen)

    def render(self, routes, expanded, selected=None, *, branch_options=(), chosen_branch=None, input_ports=None):
        scene = self.scene()
        scene.clear()
        self.reorder_groups.clear()
        counts = {}
        bias_inputs = {}
        linked_slots = set()
        linked_lanes = set()
        input_ports = input_ports or {}
        for port, lanes in routes:
            for lane in lanes:
                slots = stage_slots(lane)
                for slot, count in Counter(slot for slot, _, _ in slots).items():
                    depth_counts = counts.setdefault(lane.depth, {})
                    depth_counts[slot] = max(depth_counts.get(slot, 0), count)
                for slot, key, component in slots:
                    if inputs := bias_tee_input_labels(component, input_ports):
                        bias_inputs[port["id"], lane.route, key] = inputs
                        linked_slots.add((lane.depth, slot))
                        linked_lanes.add((port["id"], lane.route))
        if not counts:
            counts[0] = {("", 0): 1}
        self.visible_stages = tuple(ordered_stages({stage for stages in counts.values() for stage, _ in stages}))
        layout = {}
        columns = []
        x = 180
        for depth, slots in sorted(counts.items()):
            stages = ordered_stages({stage for stage, _ in slots})
            layout[depth] = []
            for edge in (-1, 0, 1):
                for stage in stages:
                    slot = stage, edge
                    if slot not in slots:
                        continue
                    name = stage or "温级未登记"
                    if depth:
                        name = f"{'分支' if depth == 1 else f'第{depth}级'} · {stage or '未登记'}"
                    if edge < 0:
                        name += " · 前段"
                    elif edge > 0:
                        name += " · 后段"
                    minimum = 168 if edge or depth > 1 else 136 if depth else 118
                    step = 200 if (depth, slot) in linked_slots else 94
                    width = max(minimum, 24 + step * slots[slot])
                    columns.append((name, x, width))
                    layout[depth].append((slot, x, width, step))
                    x += width
            x += 24
        endpoint_x = x + 15
        right = endpoint_x + 270
        height = 48
        groups = []
        for port, lanes in routes:
            visible = lanes if port["id"] in expanded else lanes[:1]
            row_top, row_middle = {}, {}
            for lane in visible:
                linked = (port["id"], lane.route) in linked_lanes
                row_top[lane.route] = height
                row_middle[lane.route] = height + (64 if linked else 41)
                endpoint_height = 35 + (len(lane.endpoint) - 1) * 22
                if lane.route in branch_options:
                    endpoint_height += 25
                height += max(120 if linked else 82, endpoint_height)
            groups.append((port, lanes, visible, row_top, row_middle))
        rule = QPen(QColor(self.ui["border"]))
        no_pen = QPen(Qt.PenStyle.NoPen)
        for index, (label, left, width) in enumerate(columns):
            if index % 2 == 0:
                scene.addRect(left, 0, width, height, no_pen, QBrush(QColor(self.colors["dot_minor"])))
            scene.addLine(left, 0, left, height, rule)
            self.text(label, left + 12, 11, width - 20, size=18, bold=True).setToolTip(label)
        self.text("制冷机端口", 14, 11, 156, size=18, bold=True)
        self.text("样品与接线目标", endpoint_x, 11, 235, size=18, bold=True)
        wire = QPen(QColor(self.colors["muted"]), 1.4)
        unknown_wire = QPen(QColor(self.colors["muted"]), 1, Qt.PenStyle.DotLine)
        branch_wire = QPen(QColor(self.colors["coupler"]), 1.4)
        for port, lanes, visible, row_top, row_middle in groups:
            port_id = port["id"]
            open_group = port_id in expanded
            junctions = {}
            directions = {}
            positioned = {lane.route: self.position_symbols(lane, layout) for lane in visible}
            numbered_ports = {}
            for lane in lanes:
                if lane.route is not None and not lane.merge_inputs:
                    numbered_ports.setdefault(lane.parent, []).append(lane.route)
            merges = {}
            for lane in visible:
                if lane.merge_inputs:
                    sx = next(x for key, _, x in positioned[lane.route] if key == "merge")
                    merges.update({route: sx - 48 for route in lane.merge_inputs})
            scene.addLine(0, row_top[None], right, row_top[None], rule)
            for lane in visible:
                branch = lane.route
                top = row_top[branch]
                middle = row_middle[branch]
                available = layout.get(lane.depth, [])
                slots = stage_slots(lane)
                positions = positioned[branch]
                join_x = next((x for key, _, x in positions if key == "merge"), None)
                if join_x is not None:
                    start = join_x + 11
                else:
                    start = 171 if branch is None else junctions[lane.parent]
                switch = next(((component, x) for key, component, x in positions if key == "switch"), None)
                end = switch[1] - 11 if switch else merges.get(branch, endpoint_x - 6)
                if join_x is not None or branch in merges:
                    direction = "fan_out"  # On screen the merge inputs are to the left of the output.
                else:
                    direction = switch[0].get("direction") if switch else directions.get(lane.parent)
                scene.addLine(start, middle, end, middle, wire if lane.symbols else unknown_wire)
                if direction in SWITCH_DIRECTIONS:
                    arrow_x = start + 23 if switch else end - 6
                    self.signal_arrow(arrow_x, middle, direction, branch_wire)
                for slot, left, width, _ in available:
                    if left < end and left + width > start and not any(loc == slot for loc, _, _ in slots):
                        self.text("未登记", left + 12, middle + 9, width - 24, "muted", 13)
                for key, component, position in positions:
                    item_key = (port_id, branch, key)
                    if self.reorderable and isinstance(key, int):
                        self.reorder_groups[item_key] = (port_id, branch, component.get("stage") or "")
                    self.symbol(component, position, middle, item_key, item_key == selected,
                                bias_inputs.get(item_key, ()))

                if join_x is not None:
                    join_col = join_x - 48
                    first_input = min(row_middle[route] for route in lane.merge_inputs)
                    scene.addLine(join_col, first_input, join_col, middle - 8, branch_wire)
                    scene.addLine(join_col, middle - 8, join_x - 10, middle - 8, branch_wire)
                    for route in lane.merge_inputs:
                        input_y = row_middle[route]
                        scene.addEllipse(join_col - 2, input_y - 2, 4, 4, no_pen, QBrush(branch_wire.color()))
                if switch:
                    sx = switch[1]
                    junctions[branch] = junction_x = sx + 48
                    directions[branch] = direction
                    children = [row_middle[child.route] for child in visible if child.route is not None and not child.merge_inputs
                                and child.parent == branch]
                    if children:
                        last = max(children)
                        scene.addLine(sx + 10, middle + 8, junction_x, middle + 8, branch_wire)
                        scene.addLine(junction_x, middle + 8, junction_x, last, branch_wire)
                if branch is None:
                    usage = (port["usage"] if port["direct_sample_id"] else None) or port["kind"]
                    self.text(f"{port['board']}{port['number']} · {display_line_type(usage)}",
                              14, top + 9, 154, size=17, bold=True)
                    if len(lanes) > 1:
                        action = "收起" if open_group else "展开"
                        toggle = self.text(f"{action} {len(lanes) - 1} 段分支", 14, top + 34, 154, size=15)
                        toggle.setData(0, (port_id, None, "toggle"))
                        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    indent = min(lane.depth, 3) * 10
                    label = f"输出 {branch}" if lane.merge_inputs else f"分支 {branch}"
                    if not lane.merge_inputs:
                        position = str(numbered_ports[lane.parent].index(branch) + 1)
                        if branch != position:
                            label += f" · 端口 {position}"
                    item = self.text(label, 18 + indent, top + 20, 142 - indent, size=16, bold=True)
                    item.setToolTip(label)
                endpoint_top = top + 10
                if branch in branch_options:
                    label = f"公共输出 {branch}" if lane.merge_inputs else f"末端分支 {branch}"
                    self.endpoint_choice(port_id, branch, label, endpoint_x, endpoint_top, branch == chosen_branch)
                    endpoint_top += 25
                for index, line in enumerate(lane.endpoint):
                    item = self.text(line, endpoint_x, endpoint_top + index * 22, 260,
                                     "ink" if index == 0 else "muted")
                    item.setToolTip(line)
        scene.addLine(0, height, right, height, rule)
        scene.setSceneRect(-8, -8, right + 16, height + 20)

    def endpoint_choice(self, port_id, branch, label, x, y, selected):
        text = self.text(label, x + 7, y, 246, size=15, bold=True)
        shape = QPainterPath()
        shape.addRoundedRect(x, y - 3, text.boundingRect().width() + 14, 24, 5, 5)
        fill = self.ui["selected"] if selected else self.colors["paper"]
        panel = self.scene().addPath(shape, QPen(QColor(self.ui["accent"])), QBrush(QColor(fill)))
        text.setZValue(panel.zValue() + 1)
        for item in (panel, text):
            item.setData(0, (port_id, branch, "endpoint"))
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            item.setToolTip(label)

    @staticmethod
    def position_symbols(lane, layout):
        positions = []
        slots = stage_slots(lane)
        for slot, left, width, step in layout.get(lane.depth, []):
            entries = [(key, component) for loc, key, component in slots if loc == slot]
            first = left + (width - step * (len(entries) - 1)) / 2
            positions.extend((key, component, first + i * step) for i, (key, component) in enumerate(entries))
        return positions

"""Read-only calibrated transition spectra and native Qt scientific figures."""

from dataclasses import dataclass
import math
from pathlib import Path
import re

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QTextOption
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QSlider, QSpinBox, QWidget,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout,
)

from .diagram import cjk_font_family
from .parameter_rules import BIASED_FREQUENCY, VERTEX_FREQUENCY, bias_context, format_bias_z
from .storage import display_timestamp
from .tex_labels import tex_to_html
from .theme import DIAGRAM_COLORS, UI_COLORS


FREQUENCY_SOURCES = {BIASED_FREQUENCY: "偏置频率", VERTEX_FREQUENCY: "顶点频率", "qubit.f01": "通用比特频率"}


@dataclass(frozen=True)
class QubitLevels:
    ident: str
    label: str
    f10: float | None
    alpha: float | None
    frequency_record: dict | None = None
    alpha_record: dict | None = None
    frequency_key: str = "qubit.f01"

    @property
    def f21(self):
        return self.f10 + self.alpha / 1000 if self.f10 is not None and self.alpha is not None else None


def qubit_levels(store, point="default", frequency_key="qubit.f01"):
    """Use current measured fields at one workpoint; never substitute design data."""
    builtin_frequencies = {d["key"] for d in store.definitions("qubit")
                           if d["key"] in FREQUENCY_SOURCES and not d["is_custom"]}
    records = {(row["device_id"], row["definition_key"]): dict(row)
               for row in store.records(current_only=True, point=point)
               if row["definition_key"] in builtin_frequencies or row["definition_key"] == "qubit.anharmonicity"}
    if frequency_key == "auto":
        available = {key for (_, key), row in records.items() if row["numeric_value"] is not None}
        frequency_key = next((key for key in FREQUENCY_SOURCES if key in available), "qubit.f01")
    devices = sorted(store.devices("qubit"), key=lambda row: [
        int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", row["label"])])
    result = []
    for device in devices:
        frequency = records.get((device["id"], frequency_key))
        alpha = records.get((device["id"], "qubit.anharmonicity"))
        if frequency_key != "qubit.f01" and frequency and alpha and bias_context(frequency) != bias_context(alpha):
            alpha = None
        result.append(QubitLevels(device["id"], device["label"],
                                  frequency["numeric_value"] if frequency else None,
                                  alpha["numeric_value"] if alpha else None, frequency, alpha, frequency_key))
    return result


def frequency_axis(values):
    if not values:
        return 0, 1, []
    low, high = min(values), max(values)
    margin = max((high-low)*.1, .025)
    low, high = low-margin, high+margin
    target = (high-low)/6
    scale = 10 ** math.floor(math.log10(target))
    step = next(value for value in (1, 2, 2.5, 5, 10) if value*scale >= target)*scale
    first, last = math.floor(low/step), math.ceil(high/step)
    return first*step, last*step, [index*step for index in range(first, last+1)]


class EnergyScene(QGraphicsScene):
    def __init__(self, rows, chip_name, point, show_f21=False, style="columns", theme="light",
                 spacing=72, show_values=True):
        super().__init__()
        self.rows = rows
        self.show_f21 = show_f21
        self.style = style
        self.spacing = spacing
        self.show_values = show_values
        self.colors = UI_COLORS[theme]
        self.qubit_colors = DIAGRAM_COLORS[theme]["categories"]
        self.level_items = {}
        self.legend_items = {}
        self.setBackgroundBrush(QColor(self.colors["panel"]))
        values = [row.f10 for row in rows if row.f10 is not None]
        if show_f21:
            values += [row.f21 for row in rows if row.f21 is not None]
        self.low, self.high, self.ticks = frequency_axis(values)
        calibrated = sum(row.f10 is not None for row in rows)
        self.figure_width = max(760, 92+len(rows)*spacing+30) if style == "columns" else 1080
        self._text("比特能级图 · 跃迁频率", 28, 20, size=21, bold=True)
        source = FREQUENCY_SOURCES[rows[0].frequency_key] if rows else "比特频率"
        self._text(f"{chip_name} · 工作点 {point} · {source} · 已标定 f10 {calibrated} · 未标定 {len(rows)-calibrated}",
                   28, 60, color=self.colors["muted"], width=self.figure_width-56)
        self._line(28, 111, 64, 111, self.colors["text"], 3)
        self._text("f_{10} · 标定值", 72, 100, tex=True)
        if show_f21:
            self._line(245, 111, 281, 111, self.colors["text"], 2, Qt.PenStyle.DashLine)
            self._text("f_{21} · 由非谐计算", 289, 100, tex=True)
        legend_top = self._columns() if style == "columns" else self._horizontal()
        if not values:
            message = "当前工作点暂无已标定的 f10" if rows else "此项目尚未添加比特"
            self._text(message, 100, 250, color=self.colors["muted"])
        self._legend(legend_top)
        self.content_rect = self.itemsBoundingRect().adjusted(-24, -18, 24, 24)
        self.setSceneRect(self.content_rect)

    def _text(self, text, x, y, *, color=None, size=13, bold=False, tex=False, width=None, centered=False):
        item = self.addText("")
        font = QFont(cjk_font_family()); font.setPixelSize(size); font.setBold(bold)
        item.setFont(font); item.setDefaultTextColor(QColor(color or self.colors["text"]))
        item.document().setDocumentMargin(0)
        item.setHtml(tex_to_html(text)) if tex else item.setPlainText(text)
        option = item.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        item.document().setDefaultTextOption(option)
        if width is not None:
            item.setTextWidth(width)
        if centered:
            option = item.document().defaultTextOption()
            option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.document().setDefaultTextOption(option)
        item.setPos(x, y)
        return item

    def _line(self, x1, y1, x2, y2, color, width=1, style=Qt.PenStyle.SolidLine):
        pen = QPen(QColor(color), width, style)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        return self.addLine(x1, y1, x2, y2, pen)

    def _color(self, index):
        return self.qubit_colors[index % len(self.qubit_colors)]

    def _tooltip(self, row):
        parts = [row.label, "f10 未标定" if row.f10 is None else f"f10 = {row.f10:.10g} GHz"]
        bias = format_bias_z(row.frequency_record) if row.frequency_key == BIASED_FREQUENCY else None
        if bias is not None:
            parts.append(f"BiasZ：{bias}")
        if row.alpha is not None:
            parts.append(f"α = {row.alpha:.10g} MHz（保留原始符号）")
        if row.f21 is not None:
            parts.append(f"f21 = {row.f21:.10g} GHz（计算值）")
        elif row.f10 is not None:
            parts.append("同条件非谐未登记，无法计算 f21")
        for name, record in (("频率", row.frequency_record), ("非谐", row.alpha_record)):
            if record:
                parts.append(f"{name}：{display_timestamp(record['observed_at'])} · {record['source'] or '未填写来源'}")
        return "\n".join(parts)

    def _level(self, row, transition, x1, y1, x2, y2, color):
        style = Qt.PenStyle.SolidLine if transition == "f10" else Qt.PenStyle.DashLine
        item = self._line(x1, y1, x2, y2, color, 3.4 if transition == "f10" else 2, style)
        item.setData(0, (row.ident, transition)); item.setToolTip(self._tooltip(row))
        item.setZValue(2 if transition == "f10" else 1)
        self.level_items[(row.ident, transition)] = item

    def _columns(self):
        left, top, height = 92, 155, 380
        width = self.figure_width-left-30
        bottom = top+height
        project = lambda value: bottom-(value-self.low)/(self.high-self.low)*height
        self._text("跃迁频率 / GHz", left, 131, color=self.colors["muted"], size=12)
        for tick in self.ticks:
            y = project(tick)
            self._line(left, y, left+width, y, self.colors["grid"])
            self._text(f"{tick:.6g}", 16, y-8, width=62, centered=True, size=12, color=self.colors["muted"])
        self._line(left, top, left, bottom, self.colors["border"])
        self._line(left, bottom, left+width, bottom, self.colors["border"])
        gap = self.spacing
        inset = (width-len(self.rows)*gap)/2
        label_width = gap-6
        label_size = 10 if gap < 48 else 12
        value_format = ".4g" if gap < 64 else ".6g"
        name_height = 0
        for index, row in enumerate(self.rows):
            x = left+inset+(index+.5)*gap
            color = self._color(index)
            name = self._text(row.label, x-label_width/2, bottom+12, tex=True, width=label_width,
                              centered=True, size=label_size)
            name.setToolTip(self._tooltip(row))
            name_height = max(name_height, name.boundingRect().height())
            if row.f10 is not None:
                y = project(row.f10)
                half = min(30, gap*.28)
                self._level(row, "f10", x-half, y, x+half, y, color)
                if self.show_values:
                    value = self._text(format(row.f10, value_format), x-label_width/2, y,
                                       color=color, width=label_width, centered=True, bold=True, size=label_size)
                    value.setY(y-value.boundingRect().height()-5)
                if self.show_f21 and row.f21 is not None:
                    other_y = project(row.f21)
                    half = min(36, gap*.35)
                    self._level(row, "f21", x-half, other_y, x+half, other_y, color)
                    if self.show_values:
                        label_y = max(other_y+7, y+10) if abs(other_y-y) < 26 else other_y+7
                        self._text(format(row.f21, value_format), x-label_width/2, label_y,
                                   color=color, width=label_width, centered=True, size=label_size)
        status_top = bottom+16+name_height
        status_height = 0
        for index, row in enumerate(self.rows):
            if gap < 48:
                continue  # The complete legend retains statuses in compact layouts.
            x = left+inset+(index+.5)*gap
            text = "未标定" if row.f10 is None else "已标定"
            status = self._text(text, x-label_width/2, status_top, width=label_width, centered=True, size=11,
                                color=self.colors["warning"] if row.f10 is None else self.colors["success"])
            status_height = max(status_height, status.boundingRect().height())
        return status_top+status_height+30

    def _horizontal(self):
        left, top, width = 200, 180, 835
        gap = self.spacing
        height = max(112, len(self.rows)*gap)
        compact = gap < 48
        project = lambda value: left+(value-self.low)/(self.high-self.low)*width
        self._text("跃迁频率 / GHz", left, 132, color=self.colors["muted"], size=12)
        for tick in self.ticks:
            x = project(tick)
            self._line(x, top, x, top+height, self.colors["grid"])
            self._text(f"{tick:.6g}", x-38, top-26, width=76, centered=True, size=12, color=self.colors["muted"])
        self._line(left, top, left+width, top, self.colors["border"])
        for index, row in enumerate(self.rows):
            y = top+gap/2+index*gap
            color = self._color(index)
            name = self._text(row.label, 28, y-(8 if compact else 19), tex=True, width=145,
                              size=10 if compact else 13)
            name.setToolTip(self._tooltip(row))
            if not compact:
                self._text("未标定" if row.f10 is None else "已标定", 28, y+5, size=11,
                           color=self.colors["warning"] if row.f10 is None else self.colors["success"])
            if row.f10 is None:
                continue
            x = project(row.f10)
            half = min(12, gap*.23)
            self._level(row, "f10", x, y-half, x, y+half, color)
            if self.show_values:
                lower = self.show_f21 and row.f21 is not None and row.f10 < row.f21
                self._text(f"{row.f10:.6g}", x-68 if lower else x+8, y-8,
                           color=color, width=60, centered=True, bold=True, size=10 if compact else 12)
            if self.show_f21 and row.f21 is not None:
                other_x = project(row.f21)
                half = min(17, gap*.35)
                self._level(row, "f21", other_x, y-half, other_x, y+half, color)
                if self.show_values:
                    self._text(f"{row.f21:.6g}", other_x-68 if row.f21 <= row.f10 else other_x+8, y-8,
                               color=color, width=60, centered=True, size=10 if compact else 12)
            elif self.show_f21 and not compact:
                self._text("非谐未登记", left+12, y+5, color=self.colors["muted"], size=11)
        return top+height+28

    def _legend(self, top):
        self._line(28, top, self.figure_width-28, top, self.colors["border"])
        self._text("全比特图例 · 标定状态以当前工作点的 f_{10} 为准", 28, top+16, bold=True, tex=True)
        x, y, row_height = 28, top+51, 0
        limit = min(self.figure_width-28, 1250)
        for index, row in enumerate(self.rows):
            status = "未标定" if row.f10 is None else "已标定"
            if self.show_f21 and row.f10 is not None and row.alpha is None:
                status += " · 非谐未登记"
            item = self._text(f"{row.label} · {status}", 0, 0, tex=True)
            bounds = item.boundingRect()
            entry_width = bounds.width()+50
            if x > 28 and x+entry_width > limit:
                x = 28; y += row_height+14; row_height = 0
            sample = self._line(x, y+10, x+26, y+10,
                                self._color(index) if row.f10 is not None else self.colors["muted"],
                                3, Qt.PenStyle.SolidLine if row.f10 is not None else Qt.PenStyle.DotLine)
            sample.setToolTip(self._tooltip(row))
            item.setPos(x+36, y); item.setToolTip(self._tooltip(row))
            self.legend_items[row.ident] = item
            x += entry_width+12; row_height = max(row_height, bounds.height())
        if self.show_f21:
            self._text("f_{21} = f_{10} + α/1000（f_{10}：GHz，α：MHz；α 保留符号）。虚线为计算值，未登记非谐时留空。",
                       28, y+row_height+20, color=self.colors["muted"], width=self.figure_width-56, size=12, tex=True)

    def export(self, path, width=2000):
        path = Path(path)
        source = self.content_rect
        height = round(width*source.height()/source.width())
        if path.suffix.lower() == ".svg":
            device = QSvgGenerator(); device.setFileName(str(path))
            device.setSize(QSize(width, height)); device.setViewBox(QRectF(0, 0, width, height))
            device.setTitle("比特能级图 · 跃迁频率")
        else:
            device = QImage(width, height, QImage.Format.Format_ARGB32)
            device.fill(QColor(self.colors["panel"]))
        painter = QPainter(device)
        if not painter.isActive():
            raise OSError(f"无法保存图片：{path}")
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.render(painter, QRectF(0, 0, width, height), source)
        painter.end()
        if isinstance(device, QImage) and not device.save(str(path), "PNG"):
            raise OSError(f"无法保存图片：{path}")


class EnergyView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.fitted = True

    def fit_graph(self):
        self.fitted = True
        if self.scene() is not None:
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        self.fitted = False
        self.scale(1.15 if event.angleDelta().y() > 0 else 1/1.15,
                   1.15 if event.angleDelta().y() > 0 else 1/1.15)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_graph()

    def showEvent(self, event):
        super().showEvent(event)
        if self.fitted:
            self.fit_graph()


class EnergyLevelsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []; self.chip_name = ""; self.point = "default"; self.theme = "light"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.context = QLabel(); toolbar.addWidget(self.context, 1)
        self.style_combo = QComboBox()
        self.style_combo.addItem("分列能级图", "columns")
        self.style_combo.addItem("横向频谱图", "horizontal")
        toolbar.addWidget(self.style_combo)
        self.show_f21 = QCheckBox("显示 f21（计算值）")
        toolbar.addWidget(self.show_f21)
        fit = QPushButton("适配能级图"); fit.clicked.connect(lambda: self.view.fit_graph())
        toolbar.addWidget(fit)
        self.export_button = QPushButton("导出图片"); self.export_button.clicked.connect(self.export_image)
        toolbar.addWidget(self.export_button); layout.addLayout(toolbar)
        density = QHBoxLayout()
        density.addWidget(QLabel("比特间距"))
        self.spacing_slider = QSlider(Qt.Orientation.Horizontal)
        self.spacing_slider.setRange(24, 200); self.spacing_slider.setValue(72)
        self.spacing_slider.setMaximumWidth(280)
        density.addWidget(self.spacing_slider)
        self.spacing = QSpinBox()
        self.spacing.setRange(24, 200); self.spacing.setValue(72); self.spacing.setSingleStep(4)
        self.spacing.setSuffix(" px")
        self.spacing.setToolTip("调整分列图的列距或横向图的行距；缩小间距可在同一画幅容纳更多比特")
        density.addWidget(self.spacing)
        self.show_values = QCheckBox("显示数值"); self.show_values.setChecked(True)
        self.show_values.setToolTip("紧凑间距下可关闭数值标签，完整数值仍可悬停查看")
        density.addWidget(self.show_values); density.addStretch()
        self.frequency_source = QComboBox()
        self.frequency_source.addItem("频率来源：自动", "auto")
        for key, label in FREQUENCY_SOURCES.items():
            self.frequency_source.addItem(label, key)
        self.frequency_source.setToolTip("自动按偏置频率、顶点频率、通用比特频率的顺序选择一种来源；全图不混用")
        density.addWidget(self.frequency_source)
        layout.addLayout(density)
        self.view = EnergyView(); layout.addWidget(self.view, 1)
        self.style_combo.currentIndexChanged.connect(self.redraw)
        self.show_f21.toggled.connect(self.redraw)
        self.spacing_slider.valueChanged.connect(self.spacing.setValue)
        self.spacing.valueChanged.connect(self.spacing_slider.setValue)
        self.spacing.valueChanged.connect(self.redraw)
        self.show_values.toggled.connect(self.redraw)
        hint = QLabel("工作点跟随主窗口 · 滚轮缩放、拖动画布；滚动条可查看所有比特及图例 · 悬停线段查看数值、时间和来源")
        hint.setWordWrap(True); hint.setProperty("role", "muted"); layout.addWidget(hint)
        self.export_button.setEnabled(False)

    def refresh(self, rows, chip_name, point, theme):
        self.rows, self.chip_name, self.point, self.theme = rows, chip_name, point, theme
        self.context.setText(f"工作点：{point}")
        self.export_button.setEnabled(bool(rows))
        self.redraw()

    def redraw(self, *_):
        old_scene = self.view.scene()
        self.figure = EnergyScene(self.rows, self.chip_name, self.point, self.show_f21.isChecked(),
                                  self.style_combo.currentData(), self.theme,
                                  self.spacing.value(), self.show_values.isChecked())
        self.view.setScene(self.figure)
        if old_scene is not None:
            old_scene.deleteLater()
        self.view.fit_graph()

    def export_image(self):
        path, fmt = QFileDialog.getSaveFileName(self, "导出比特能级图", "比特能级图.png",
                                               "PNG 图像 (*.png);;SVG 矢量图 (*.svg)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".svg" if "SVG" in fmt else ".png"
        try:
            self.figure.export(path)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

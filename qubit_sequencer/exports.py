"""Portable parameter tables. Visual exports live in diagram.py."""

from __future__ import annotations

import csv
import math
import re
from itertools import groupby
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .device_types import DEVICE_NAMES
from .storage import ChipStore, SCHEMA_VERSION, display_timestamp
from .wiring_store import WIRING_EXPORT_HEADERS, wiring_export_values
from .readout_store import readout_endpoints
from .sample_box_store import BOX_ROLES, box_connection_label, box_number_source, port_number_key
from .parameter_rules import (BIAS_Z_HEADERS, PI_AMPLITUDE, PI_LENGTH, PI_PRODUCT,
                              format_bias_z, pi_product, record_value, uses_bias_z)


def _header(definition) -> str:
    suffix = f" [{definition['unit']}]" if definition["unit"] else ""
    return f"{definition['label']}{suffix} ({definition['key']})"


def build_tables(store: ChipStore, point: str = "default",
                 include_history: bool = False) -> dict[str, tuple[list[str], list[list[object]]]]:
    """Create a stable, wide current-value snapshot plus optional long history."""
    records = store.current_records(point)
    values = {key: record_value(row) for key, row in records.items()}
    defs = {scope: store.definitions(scope) for scope in (*DEVICE_NAMES, "coupling", "link")}
    devices = {d["id"]: d for d in store.devices()}
    meta = store.meta()
    tables: dict[str, tuple[list[str], list[list[object]]]] = {
        "芯片信息": (["项目", "值"], [
            ["芯片名称", meta["chip_name"]], ["模板", meta["template"]],
            ["创建时间", display_timestamp(meta["created_at"])], ["导出工作点", point],
            ["格式版本", SCHEMA_VERSION],
        ])
    }
    for kind, title in DEVICE_NAMES.items():
        extra_headers = (["图纸编号"] if kind == "qubit" else ["线长 [格]", "方向", "起点 X", "起点 Y", "终点 X", "终点 Y"] if kind == "readout" else
                         ["所属读取线", "线上相对位置"] if kind == "filter" else [])
        fields = []
        builtins = {d["key"] for d in defs[kind] if not d["is_custom"]}
        for definition in defs[kind]:
            key = definition["key"]
            fields.append((_header(definition), key, "value"))
            if uses_bias_z(definition):
                fields.append((BIAS_Z_HEADERS[key], key, "bias"))
            if key == PI_AMPLITUDE and {PI_LENGTH, PI_AMPLITUDE} <= builtins:
                fields.append((f"π 脉冲面积（自动计算） [ns × 控制值] ({PI_PRODUCT})", PI_PRODUCT, "product"))
        type_header = "自定义类型" if kind == "custom" else "类型"
        headers = ["ID", "名称", type_header, "角色", "格点 X", "格点 Y", "备注"] + extra_headers + [field[0] for field in fields]
        rows = []
        for device in store.devices(kind):
            extra = []
            if kind == "qubit":
                extra = [device["drawing_number"]]
            elif kind == "readout":
                start, end = readout_endpoints(device)
                extra = [device["readout_length"], device["readout_direction"], *start, *end]
            elif kind == "filter":
                link = store.filter_readout(device["id"])
                extra = [devices[link["readout_id"]]["label"], link["position"]] if link else ["", None]
            ident = device["id"]
            rows.append([ident, device["label"], device["subtype"],
                         device["role"], device["x"], device["y"], device["notes"]] + extra + [
                format_bias_z(records.get((ident, key))) if field == "bias" else
                pi_product(records.get((ident, PI_LENGTH)), records.get((ident, PI_AMPLITUDE))) if field == "product" else
                values.get((ident, key)) for _, key, field in fields
            ])
        tables[title] = headers, rows
    tables["读取线连接"] = (["ID", "读取线", "关联元件", "元件类别", "线上相对位置", "备注"], [
        [link["id"], devices[link["readout_id"]]["label"], devices[link["device_id"]]["label"],
         devices[link["device_id"]]["kind"], link["position"], link["notes"]] for link in store.readout_links()
    ])
    headers = ["ID", "比特 A", "比特 B", "方式", "耦合器", "备注"] + [
        _header(d) for d in defs["coupling"]
    ]
    rows = []
    for coupling in store.couplings():
        rows.append([coupling["id"], devices[coupling["qubit_a"]]["label"],
                     devices[coupling["qubit_b"]]["label"], coupling["mode"],
                     devices[coupling["coupler_id"]]["label"] if coupling["coupler_id"] else "",
                     coupling["notes"]] + [
                         values.get((coupling["id"], d["key"])) for d in defs["coupling"]
                     ])
    tables["耦合"] = headers, rows
    headers = ["ID", "比特", "耦合器", "备注"] + [_header(d) for d in defs["link"]]
    rows = []
    for link in store.links():
        rows.append([link["id"], devices[link["qubit_id"]]["label"],
                     devices[link["coupler_id"]]["label"], link["notes"]] + [
                         values.get((link["id"], d["key"])) for d in defs["link"]])
    tables["比特-耦合器连接"] = headers, rows
    rows = []
    for line in store.lines():
        ids = store.line_devices(line["id"])
        details = wiring_export_values(store.line_details(line["id"]))
        for device_id in ids or [None]:
            rows.append([line["id"], line["line_type"], line["cryostat"],
                         line["port"], line["channel"],
                         devices[device_id]["label"] if device_id else "", line["notes"]] + details)
    tables["接线"] = (["线路 ID", "线路类型", "制冷机", "端口", "通道", "器件", "备注"] + WIRING_EXPORT_HEADERS, rows)
    ports = store.sample_box_ports()
    if ports:
        tables["样品盒接口"] = (["接口编号", "编号来源", "信号名称", "器件", "方向"], [
            [p["number"], box_number_source(p), p["signal_name"], p["device_label"] or "", BOX_ROLES.get(p["role"], "")]
            for p in sorted(ports, key=lambda p: (0, int(p["number"])) if p["number"].isdecimal() else (1, p["number"]))
        ])
    if include_history:
        definitions = {d["key"]: d for d in store.definitions()}
        coupling_names = {c["id"]: f"{devices[c['qubit_a']]['label']}-{devices[c['qubit_b']]['label']}"
                          for c in store.couplings()}
        link_names = {link["id"]: f"{devices[link['qubit_id']]['label']}-{devices[link['coupler_id']]['label']}"
                      for link in store.links()}
        rows = []
        for record in store.records():
            owner = record["device_id"] or record["coupling_id"] or record["link_id"]
            definition = definitions[record["definition_key"]]
            rows.append([
                owner, devices[owner]["label"] if owner in devices else coupling_names.get(owner, link_names.get(owner, "")),
                definition["key"], definition["label"], definition["unit"],
                record["numeric_value"] if record["numeric_value"] is not None else record["text_value"],
                record["point"], record["bias_value"], record["bias_unit"],
                display_timestamp(record["observed_at"]), record["source"], record["notes"],
                "是" if record["is_current"] else "否",
            ])
        tables["参数历史"] = (["对象 ID", "对象", "参数键", "参数名", "单位", "值", "工作点",
                            "偏置值", "偏置单位", "记录时间", "来源", "备注", "当前值"], rows)
    return tables


def export_xlsx(store: ChipStore, path: str | Path, point: str = "default",
                include_history: bool = False) -> None:
    _write_xlsx(path, build_tables(store, point, include_history))


def _write_xlsx(path, tables, *, sample_name=None, diagram_image=None):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, (headers, rows) in tables.items():
        if title == DEVICE_NAMES["custom"] and not rows:
            continue
        sheet = workbook.create_sheet(title)
        header_row = 1
        if sample_name is not None:
            _wiring_sample_heading(sheet, sample_name, len(headers))
            header_row = 2
        sheet.append(headers)
        for row in rows:
            sheet.append(["" if value is None else value for value in row])
        sheet.freeze_panes = f"A{header_row + 1}"
        sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{sheet.max_row}"
        for cell in sheet[header_row]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="24436B")
            cell.alignment = Alignment(wrap_text=True)
        sheet.row_dimensions[header_row].height = 34
        for index, header in enumerate(headers, 1):
            sheet.column_dimensions[get_column_letter(index)].width = min(42, max(13, len(header) * 1.4))
    if diagram_image is not None:
        _append_wiring_diagram_sheet(workbook, diagram_image)
    workbook.save(path)


def _write_csv(path, headers, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def export_sample_box_wiring(store: ChipStore, path: str | Path, connections, *, diagram_image=None) -> None:
    path = Path(path)
    headers = ["圈序", "样品盒接口", "制冷机端口", "信号名称", "元件"]
    drawing = store.sample_box_appearance()["drawing"]
    if drawing["cover"]:
        x, y, width, height = drawing["cover"]
        center_x, center_y = x + width / 2, y + height / 2
    else:
        xs, ys = zip(*drawing["outline"])
        center_x, center_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    origin_x, origin_y = drawing["origin"]
    pitch = drawing["pitch"]
    center_x, center_y = (center_x - origin_x) / pitch, (center_y - origin_y) / pitch

    def position(port):
        dx, dy = port["x"] - center_x, port["y"] - center_y
        return max(abs(dx), abs(dy)), math.atan2(dx, -dy) % math.tau

    rows = []
    ports = sorted(store.sample_box_ports(), key=position)
    for ring, (_, members) in enumerate(groupby(ports, key=lambda port: position(port)[0]), 1):
        for port in members:
            connection = connections.get(port["id"])
            if connection is None:
                continue
            rows.append([ring, port["number"], box_connection_label(connection),
                         port["signal_name"], port["device_label"] or ""])
    if path.suffix.lower() == ".xlsx":
        _write_xlsx(path, {"样品盒接线": (headers, rows)}, sample_name=store.meta()["chip_name"],
                    diagram_image=diagram_image)
    elif path.suffix.lower() == ".csv":
        _write_csv(path, headers, rows)
    else:
        raise ValueError("请选择 XLSX 或 CSV 格式")


def _wiring_drawing_number(port):
    drawing = (port["drawing_number"] or "").strip()
    if drawing:
        return drawing
    label = port["device_label"] or ""
    match = re.fullmatch(r"([qcrf])(?:_\{(\d+|\d,\s*\d)\}|_?(\d+))", label, re.IGNORECASE)
    if match:
        prefix, subscript, number = match.groups()
        return prefix.upper() + re.sub(r"[,\s]", "", subscript or number)
    return label


def sample_box_wiring_blocks(ports, connections, *, combine_readout_plates=False):
    """Build table blocks in port order, optionally sharing rows across readout plates."""
    ordered = sorted(connections.values(), key=lambda c: (
        port_number_key(c["board"]), port_number_key(c["number"]), port_number_key(c["branch"] or "")))
    block_size = 20
    def plate_key(connection):
        board = connection["board"]
        return None if combine_readout_plates and board in {"D", "E", "F"} else board

    for _, group in groupby(ordered, key=plate_key):
        plate = list(group)
        for start in range(0, len(plate), block_size):
            block = plate[start:start + block_size]
            boards = "、".join(dict.fromkeys(connection["board"] for connection in block))
            columns = [("端口号", "样品盒端口", "图纸编号")]
            for connection in block:
                port = ports[connection["box_port_id"]]
                columns.append((box_connection_label(connection), port["number"], _wiring_drawing_number(port)))
            yield f"{boards}盘端口接线一览", columns


def _wiring_cell_color(row, column):
    if row == 0:
        return "456FBF"
    return "CCD3E7" if column == 0 else "E7E7E7"


def _wiring_sample_heading(sheet, sample_name, columns):
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    heading = sheet.cell(1, 1, f"样品名称：{sample_name}")
    heading.font = Font(bold=True, size=18)
    heading.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 35


def _append_wiring_diagram_sheet(book, image_path):
    sheet = book.create_sheet("接线图")
    sheet.sheet_view.showGridLines = False
    diagram = WorkbookImage(str(image_path))
    landscape = diagram.width > diagram.height
    max_width, max_height = (940, 660) if landscape else (660, 940)
    scale = min(max_width / diagram.width, max_height / diagram.height)
    diagram.width = round(diagram.width * scale)
    diagram.height = round(diagram.height * scale)
    sheet.add_image(diagram, "A1")
    sheet.column_dimensions["A"].width = math.ceil(diagram.width / 7) + 1
    sheet.sheet_format.defaultRowHeight = 15
    sheet.print_area = f"A1:A{math.ceil(diagram.height / 20)}"
    sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE if landscape else sheet.ORIENTATION_PORTRAIT
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = sheet.page_setup.fitToHeight = 1


def export_sample_box_wiring_matrix(path: Path, blocks, *, sample_name, diagram_image) -> None:
    """Write a plate-by-plate wiring sheet for the exported diagrams."""
    book = Workbook()
    sheet = book.active
    sheet.title = "接线表"
    sheet.sheet_view.showGridLines = False
    sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.column_dimensions["A"].width = 14
    line = Side(style="thin", color="000000")
    border = Border(left=line, right=line, top=line, bottom=line)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    header_font = Font(bold=True, color="FFFFFF", size=12)
    _wiring_sample_heading(sheet, sample_name, max(6, max(len(columns) for _, columns in blocks)))
    row = 2
    for title, columns in blocks:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(columns))
        sheet.cell(row, 1, title).font = Font(bold=True, size=17)
        sheet.row_dimensions[row].height = 27
        for column, values in enumerate(columns):
            if column > 0:
                sheet.column_dimensions[get_column_letter(column + 1)].width = 12
            for offset, value in enumerate(values):
                cell = sheet.cell(row + 1 + offset, column + 1, value)
                cell.data_type = "s"
                cell.alignment = center
                cell.border = border
                cell.fill = PatternFill("solid", fgColor=_wiring_cell_color(offset, column))
                if offset == 0:
                    cell.font = header_font
        for offset in range(1, 4):
            sheet.row_dimensions[row + offset].height = 32
        row += 5
    _append_wiring_diagram_sheet(book, diagram_image)
    book.save(path)


def export_sample_box_wiring_pdf(path: Path, blocks, *, sample_name, diagram_scene) -> None:
    from PySide6.QtCore import QMarginsF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPdfWriter

    writer = QPdfWriter(str(path))
    writer.setTitle(f"{sample_name} · 样品盒接线表")
    writer.setResolution(72)
    page_layout = QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Landscape,
                              QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
    writer.setPageLayout(page_layout)
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"无法写入 {path}")
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = painter.font()
    font.setPointSize(9)
    header = QFont(font)
    header.setBold(True)
    title_font = QFont(header)
    title_font.setPointSize(15)
    centered = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWrapAnywhere)
    width, height = writer.width(), writer.height()
    label_width = 72
    sample_heading = f"样品名称：{sample_name}"
    start_y = 1
    try:
        painter.setFont(title_font)
        painter.setPen(Qt.GlobalColor.black)
        heading_area = QRectF(1, start_y, width - 2, height)
        heading_flags = int(Qt.TextFlag.TextWrapAnywhere)
        heading_height = painter.boundingRect(heading_area, heading_flags, sample_heading).height()
        heading_area.setHeight(heading_height + 2)
        painter.drawText(heading_area, heading_flags, sample_heading)
        y = start_y = heading_height + 14
        has_rows = False
        for title, columns in blocks:
            has_rows = True
            port_width = min(82, (width - label_width - 2) / (len(columns) - 1))
            widths = [label_width] + [port_width] * (len(columns) - 1)
            rows = list(zip(*columns))
            row_heights = []
            for index, values in enumerate(rows):
                painter.setFont(header if index == 0 else font)
                text_height = max(painter.boundingRect(QRectF(0, 0, cell_width - 6, height), centered, value).height()
                                  for cell_width, value in zip(widths, values))
                row_heights.append(max(24, text_height + 8))
            if y > start_y and y + 26 + sum(row_heights) > height - 1:
                if not writer.newPage():
                    raise OSError(f"无法写入 {path}")
                y = start_y = 1
            painter.setFont(title_font)
            painter.setPen(Qt.GlobalColor.black)
            painter.drawText(QRectF(1, y, width - 2, 24), int(Qt.AlignmentFlag.AlignVCenter), title)
            y += 26
            for row, (values, row_height) in enumerate(zip(rows, row_heights)):
                painter.setFont(header if row == 0 else font)
                x = 1
                for column, (value, cell_width) in enumerate(zip(values, widths)):
                    cell = QRectF(x, y, cell_width, row_height)
                    painter.setPen(Qt.GlobalColor.black)
                    painter.setBrush(QColor("#" + _wiring_cell_color(row, column)))
                    painter.drawRect(cell)
                    painter.setPen(Qt.GlobalColor.white if row == 0 else Qt.GlobalColor.black)
                    painter.drawText(cell.adjusted(3, 2, -3, -2), centered, value)
                    x += cell_width
                y += row_height
            y += 16
        if not has_rows:
            painter.setFont(font)
            painter.drawText(QRectF(1, y, width - 2, 24), int(Qt.AlignmentFlag.AlignLeft), "暂无已分配的样品盒接线。")
        bounds = diagram_scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        if not writer.newPage():
            raise OSError(f"无法写入 {path}")
        scale = min(writer.width() / bounds.width(), writer.height() / bounds.height())
        image_width, image_height = bounds.width() * scale, bounds.height() * scale
        frame = QRectF((writer.width() - image_width) / 2, (writer.height() - image_height) / 2,
                       image_width, image_height)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        diagram_scene.render(painter, frame, bounds)
    finally:
        painter.end()


def export_csv(store: ChipStore, directory: str | Path, point: str = "default",
               include_history: bool = False) -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = {"芯片信息": "chip", "比特": "qubits", "耦合器": "couplers",
             "耦合": "couplings", "比特-耦合器连接": "qubit_coupler_links",
             "接线": "wiring", "参数历史": "history",
             "读取线": "readout_lines", "可调 filter": "filters", "自定义元件": "custom_devices"}
    names["读取线连接"] = "readout_links"
    written = []
    for title, (headers, rows) in build_tables(store, point, include_history).items():
        if title == DEVICE_NAMES["custom"] and not rows:
            continue
        path = directory / f"{names[title]}.csv"
        _write_csv(path, headers, rows)
        written.append(path)
    return written

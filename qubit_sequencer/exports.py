"""Portable parameter tables. Visual exports live in diagram.py."""

from __future__ import annotations

import csv
import math
from itertools import groupby
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .device_types import DEVICE_NAMES
from .storage import ChipStore, SCHEMA_VERSION, display_timestamp
from .wiring_store import WIRING_EXPORT_HEADERS, wiring_export_values
from .readout_store import readout_endpoints
from .sample_box_store import BOX_ROLES, box_connection_label, box_number_source
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


def _write_xlsx(path, tables):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, (headers, rows) in tables.items():
        if title == DEVICE_NAMES["custom"] and not rows:
            continue
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for row in rows:
            sheet.append(["" if value is None else value for value in row])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="24436B")
            cell.alignment = Alignment(wrap_text=True)
        sheet.row_dimensions[1].height = 34
        for index, header in enumerate(headers, 1):
            sheet.column_dimensions[get_column_letter(index)].width = min(42, max(13, len(header) * 1.4))
    workbook.save(path)


def _write_csv(path, headers, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def export_sample_box_wiring(store: ChipStore, path: str | Path, connections) -> None:
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
        _write_xlsx(path, {"样品盒接线": (headers, rows)})
    elif path.suffix.lower() == ".csv":
        _write_csv(path, headers, rows)
    else:
        raise ValueError("请选择 XLSX 或 CSV 格式")


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

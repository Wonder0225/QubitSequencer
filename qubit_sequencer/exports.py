"""Portable parameter tables. Visual exports live in diagram.py."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .storage import ChipStore, SCHEMA_VERSION


def _header(definition) -> str:
    suffix = f" [{definition['unit']}]" if definition["unit"] else ""
    return f"{definition['label']}{suffix} ({definition['key']})"


def build_tables(store: ChipStore, point: str = "default",
                 include_history: bool = False) -> dict[str, tuple[list[str], list[list[object]]]]:
    """Create a stable, wide current-value snapshot plus optional long history."""
    values = store.current_values(point)
    defs = {scope: store.definitions(scope) for scope in ("qubit", "coupler", "coupling", "link")}
    devices = {d["id"]: d for d in store.devices()}
    meta = store.meta()
    tables: dict[str, tuple[list[str], list[list[object]]]] = {
        "芯片信息": (["项目", "值"], [
            ["芯片名称", meta["chip_name"]], ["模板", meta["template"]],
            ["创建时间", meta["created_at"]], ["导出工作点", point],
            ["格式版本", SCHEMA_VERSION],
        ])
    }
    for kind, title in (("qubit", "比特"), ("coupler", "耦合器")):
        headers = ["ID", "名称", "类型", "角色", "格点 X", "格点 Y", "备注"] + [
            _header(d) for d in defs[kind]
        ]
        rows = []
        for device in store.devices(kind):
            rows.append([device["id"], device["label"], device["subtype"],
                         device["role"], device["x"], device["y"], device["notes"]] + [
                values.get((device["id"], d["key"])) for d in defs[kind]
            ])
        tables[title] = headers, rows
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
        for device_id in ids or [None]:
            rows.append([line["id"], line["line_type"], line["cryostat"],
                         line["port"], line["channel"],
                         devices[device_id]["label"] if device_id else "", line["notes"]])
    tables["接线"] = (["线路 ID", "线路类型", "制冷机", "端口", "通道", "器件", "备注"], rows)
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
                record["observed_at"], record["source"], record["notes"],
                "是" if record["is_current"] else "否",
            ])
        tables["参数历史"] = (["对象 ID", "对象", "参数键", "参数名", "单位", "值", "工作点",
                            "偏置值", "偏置单位", "记录时间", "来源", "备注", "当前值"], rows)
    return tables


def export_xlsx(store: ChipStore, path: str | Path, point: str = "default",
                include_history: bool = False) -> None:
    tables = build_tables(store, point, include_history)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, (headers, rows) in tables.items():
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


def export_csv(store: ChipStore, directory: str | Path, point: str = "default",
               include_history: bool = False) -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = {"芯片信息": "chip", "比特": "qubits", "耦合器": "couplers",
             "耦合": "couplings", "比特-耦合器连接": "qubit_coupler_links",
             "接线": "wiring", "参数历史": "history"}
    written = []
    for title, (headers, rows) in build_tables(store, point, include_history).items():
        path = directory / f"{names[title]}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)
        written.append(path)
    return written

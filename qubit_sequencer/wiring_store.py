"""Per-line experimental wiring registration, independent of chip parameters."""

import json
import math
from copy import deepcopy

WIRING_STATEMENT = """CREATE TABLE wiring_details (
    line_id TEXT PRIMARY KEY REFERENCES wiring_lines(id) ON DELETE CASCADE,
    details_json TEXT NOT NULL)"""
WIRING_SCHEMA = WIRING_STATEMENT + ";\n"
LINE_TYPES = ["XY", "Z", "XY/Z", "读取输入", "读取输出", "耦合器磁通", "滤波器控制", "泵浦", "直流", "读出", "其他"]
STAGES = ["室温", "3 K", "STILL", "50 mK", "MC"]
COMPONENT_TYPES = ["衰减器", "低通滤波器", "红外滤波器", "隔离器", "环行器", "合路器", "微波开关", "其他"]
METADATA = [("board", "盘号"), ("instrument", "室温仪器"), ("cable", "线缆编号 / 颜色"),
            ("sample_pin", "样品引脚"), ("drawing_label", "图纸编号 / 信号名称")]
TABLE_FIELDS = {
    "components": [("stage", "温级 / 位置"), ("kind", "元件类型"), ("name", "名称 / 编号"),
                   ("value", "数值"), ("unit", "单位"), ("terminal", "端子 / 通道"), ("notes", "备注")],
    "amplifiers": [("stage", "温级 / 位置"), ("model", "型号"), ("serial", "编号"),
                   ("band", "频段 [GHz]"), ("pins", "电源引脚 / 接线"), ("notes", "备注")],
    "biases": [("amplifier", "放大器编号"), ("temperature", "温度 [K]"),
               ("vd", "Vd [V]"), ("id", "Id [mA]"), ("vg", "Vg [V]"), ("notes", "备注")],
    "checks": [("time", "检查时间"), ("sample_state", "装样状态"), ("power", "测试功率 [dBm]"),
               ("band", "测试频段 [GHz]"), ("transmission", "实测传输 [dB]"), ("result", "结论"), ("notes", "条件 / 备注")],
}
NUMERIC_FIELDS = {"components": ("value",), "biases": ("temperature", "vd", "id", "vg"),
                  "checks": ("power", "transmission")}


def normalise_details(details):
    result = deepcopy(details)
    for table, keys in NUMERIC_FIELDS.items():
        labels = dict(TABLE_FIELDS[table])
        for row in result.get(table, []):
            for key in keys:
                value = row.get(key)
                if value is None or value == "":
                    row[key] = None
                else:
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        raise ValueError(f"{labels[key]}必须为数值，未登记时留空") from None
                    if not math.isfinite(number):
                        raise ValueError(f"{labels[key]}必须为有限数值")
                    row[key] = number
    return result


def text_value(value):
    return "" if value is None else f"{value:g}" if isinstance(value, (int, float)) else str(value)


def rows_text(details, table):
    """Human-readable exports retain every row, unit, condition and note."""
    lines = []
    for row in details.get(table, []):
        parts = [f"{label}: {text_value(row.get(key))}" for key, label in TABLE_FIELDS[table]
                 if row.get(key) is not None and row.get(key) != ""]
        lines.append("；".join(parts))
    return "\n".join(lines)


def wiring_export_values(details):
    return [details.get(key, "") for key, _ in METADATA] + [rows_text(details, table)
        for table in ("components", "amplifiers", "biases", "checks")]


WIRING_EXPORT_HEADERS = [label for _, label in METADATA] + ["温级元件配置", "放大链", "放大器工作参数", "检线记录"]


class WiringStore:
    def line_details(self, line_id):
        self.line(line_id)
        row = self.db.execute("SELECT details_json FROM wiring_details WHERE line_id=?", (line_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _write_line_details(self, line_id, details):
        encoded = json.dumps(normalise_details(details), ensure_ascii=False, allow_nan=False)
        self.db.execute("INSERT INTO wiring_details VALUES (?,?) ON CONFLICT(line_id) "
                        "DO UPDATE SET details_json=excluded.details_json", (line_id, encoded))

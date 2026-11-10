"""Load built-in and user-provided sample-box templates."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


BUILTIN_TEMPLATES = Path(__file__).resolve().parent / "assets" / "sample_box_templates"
DEFAULT_TEMPLATE = "lab-100"
TEMPLATE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class SampleBoxTemplate:
    key: str
    name: str
    ports: tuple[tuple[str, int, int, str], ...]
    drawing: dict


def _number(value):
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("接口编号必须是正整数或非空文本")


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("绘图坐标和尺寸必须是有限数值")
    return value


def _coordinates(values, length):
    if not isinstance(values, (list, tuple)) or len(values) != length:
        raise ValueError(f"绘图坐标应有 {length} 个数值")
    return tuple(map(_finite, values))


def _drawing(raw, columns, rows):
    if not isinstance(raw, dict):
        raise ValueError("drawing 必须是一个对象")
    origin = _coordinates(raw.get("origin", (46, 46)), 2)
    pitch = _finite(raw.get("pitch", 44))
    radius = _finite(raw.get("radius", pitch / 3))
    if pitch <= 0 or radius <= 0 or 2 * radius >= pitch:
        raise ValueError("接口间距和半径应为正数，直径应小于间距")
    x, y = origin
    left, top, right, bottom = x-pitch, y-pitch, x+columns*pitch, y+rows*pitch
    outline = [_coordinates(point, 2) for point in raw.get("outline", ((left, top), (right, top), (right, bottom), (left, bottom)))]
    if len(outline) < 3:
        raise ValueError("样品盒轮廓至少需要三个顶点")
    cover = _coordinates(raw["cover"], 4) if raw.get("cover") is not None else None
    holes = [_coordinates(hole, 3) for hole in raw.get("holes", [])]
    if (cover and min(cover[2:]) <= 0) or any(radius <= 0 for _, _, radius in holes):
        raise ValueError("盖板尺寸和定位孔半径必须大于零")
    return dict(origin=origin, pitch=pitch, radius=radius, outline=outline, cover=cover, holes=holes)


def read_sample_box_template(path):
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("模板必须是一个 JSON 对象")
    key, name, rows = (raw.get(k) for k in ("id", "name", "rows"))
    if not all(isinstance(value, str) and value.strip() for value in (key, name)):
        raise ValueError("模板需要非空的 id 和 name")
    if not TEMPLATE_KEY.fullmatch(key.strip()):
        raise ValueError("模板 ID 只能使用字母、数字、下划线和连字符")
    if not isinstance(rows, list) or not rows or not all(isinstance(row, list) and row for row in rows):
        raise ValueError("rows 必须是非空的二维接口编号数组")
    columns = len(rows[0])
    if any(len(row) != columns for row in rows):
        raise ValueError("rows 的每一行应有相同的列数")
    inferred = raw.get("inferred", [])
    if not isinstance(inferred, list):
        raise ValueError("inferred 必须是接口编号数组")
    inferred = set(map(_number, inferred))
    ports = []
    numbers = set()
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            if value is None or (type(value) is int and value == 0):
                continue
            number = _number(value)
            if number in numbers:
                raise ValueError(f"接口编号重复：{number}")
            numbers.add(number)
            ports.append((number, x, y, "inferred" if number in inferred else "photo"))
    if not ports or not inferred <= numbers:
        raise ValueError("模板需要有效接口，且推断编号须出现在 rows 中")
    return SampleBoxTemplate(key.strip(), name.strip(), tuple(ports), _drawing(raw.get("drawing", {}), columns, len(rows)))


def builtin_sample_box_template(key):
    # This also supplies the original drawing for projects made before drawings
    # were stored in the project itself.
    if not TEMPLATE_KEY.fullmatch(key):
        raise ValueError("无效的模板 ID")
    return read_sample_box_template(BUILTIN_TEMPLATES / f"{key}.json")


def load_sample_box_templates(user_directory):
    templates = {}
    errors = []
    for directory in (BUILTIN_TEMPLATES, user_directory):
        seen = set()
        for path in sorted(directory.glob("*.json")):
            try:
                template = read_sample_box_template(path)
                if template.key in seen:
                    raise ValueError(f"同一目录内模板 ID 重复：{template.key}")
                seen.add(template.key)
                templates[template.key] = template
            except (OSError, ValueError, TypeError, OverflowError) as exc:
                errors.append(f"{path.name}：{exc}")
    return templates, errors

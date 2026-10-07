"""Create a layout-image demo without guessing the physical qubit mapping.

Run: python scripts/generate_chip_design_demo.py path/to/layout.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qubit_sequencer.design_ui import read_design_file
from qubit_sequencer.storage import ChipStore


def create_demo(image_path: Path, output: Path):
    _, data = read_design_file(image_path)
    with ChipStore.create(output, "芯片设计图测试示例", "custom") as store:
        store.set_meta("grid_columns", "3")
        store.set_meta("grid_rows", "4")
        store.set_meta("demo_notes", "用户提供的版图；演示器件和参数为模拟数据，图中位置由用户手动标注。")
        store.set_chip_design("chip-design-reference" + image_path.suffix.lower(), data)
        for index in range(12):
            ident = store.add_device(
                "qubit", "fixed_transmon", f"Q_{{{index + 1}}}", index % 3, index // 3,
                notes="演示比特：名称、逻辑坐标及参数均用于测试；实际图中位置由用户标注。",
            )
            for point, offset in (("default", 0), ("idle", -.15)):
                store.set_parameter(
                    ident, "qubit.f01", round(4.8 + .04 * index + offset, 3), point=point,
                    source="DEMO · 模拟数据", notes="用于检查设计图标注的参数弹窗与工作点切换。",
                )
            store.set_parameter(ident, "qubit.t1", 30 + 2 * index, source="DEMO · 模拟数据")
        # The physical image-to-device mapping is deliberately left for the user.


def main():
    parser = argparse.ArgumentParser(description="根据图片创建芯片设计图测试示例，不自动标注器件位置。")
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "examples" / "chip_design_demo.qchip")
    args = parser.parse_args()
    create_demo(args.image, args.output)
    print(args.output)


if __name__ == "__main__":
    main()

"""Build a fully populated, explicitly synthetic d=3 rotated surface-code demo.

Run from the repository root: python scripts/generate_complete_d3.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from qubit_sequencer.diagram import export_diagram
from qubit_sequencer.exports import export_xlsx
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.templates import rotated_surface_code


DEFAULT_PATH = ROOT / "examples" / "surface_d3_complete.qchip"
DESIGN_TIME = "2026-09-01T10:00:00+00:00"
OLD_TIME = "2026-09-15T10:00:00+00:00"
MEASURE_TIME = "2026-09-20T10:00:00+00:00"
SOURCE = "SYNTHETIC DEMO · 非真实芯片测量"


def _record(store: ChipStore, owner: str, key: str, value: float,
            *, design: bool = False, point: str = "default", note: str = "") -> None:
    store.set_parameter(owner, key, round(value, 4), point=point,
                        observed_at=DESIGN_TIME if design else MEASURE_TIME,
                        source="SYNTHETIC DEMO · 设计示例" if design else SOURCE,
                        notes=note)


def _ej(frequency: float, ec: float) -> float:
    return (frequency + ec) ** 2 / (8 * ec)


def _fill_qubit(store: ChipStore, device, index: int) -> None:
    ident = device["id"]
    role_offset = {"data": 0.0, "measure_x": 0.22, "measure_z": 0.36}[device["role"]]
    frequency = 4.78 + role_offset + 0.047 * ((index * 7) % 9)
    anharmonicity = -(207 + 3 * (index % 8))
    ec = -anharmonicity / 1000
    design_f = frequency + 0.026 + 0.006 * (index % 3)
    ej = _ej(design_f, ec)
    measured = {
        "qubit.f01": frequency,
        "qubit.anharmonicity": anharmonicity,
        "qubit.readout_frequency": 6.48 + 0.023 * index,
        "qubit.readout_power": -110.0 + 0.5 * (index % 9),
        "qubit.t1": 27.8 + 2.4 * ((index * 5) % 11),
        "qubit.t2_star": 15.2 + 1.8 * ((index * 3) % 9),
        "qubit.t2_echo": 24.0 + 2.1 * ((index * 7) % 10),
    }
    design = {
        "qubit.design_f01": design_f,
        "qubit.ej_over_h": ej,
        "qubit.ec_over_h": ec,
        "qubit.c_sigma": 19.37 / ec,
        "qubit.ic": ej / 0.496,
        "qubit.junction_asymmetry": 0.012 + 0.004 * (index % 5),
    }
    if index == 0:
        store.set_parameter(ident, "qubit.f01", round(frequency - 0.018, 4),
                            observed_at=OLD_TIME, source=SOURCE,
                            notes="演示：旧值，便于查看参数历史")
    for key, value in measured.items():
        _record(store, ident, key, value)
    for key, value in design.items():
        _record(store, ident, key, value, design=True)
    _record(store, ident, "qubit.f01", frequency - 0.034, point="idle",
            note="演示用第二工作点")
    _record(store, ident, "qubit.t1", measured["qubit.t1"] * 0.94, point="idle")
    for line_type, prefix, channel in (("XY", "XY", "AWG"),
                                      ("Z", "Z", "DAC"),
                                      ("读出", "RO", "ADC")):
        store.add_line(line_type, "DR-DEMO", f"{prefix}-{index+1:02d}",
                       f"{channel}-{index+1:02d}",
                       notes="模拟接线，仅用于软件展示", devices=[ident])


def _fill_coupler(store: ChipStore, device, index: int) -> None:
    ident = device["id"]
    frequency = 6.04 + 0.031 * ((index * 7) % 13)
    anharmonicity = -(250 + 4 * (index % 8))
    ec = -anharmonicity / 1000
    design_f = frequency + 0.045
    ej = _ej(design_f, ec)
    measured = {
        "coupler.f01": frequency,
        "coupler.anharmonicity": anharmonicity,
        "coupler.t1": 9.4 + 1.15 * ((index * 3) % 9),
        "coupler.t2_star": 5.8 + 0.8 * ((index * 5) % 8),
        "coupler.t2_echo": 8.2 + 0.9 * ((index * 7) % 7),
    }
    design = {
        "coupler.design_f01": design_f,
        "coupler.ej_over_h": ej,
        "coupler.ec_over_h": ec,
        "coupler.c_sigma": 19.37 / ec,
        "coupler.ic": ej / 0.496,
        "coupler.junction_asymmetry": 0.01 + 0.003 * (index % 6),
    }
    if index == 0:
        store.set_parameter(ident, "coupler.f01", round(frequency + 0.013, 4),
                            observed_at=OLD_TIME, source=SOURCE,
                            notes="演示：旧值")
    for key, value in measured.items():
        _record(store, ident, key, value)
    for key, value in design.items():
        _record(store, ident, key, value, design=True)
    _record(store, ident, "coupler.f01", frequency + 0.09, point="idle")
    store.add_line("耦合器磁通", "DR-DEMO", f"CFLUX-{index+1:02d}",
                   f"DAC-C{index+1:02d}", notes="模拟接线，仅用于软件展示",
                   devices=[ident])


def build(path: Path) -> None:
    with ChipStore.create(path, "d=3 旋转表面码 · 完整演示（模拟数据）", "surface") as store:
        store.set_meta("sample_data", "SYNTHETIC_DEMO_NOT_MEASURED")
        store.set_meta("sample_description", "所有参数与接线均为模拟值，仅用于软件展示")
        rotated_surface_code(store, 3, "coupler")

        qubits = sorted(store.devices("qubit"), key=lambda d: (d["role"] != "data", d["y"], d["x"]))
        for index, device in enumerate(qubits):
            store.update_device(device["id"],
                                notes="模拟数据；数据比特" if device["role"] == "data" else
                                      "模拟数据；稳定子测量比特")
            _fill_qubit(store, device, index)

        couplers = sorted(store.devices("coupler"), key=lambda d: (d["y"], d["x"]))
        for index, device in enumerate(couplers):
            store.update_device(device["id"], notes="模拟数据；可调耦合器")
            _fill_coupler(store, device, index)

        for index, link in enumerate(store.links()):
            g_qc = 67.5 + 2.35 * ((index * 7) % 13)
            if index == 0:
                store.set_parameter(link["id"], "link.g_qc", round(g_qc - 1.5, 4),
                                    observed_at=OLD_TIME, source=SOURCE,
                                    notes="演示：旧值")
            _record(store, link["id"], "link.g_qc", g_qc)
            _record(store, link["id"], "link.c_design", 2.35 + 0.12 * (index % 9),
                    design=True)
            _record(store, link["id"], "link.g_qc", g_qc * 0.92, point="idle")

        for index, coupling in enumerate(store.couplings()):
            ident = coupling["id"]
            _record(store, ident, "coupling.g_direct", 0.55 + 0.07 * (index % 8),
                    note="两比特之间的模拟残余直接电容耦合")
            _record(store, ident, "coupling.g_effective", 7.6 + 0.65 * ((index * 5) % 9))
            _record(store, ident, "coupling.c_design", 0.48 + 0.04 * (index % 7),
                    design=True)
            _record(store, ident, "coupling.g_effective", 0.15 + 0.03 * (index % 7),
                    point="idle", note="模拟 idle 点有效耦合")

        issues = store.validate()
        if issues:
            raise RuntimeError("; ".join(issues))
        export_xlsx(store, path.with_suffix(".xlsx"), include_history=True)
        app = QApplication.instance() or QApplication([])
        for suffix, field in (("topology", ""), ("qubit_f01", "qubit.f01"),
                              ("coupler_f01", "coupler.f01"), ("link_g_qc", "link.g_qc")):
            export_diagram(store, path.with_name(path.stem + f"_{suffix}.png"), field, width=1800)
        export_diagram(store, path.with_name(path.stem + "_topology.svg"), width=1800)
        app.processEvents()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--replace", action="store_true", help="replace an existing generated example")
    args = parser.parse_args()
    path = args.path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.replace:
        path.unlink(missing_ok=True)
    build(path)
    print(path)


if __name__ == "__main__":
    main()

"""Command-line entry point. Without a subcommand, launch the desktop app."""

from __future__ import annotations

import argparse
import sys

from .storage import ChipStore
from .templates import rotated_surface_code, square_grid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m qubit_sequencer")
    sub = parser.add_subparsers(dest="command")
    gui = sub.add_parser("gui", help="launch the desktop interface")
    gui.add_argument("path", nargs="?", help="existing .qchip file")
    validate = sub.add_parser("validate", help="check a .qchip file")
    validate.add_argument("path")
    migrate_cmd = sub.add_parser("migrate", help="migrate an older .qchip file when a migration is available")
    migrate_cmd.add_argument("path")
    sample = sub.add_parser("sample", help="create a sample .qchip file")
    sample.add_argument("path")
    sample.add_argument("--template", choices=["square", "surface"], default="surface")
    sample.add_argument("--mode", choices=["none", "direct", "coupler"], default="coupler")
    args = parser.parse_args(argv)
    if args.command in (None, "gui"):
        from .gui import run_gui
        return run_gui(args.path if args.command == "gui" else None)
    if args.command == "validate":
        with ChipStore.open(args.path) as store:
            issues = store.validate()
        if issues:
            print("\n".join(issues), file=sys.stderr)
            return 1
        print("OK")
        return 0
    if args.command == "migrate":
        from .migrations import migrate
        backup = migrate(args.path)
        print(f"Migrated; backup: {backup}" if backup else "Already current")
        return 0
    if args.command == "sample":
        with ChipStore.create(args.path, "示例芯片", args.template) as store:
            if args.template == "square":
                square_grid(store, 3, 3, args.mode)
            else:
                rotated_surface_code(store, 3, args.mode)
            qubits = store.devices("qubit")
            for index, qubit in enumerate(qubits[:8]):
                store.set_parameter(qubit["id"], "qubit.f01", round(4.85 + index * 0.07, 3),
                                    point="idle", source="示例数据")
                store.set_parameter(qubit["id"], "qubit.t1", round(24 + index * 2.3, 1),
                                    point="idle", source="示例数据")
            if store.devices("coupler"):
                coupler = store.devices("coupler")[0]
                store.set_parameter(coupler["id"], "coupler.f01", 6.2, point="idle", source="示例数据")
                store.set_parameter(coupler["id"], "coupler.ej_over_h", 16.8, source="示例设计")
            if store.couplings():
                edge = store.couplings()[0]
                key = "coupling.g_direct" if edge["mode"] == "direct" else "coupling.g_effective"
                store.set_parameter(edge["id"], key, 10.8, point="idle", source="示例数据")
            store.add_line("XY", "DR-A", "P12", "AWG4", devices=[qubits[0]["id"]])
        print(args.path)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

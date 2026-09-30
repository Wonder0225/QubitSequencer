"""Windows desktop executable entry point."""

from sys import argv

from qubit_sequencer.gui import run_gui


if __name__ == "__main__":
    raise SystemExit(run_gui(argv[1] if len(argv) > 1 else None))

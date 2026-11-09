"""Deterministic logical-grid templates."""

from __future__ import annotations

from .storage import ChipStore


def _join(store: ChipStore, a: str, b: str, mode: str) -> None:
    if mode == "none":
        return
    if mode == "direct":
        store.add_coupling(a, b, "direct")
        return
    if mode != "coupler":
        raise ValueError("mode must be none, direct or coupler")
    qa, qb = store.device(a), store.device(b)
    cx, cy = (qa["x"] + qb["x"]) / 2, (qa["y"] + qb["y"]) / 2
    mediator = store.add_device("coupler", "tunable_coupler", None, cx, cy)
    store.add_coupling(a, b, "coupler", mediator)


def square_grid(store: ChipStore, rows: int, columns: int,
                mode: str = "coupler", subtype: str = "tunable_transmon") -> None:
    if rows < 1 or columns < 1:
        raise ValueError("rows and columns must be positive")
    if store.devices():
        raise ValueError("template requires an empty chip")
    if mode not in ("none", "direct", "coupler"):
        raise ValueError("invalid coupling mode")
    store.set_meta("grid_rows", str(rows))
    store.set_meta("grid_columns", str(columns))
    qubits = {}
    for r in range(rows):
        for c in range(columns):
            qubits[r, c] = store.add_device("qubit", subtype, None, c, r)
    for r in range(rows):
        for c in range(columns):
            for dr, dc in ((1, 0), (0, 1)):
                if (r + dr, c + dc) in qubits:
                    _join(store, qubits[r, c], qubits[r + dr, c + dc], mode)


def rotated_surface_code(store: ChipStore, distance: int,
                         mode: str = "coupler", subtype: str = "tunable_transmon") -> None:
    """Generate a rotated planar code with d² data and d²-1 measure qubits.

    Interior checks are four-body. Alternating half-plaquettes on the four
    boundaries give two-body checks. Data qubits form the square array shown
    in standard rotated-code diagrams; check qubits occupy plaquette centers
    and boundary half-plaquettes.
    """
    if distance < 3 or distance > 31 or distance % 2 != 1:
        raise ValueError("distance must be odd and between 3 and 31")
    if store.devices():
        raise ValueError("template requires an empty chip")
    if mode not in ("none", "direct", "coupler"):
        raise ValueError("invalid coupling mode")
    store.set_meta("distance", str(distance))

    def location(r: float, c: float) -> tuple[float, float]:
        return (2 * c, 2 * r)

    data = {}
    for r in range(distance):
        for c in range(distance):
            x, y = location(r, c)
            data[r, c] = store.add_device("qubit", subtype, None, x, y, "data")

    def check(r: float, c: float, role: str, neighbors: list[tuple[int, int]]) -> None:
        x, y = location(r, c)
        ancilla = store.add_device("qubit", subtype, None, x, y, role)
        for qr, qc in neighbors:
            _join(store, ancilla, data[qr, qc], mode)

    for r in range(distance - 1):
        for c in range(distance - 1):
            role = "measure_x" if (r + c) % 2 == 0 else "measure_z"
            check(r + 0.5, c + 0.5, role,
                  [(r, c), (r + 1, c), (r, c + 1), (r + 1, c + 1)])
    for c in range(0, distance - 1, 2):
        check(c + 0.5, -0.5, "measure_z", [(c, 0), (c + 1, 0)])
        check(distance - 0.5, c + 0.5, "measure_x",
              [(distance - 1, c), (distance - 1, c + 1)])
    for r in range(1, distance - 1, 2):
        check(-0.5, r + 0.5, "measure_x", [(0, r), (0, r + 1)])
        check(r + 0.5, distance - 0.5, "measure_z",
              [(r, distance - 1), (r + 1, distance - 1)])

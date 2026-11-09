"""Built-in plate layouts and the two supported port lattices."""

from math import floor, hypot, sqrt


TRIANGLE_HEIGHT = sqrt(3) / 2
GRID_NAMES = {"square": "正方格子", "triangle": "三角格子"}
TEMPLATE_NAMES = {"A": "A 盘", "B": "B 盘", "C": "C 盘",
                  "D": "D 盘", "E": "E 盘", "F": "F 盘",
                  "empty_square": "空白正方格子", "empty_triangle": "空白三角格子"}


def template_grid(template):
    return "triangle" if template in ("A", "B", "empty_triangle") else "square"


def grid_height(grid):
    return TRIANGLE_HEIGHT if grid == "triangle" else 1.0


def snap_to_grid(x, y, grid):
    """Return the nearest lattice point in logical coordinates."""
    if grid == "square":
        return float(floor(x + 0.5)), float(floor(y + 0.5))
    center = floor(y / TRIANGLE_HEIGHT + 0.5)
    candidates = []
    for row in (center - 1, center, center + 1):
        offset = 0.5 if row % 2 else 0.0
        col = floor(x - offset + 0.5 - 1e-10) + offset
        row_y = row * TRIANGLE_HEIGHT
        candidates.append((hypot(x - col, y - row_y), col, row_y))
    _, col, row = min(candidates)
    return float(col), row


def nearest_free_point(x, y, grid, occupied):
    point = snap_to_grid(x, y, grid)
    if point not in occupied:
        return point
    height = grid_height(grid)
    center_row, center_col = floor(y / height + 0.5), floor(x + 0.5)
    best = None
    radius = 1
    while True:
        for row in range(center_row - radius, center_row + radius + 1):
            offset = 0.5 if grid == "triangle" and row % 2 else 0.0
            for col in range(center_col - radius, center_col + radius + 1):
                candidate = (float(col + offset), row * height)
                if candidate not in occupied:
                    option = (hypot(x - candidate[0], y - candidate[1]), candidate)
                    if best is None or option < best:
                        best = option
        if best and best[0] <= max(0, radius - 1) * min(1.0, height):
            return best[1]
        radius += 1


def template_ports(template):
    """Return (number, x, y, kind) without sharing any assignments."""
    if template in ("empty_square", "empty_triangle"):
        return []
    if template in ("D", "E", "F"):
        return [(f"O{i + 1}", float(i % 2), float(i // 2), "读取输出") for i in range(4)]
    lengths = [3, 6, 7, 8, 7, 8, 7, 6, 2] if template in ("A", "B") else [3, 7, 7, 9, 9, 9, 7, 7, 3]
    ports = []
    for row, count in enumerate(lengths):
        # The last A/B row has a third, unused position in the reference layout.
        slots = 3 if template in ("A", "B") and row == 8 else count
        for col in range(count):
            number = len(ports) + 1
            kind = "XY/Z"
            if template == "A" and number <= 12:
                kind = "读取输入"
            elif (template == "A" and number >= 42 or
                  template == "B" and (40 <= number <= 43 or number >= 47)):
                kind = "直流"
            ports.append((str(number), col - (slots - 1) / 2, row * grid_height(template_grid(template)), kind))
    return ports

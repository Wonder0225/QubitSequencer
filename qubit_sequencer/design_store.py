"""Embedded chip layout image and device anchors in original image coordinates."""

import math

from .attachment_store import safe_file_name
from .undo import undoable


DESIGN_STATEMENTS = (
    """CREATE TABLE chip_design (
        id INTEGER PRIMARY KEY CHECK(id=1), file_name TEXT NOT NULL,
        data BLOB NOT NULL, rotation REAL NOT NULL DEFAULT 0)""",
    """CREATE TABLE design_markers (
        device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
        design_id INTEGER NOT NULL DEFAULT 1 REFERENCES chip_design(id) ON DELETE CASCADE,
        x REAL NOT NULL CHECK(x>=0 AND x<=1), y REAL NOT NULL CHECK(y>=0 AND y<=1))""",
)
DESIGN_SCHEMA = ";\n".join(DESIGN_STATEMENTS) + ";\n"


class DesignStore:
    def chip_design(self):
        return self.db.execute("SELECT * FROM chip_design WHERE id=1").fetchone()

    @undoable("导入或替换设计图")
    def set_chip_design(self, file_name: str, data: bytes):
        safe_file_name(file_name)
        if not data:
            raise ValueError("设计图内容不能为空")
        with self.db:
            # Replacing a layout invalidates its old anchors and orientation.
            self.db.execute("DELETE FROM chip_design")
            self.db.execute("INSERT INTO chip_design(id,file_name,data) VALUES (1,?,?)",
                            (file_name, data))

    @undoable("移除设计图")
    def remove_chip_design(self):
        with self.db:
            self.db.execute("DELETE FROM chip_design")

    @undoable("旋转设计图")
    def set_design_rotation(self, angle: float):
        if not math.isfinite(angle):
            raise ValueError("旋转角度必须为有限数值")
        with self.db:
            self.db.execute("UPDATE chip_design SET rotation=? WHERE id=1", (angle % 360,))

    def design_markers(self):
        return self.db.execute(
            """SELECT m.device_id,m.x,m.y,d.label,d.kind,d.subtype FROM design_markers m
               JOIN devices d ON d.id=m.device_id ORDER BY d.label"""
        ).fetchall()

    @undoable("放置或移动设计图标注")
    def set_design_marker(self, device_id: str, x: float, y: float):
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("标注位置必须在设计图内")
        with self.db:
            self.db.execute(
                """INSERT INTO design_markers(device_id,x,y) VALUES (?,?,?)
                   ON CONFLICT(device_id) DO UPDATE SET x=excluded.x,y=excluded.y""",
                (device_id, x, y),
            )

    @undoable("移除设计图标注")
    def remove_design_marker(self, device_id: str):
        with self.db:
            self.db.execute("DELETE FROM design_markers WHERE device_id=?", (device_id,))

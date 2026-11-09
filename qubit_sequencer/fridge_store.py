"""Independent cryostat projects and portable sample bundles."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook

from .undo import undoable
from .fridge_templates import TEMPLATE_NAMES, nearest_free_point, snap_to_grid, template_grid, template_ports
from .fridge_presets import preset_for


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE samples (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL);
CREATE TABLE ports (id TEXT PRIMARY KEY, board TEXT NOT NULL, number TEXT NOT NULL,
 x REAL NOT NULL, y REAL NOT NULL, kind TEXT NOT NULL, UNIQUE(board, number));
CREATE TABLE assignments (port_id TEXT PRIMARY KEY REFERENCES ports(id) ON DELETE CASCADE,
 sample_id TEXT NOT NULL REFERENCES samples(id), usage TEXT NOT NULL, pin TEXT NOT NULL, drawing TEXT NOT NULL,
 devices_json TEXT NOT NULL, notes TEXT NOT NULL, legacy_json TEXT NOT NULL);
CREATE TABLE port_circuits (port_id TEXT PRIMARY KEY REFERENCES ports(id) ON DELETE CASCADE,
 circuit_json TEXT NOT NULL);
CREATE TABLE branch_assignments (port_id TEXT NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
 branch TEXT NOT NULL, sample_id TEXT NOT NULL REFERENCES samples(id), usage TEXT NOT NULL,
 pin TEXT NOT NULL, drawing TEXT NOT NULL, devices_json TEXT NOT NULL, notes TEXT NOT NULL,
 legacy_json TEXT NOT NULL, PRIMARY KEY(port_id,branch));
"""


def display_line_type(value):
    return {"读取输入": "读入", "读取输出": "读出"}.get(value, value)


def stored_line_type(value):
    return {"读入": "读取输入", "读出": "读取输出"}.get(value, value)


def ident():
    return uuid4().hex


def _read_chip(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if db.execute("SELECT value FROM meta WHERE key='format'").fetchone()[0] != "qchip":
            raise ValueError("不是 .qchip 样品文件")
    except Exception:
        db.close()
        raise
    return db


def _relative(path, base):
    try:
        return os.path.relpath(Path(path).resolve(), base)
    except ValueError:  # Windows: paths on different drives
        return str(Path(path).resolve())


def default_ports():
    return [(ident(), board, number, x, y, kind)
            for board in "ABCDEF" for number, x, y, kind in template_ports(board)]


class FridgeStore:
    def __init__(self, path, db):
        self.path = Path(path).resolve()
        self.db = db
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.undo_history = None

    @classmethod
    def preview(cls):
        """Transient default layout for browsing before a project is opened."""
        db = sqlite3.connect(":memory:")
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO meta VALUES (?,?)", [("format", "qfridge"), ("name", "默认盘位预览")])
        db.executemany("INSERT INTO ports VALUES (?,?,?,?,?,?)", default_ports())
        db.commit()
        return cls(Path("default-preview.qfridge"), db)

    @classmethod
    def create(cls, path, name, with_preset=False):
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        db = sqlite3.connect(path)
        try:
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO meta VALUES (?,?)", [("format", "qfridge"), ("name", name)])
            db.execute("PRAGMA user_version=2")
            db.executemany("INSERT INTO ports VALUES (?,?,?,?,?,?)", default_ports())
            if with_preset:
                for port_id, board, number in db.execute("SELECT id,board,number FROM ports").fetchall():
                    circuit = preset_for(board, number)
                    if circuit is not None:
                        db.execute("INSERT INTO port_circuits VALUES (?,?)", (port_id, json.dumps(circuit, ensure_ascii=False)))
            db.commit()
            return cls(path, db)
        except Exception:
            db.close()
            path.unlink(missing_ok=True)
            raise

    @classmethod
    def open(cls, path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        db = sqlite3.connect(path)
        try:
            marker = db.execute("SELECT value FROM meta WHERE key='format'").fetchone()
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if not marker or marker[0] != "qfridge" or version not in (1, 2):
                raise ValueError("不支持的 .qfridge 文件")
            if version == 1:
                backup = path.with_name(f"{path.stem}.pre-v1.qfridge")
                if backup.exists():
                    raise FileExistsError(backup)
                with closing(sqlite3.connect(backup)) as target:
                    db.backup(target)
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("CREATE TABLE port_circuits (port_id TEXT PRIMARY KEY REFERENCES ports(id) ON DELETE CASCADE, circuit_json TEXT NOT NULL)")
                    db.execute("""CREATE TABLE branch_assignments (port_id TEXT NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
                        branch TEXT NOT NULL, sample_id TEXT NOT NULL REFERENCES samples(id), usage TEXT NOT NULL,
                        pin TEXT NOT NULL, drawing TEXT NOT NULL, devices_json TEXT NOT NULL, notes TEXT NOT NULL,
                        legacy_json TEXT NOT NULL, PRIMARY KEY(port_id,branch))""")
                    db.execute("PRAGMA user_version=2")
            store = cls(path, db)
            store.align_existing()
            return store
        except Exception:
            db.close()
            raise

    def close(self):
        self.db.close()

    def name(self):
        return self.db.execute("SELECT value FROM meta WHERE key='name'").fetchone()[0]

    def samples(self):
        return self.db.execute("SELECT * FROM samples ORDER BY name,id").fetchall()

    def sample(self, sample_id):
        return self.db.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()

    def sample_path(self, sample_id):
        return (self.path.parent / self.sample(sample_id)["path"]).resolve()

    def sample_devices(self, sample_id):
        with closing(_read_chip(self.sample_path(sample_id))) as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(devices)")}
            drawing = "drawing_number" if "drawing_number" in columns else "'' AS drawing_number"
            return [dict(row) for row in db.execute(f"SELECT id,label,kind,{drawing} FROM devices ORDER BY label")]

    def resolve_devices(self, sample_id, saved):
        try:
            current = {d["id"]: d for d in self.sample_devices(sample_id)}
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return [dict(d, drawing_number="", status="样品不可用") for d in saved]
        return [dict(current[d["id"]], status="") if d["id"] in current else
                dict(d, drawing_number="", status="器件已删除") for d in saved]

    @undoable("关联样品")
    def add_sample(self, path):
        path = Path(path).resolve()
        if any(self.sample_path(row["id"]) == path for row in self.samples()):
            raise ValueError("该样品文件已关联")
        with closing(_read_chip(path)) as db:
            name = db.execute("SELECT value FROM meta WHERE key='chip_name'").fetchone()[0]
        sample_id = ident()
        with self.db:
            self.db.execute("INSERT INTO samples VALUES (?,?,?)", (sample_id, name, _relative(path, self.path.parent)))
        return sample_id

    @undoable("重新定位样品")
    def relocate_sample(self, sample_id, path):
        with closing(_read_chip(path)) as db:
            ids = {r[0] for r in db.execute("SELECT id FROM devices")}
        used = {d["id"] for row in self.db.execute("SELECT devices_json FROM assignments WHERE sample_id=? UNION ALL SELECT devices_json FROM branch_assignments WHERE sample_id=?", (sample_id, sample_id))
                for d in json.loads(row[0])}
        if not used <= ids:
            raise ValueError("所选样品不包含已关联的器件，请选择原样品文件")
        if any(r["id"] != sample_id and self.sample_path(r["id"]) == Path(path).resolve() for r in self.samples()):
            raise ValueError("该样品文件已被其他样品使用")
        with self.db:
            self.db.execute("UPDATE samples SET path=? WHERE id=?", (_relative(path, self.path.parent), sample_id))

    @undoable("移除样品")
    def remove_sample(self, sample_id):
        with self.db:
            self.db.execute("DELETE FROM assignments WHERE sample_id=?", (sample_id,))
            self.db.execute("DELETE FROM branch_assignments WHERE sample_id=?", (sample_id,))
            self.db.execute("DELETE FROM samples WHERE id=?", (sample_id,))

    def ports(self):
        return self.db.execute("""SELECT p.*, a.sample_id AS direct_sample_id,
          COALESCE(a.sample_id,(SELECT b.sample_id FROM branch_assignments b WHERE b.port_id=p.id LIMIT 1)) AS sample_id,
          a.usage, a.pin, a.drawing, a.devices_json, a.notes, a.legacy_json,
          COALESCE(s.name,(SELECT s2.name FROM branch_assignments b JOIN samples s2 ON s2.id=b.sample_id WHERE b.port_id=p.id LIMIT 1)) AS sample_name,
          (SELECT COUNT(*) FROM branch_assignments b WHERE b.port_id=p.id) AS branch_count
          FROM ports p LEFT JOIN assignments a ON p.id=a.port_id
          LEFT JOIN samples s ON s.id=a.sample_id ORDER BY p.board,p.y,p.x""").fetchall()

    def port(self, port_id):
        return next(row for row in self.ports() if row["id"] == port_id)

    def circuit(self, port_id):
        row = self.db.execute("SELECT circuit_json FROM port_circuits WHERE port_id=?", (port_id,)).fetchone()
        return json.loads(row[0]) if row else {"components": [], "switch": []}

    def branch_assignments(self, port_id):
        return self.db.execute("""SELECT b.*,s.name AS sample_name FROM branch_assignments b
            JOIN samples s ON s.id=b.sample_id WHERE port_id=? ORDER BY branch""", (port_id,)).fetchall()

    def _set_circuit(self, port_id, circuit, transfer_branch=None):
        self.port(port_id)
        components = circuit.get("components", [])
        branches = [str(b).strip() for b in circuit.get("switch", [])]
        if any(not b for b in branches) or len(set(branches)) != len(branches):
            raise ValueError("开关分支名称不能为空或重复")
        used = {r["branch"] for r in self.branch_assignments(port_id)}
        if not used.issubset(branches):
            raise ValueError("已有分配的开关分支不能移除，请先解除分配")
        direct = self.db.execute("SELECT * FROM assignments WHERE port_id=?", (port_id,)).fetchone()
        if direct and branches:
            if transfer_branch not in branches:
                raise ValueError("该端口已有样品，请选择其所属开关分支")
            self.db.execute("INSERT INTO branch_assignments VALUES (?,?,?,?,?,?,?,?,?)",
                            (port_id, transfer_branch, direct["sample_id"], direct["usage"], direct["pin"],
                             direct["drawing"], direct["devices_json"], direct["notes"], direct["legacy_json"]))
            self.db.execute("DELETE FROM assignments WHERE port_id=?", (port_id,))
        data = dict(circuit, components=components, switch=branches)
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
        self.db.execute("INSERT INTO port_circuits VALUES (?,?) ON CONFLICT(port_id) DO UPDATE SET circuit_json=excluded.circuit_json",
                        (port_id, encoded))

    @undoable("编辑低温线路")
    def save_circuit(self, port_id, circuit, transfer_branch=None):
        with self.db:
            self._set_circuit(port_id, circuit, transfer_branch)

    @undoable("应用低温线路预设")
    def apply_circuits(self, selections, transfers=None):
        transfers = transfers or {}
        with self.db:
            for port_id, circuit in selections.items():
                self._set_circuit(port_id, circuit, transfers.get(port_id))

    def preset_preview(self):
        result = []
        for p in self.ports():
            template = self.db.execute("SELECT value FROM meta WHERE key=?", (f"board_template:{p['board']}",)).fetchone()
            desired = preset_for(template[0] if template else p["board"], p["number"])
            if desired is not None:
                existing = self.circuit(p["id"])
                result.append({"port": p, "preset": desired, "existing": existing,
                               "conflict": bool(existing.get("components") or existing.get("switch") or existing.get("legacy_details"))})
        return result

    def boards(self):
        names = {r[0] for r in self.db.execute("SELECT DISTINCT board FROM ports")}
        names.update(r[0][len("board_grid:"):] for r in self.db.execute(
            "SELECT key FROM meta WHERE key GLOB 'board_grid:*'"))
        return sorted(names)

    def board_grid(self, board):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (f"board_grid:{board}",)).fetchone()
        return row[0] if row else ("triangle" if board in ("A", "B") else "square")

    def set_board_grid(self, board, grid):
        if grid not in ("square", "triangle"):
            raise ValueError("不支持的格子类型")
        if self.board_grid(board) == grid:
            return
        with self.db:
            self.db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (f"board_grid:{board}", grid))
            self._write_aligned_positions(self._alignment_plan(board, grid))
        if self.undo_history:
            self.undo_history.clear()

    @undoable("新建盘")
    def create_board(self, board, template, with_preset=False):
        board = board.strip()
        if not board or board in self.boards():
            raise ValueError("盘号不能为空或与现有盘重复")
        if template not in TEMPLATE_NAMES:
            raise ValueError("未知盘位模板")
        with self.db:
            self.db.execute("INSERT INTO meta VALUES (?,?)", (f"board_grid:{board}", template_grid(template)))
            self.db.execute("INSERT INTO meta VALUES (?,?)", (f"board_template:{board}", template))
            for number, x, y, kind in template_ports(template):
                port_id = ident()
                self.db.execute("INSERT INTO ports VALUES (?,?,?,?,?,?)", (port_id, board, number, x, y, kind))
                circuit = preset_for(template, number) if with_preset else None
                if circuit is not None:
                    self.db.execute("INSERT INTO port_circuits VALUES (?,?)", (port_id, json.dumps(circuit, ensure_ascii=False)))

    def next_port_number(self, board):
        numbers = [r[0] for r in self.db.execute("SELECT number FROM ports WHERE board=?", (board,))]
        prefix = "O" if numbers and all(re.fullmatch(r"O\d+", number) for number in numbers) else ""
        used = set(numbers); number = 1
        while f"{prefix}{number}" in used:
            number += 1
        return f"{prefix}{number}"

    def _alignment_plan(self, board, grid):
        ports = self.db.execute("SELECT id,number,x,y FROM ports WHERE board=?", (board,)).fetchall()
        def priority(port):
            x, y = snap_to_grid(port["x"], port["y"], grid)
            return (math.hypot(x - port["x"], y - port["y"]), port["y"], port["x"], port["number"])
        occupied = set(); updates = []
        for port in sorted(ports, key=priority):
            x, y = nearest_free_point(port["x"], port["y"], grid, occupied)
            occupied.add((x, y))
            if (x, y) != (port["x"], port["y"]):
                updates.append((x, y, port["id"]))
        return updates

    def _write_aligned_positions(self, updates):
        self.db.executemany("UPDATE ports SET x=?,y=? WHERE id=?", updates)

    def align_existing(self):
        updates = [update for board in self.boards() for update in self._alignment_plan(board, self.board_grid(board))]
        if not updates:
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = self.path.with_name(f"{self.path.stem}.pre-grid-{stamp}-{ident()[:8]}.qfridge")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            updates = [update for board in self.boards() for update in self._alignment_plan(board, self.board_grid(board))]
            if not updates:
                return None
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
            self._write_aligned_positions(updates)
        return backup

    @undoable("编辑端口")
    def save_port(self, port_id, board, number, x, y, kind):
        if not board.strip() or not number.strip():
            raise ValueError("盘号和端口编号不能为空")
        board, number = board.strip(), number.strip()
        current = self.db.execute("SELECT board,x,y FROM ports WHERE id=?", (port_id,)).fetchone() if port_id else None
        moving = not current or (board, x, y) != (current["board"], current["x"], current["y"])
        if moving:
            grid = self.board_grid(board)
            x, y = snap_to_grid(x, y, grid)
            for other in self.db.execute("SELECT id,x,y FROM ports WHERE board=?", (board,)):
                if other["id"] != port_id and math.hypot(x - other["x"], y - other["y"]) < 0.8:
                    raise ValueError("该格点附近已有端口，请选择空位")
        with self.db:
            if port_id:
                self.db.execute("UPDATE ports SET board=?,number=?,x=?,y=?,kind=? WHERE id=?",
                                (board, number, x, y, kind, port_id))
            else:
                port_id = ident()
                self.db.execute("INSERT INTO ports VALUES (?,?,?,?,?,?)", (port_id, board, number, x, y, kind))
        return port_id

    @undoable("删除端口")
    def delete_port(self, port_id):
        with self.db:
            self.db.execute("DELETE FROM ports WHERE id=?", (port_id,))

    @undoable("分配端口")
    def assign(self, port_id, sample_id, pin="", drawing="", devices=None, notes="", usage=None, *, branch=None):
        row = self.port(port_id)
        branches = self.circuit(port_id).get("switch", [])
        if branches and branch not in branches or not branches and branch is not None:
            raise ValueError("请选择有效的开关分支")
        previous = (self.db.execute("SELECT * FROM branch_assignments WHERE port_id=? AND branch=?", (port_id, branch)).fetchone()
                    if branch is not None else self.db.execute("SELECT * FROM assignments WHERE port_id=?", (port_id,)).fetchone())
        if previous and previous["sample_id"] != sample_id:
            raise ValueError("端口已分配给其他样品，请先解除分配")
        available = {d["id"]: d for d in self.sample_devices(sample_id)}
        if previous:
            for device in json.loads(previous["devices_json"]):
                available.setdefault(device["id"], device)
        ids = devices or []
        if any(i not in available for i in ids):
            raise ValueError("关联器件不属于该样品")
        legacy = previous["legacy_json"] if previous else "{}"
        with self.db:
            if branch is not None:
                self.db.execute("""INSERT INTO branch_assignments VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(port_id,branch) DO UPDATE SET sample_id=excluded.sample_id,usage=excluded.usage,
                    pin=excluded.pin,drawing=excluded.drawing,devices_json=excluded.devices_json,notes=excluded.notes""",
                    (port_id, branch, sample_id, usage or row["kind"], pin, drawing,
                     json.dumps([available[i] for i in ids], ensure_ascii=False), notes, legacy))
            else:
                self.db.execute("""INSERT INTO assignments VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(port_id) DO UPDATE SET sample_id=excluded.sample_id,usage=excluded.usage,pin=excluded.pin,
                drawing=excluded.drawing,devices_json=excluded.devices_json,notes=excluded.notes""",
                (port_id, sample_id, usage or row["kind"], pin, drawing, json.dumps([available[i] for i in ids], ensure_ascii=False), notes, legacy))

    @undoable("解除分配")
    def unassign(self, port_id, branch=None):
        with self.db:
            if branch is None:
                self.db.execute("DELETE FROM assignments WHERE port_id=?", (port_id,))
            else:
                self.db.execute("DELETE FROM branch_assignments WHERE port_id=? AND branch=?", (port_id, branch))

    def migration_preview(self, sample_id):
        ports = {(p["board"].upper(), p["number"].upper()): p for p in self.ports()}
        result = []
        with closing(_read_chip(self.sample_path(sample_id))) as db:
            has_details = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wiring_details'").fetchone())
            for line in db.execute("SELECT * FROM wiring_lines ORDER BY port,id"):
                details_row = db.execute("SELECT details_json FROM wiring_details WHERE line_id=?", (line["id"],)).fetchone() if has_details else None
                details = json.loads(details_row[0]) if details_row else {}
                board = str(details.get("board", "")).strip().upper().removesuffix("盘")
                number = str(line["port"]).strip().upper()
                match = re.fullmatch(r"([A-F])\s*[-:]?\s*(O?\d+)", number)
                if match:
                    mismatch = bool(board and board != match.group(1))
                    board, number = board or match.group(1), match.group(2)
                else:
                    mismatch = False
                port = None if mismatch else ports.get((board, number))
                branches = self.circuit(port["id"]).get("switch", []) if port else []
                free = set(branches) - {r["branch"] for r in self.branch_assignments(port["id"])} if branches else set()
                reason = ("未匹配盘号／端口" if not port else "请选择开关分支" if free else
                          "端口已占用" if port["sample_id"] or branches else "可迁移")
                devices = [dict(r) for r in db.execute("""SELECT d.id,d.label,d.kind FROM devices d
                    JOIN wiring_assignments a ON a.device_id=d.id WHERE a.line_id=?""", (line["id"],))]
                result.append({"line": dict(line), "details": details, "devices": devices,
                               "port_id": port["id"] if port else None, "reason": reason})
        counts = {}
        for item in result:
            if item["port_id"]:
                counts[item["port_id"]] = counts.get(item["port_id"], 0) + 1
        for item in result:
            if item["port_id"] and counts[item["port_id"]] > 1 and item["reason"] == "可迁移":
                item["reason"] = "多条线路匹配同一端口，请选择其中一条"
        return result

    def migrate_lines(self, sample_id, line_ports):
        selected = [item for item in self.migration_preview(sample_id) if item["line"]["id"] in line_ports]
        if len(selected) != len(line_ports):
            raise ValueError("选择中包含无法迁移的线路")
        for item in selected:
            target = line_ports[item["line"]["id"]]
            item["port_id"], item["branch"] = target if isinstance(target, (tuple, list)) else (target, None)
            branches = self.circuit(item["port_id"]).get("switch", [])
            if branches and item["branch"] not in branches or not branches and item["branch"] is not None:
                raise ValueError("请选择有效的开关分支")
            if (item["branch"] is None and self.port(item["port_id"])["sample_id"] or
                    item["branch"] is not None and any(r["branch"] == item["branch"] for r in self.branch_assignments(item["port_id"]))):
                raise ValueError("所选端口已占用")
        if len({(i["port_id"], i["branch"]) for i in selected}) != len(selected):
            raise ValueError("同一端口或开关分支只能迁移一条线路")
        if not selected:
            return None
        source = self.sample_path(sample_id)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = source.with_name(f"{source.stem}.pre-fridge-{stamp}.qchip")
        self.db.execute("ATTACH DATABASE ? AS sampledb", (str(source),))
        try:
            if any(self.db.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0].lower() == "wal" for schema in ("main", "sampledb")):
                raise ValueError("迁移要求项目和样品使用 SQLite 回滚日志；请关闭其他程序后重试")
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                with closing(_read_chip(source)) as src, closing(sqlite3.connect(backup)) as dst:
                    src.backup(dst)
                has_details = bool(self.db.execute("SELECT 1 FROM sampledb.sqlite_master WHERE type='table' AND name='wiring_details'").fetchone())
                for item in selected:
                    line, detail = item["line"], item["details"]
                    current = self.db.execute("SELECT * FROM sampledb.wiring_lines WHERE id=?", (line["id"],)).fetchone()
                    if not current or dict(current) != line:
                        raise ValueError("原线路已变化，请重新预览")
                    current_detail = self.db.execute("SELECT details_json FROM sampledb.wiring_details WHERE line_id=?", (line["id"],)).fetchone() if has_details else None
                    if (json.loads(current_detail[0]) if current_detail else {}) != detail:
                        raise ValueError("原线路详情已变化，请重新预览")
                    devices = [dict(r) for r in self.db.execute("""SELECT d.id,d.label,d.kind FROM sampledb.devices d
                        JOIN sampledb.wiring_assignments a ON a.device_id=d.id WHERE a.line_id=? ORDER BY d.id""", (line["id"],))]
                    if sorted(devices, key=lambda d: d["id"]) != sorted(item["devices"], key=lambda d: d["id"]):
                        raise ValueError("原线路关联器件已变化，请重新预览")
                    legacy = json.dumps({"line": line, "details": detail}, ensure_ascii=False)
                    values = (sample_id, line["line_type"], detail.get("sample_pin", ""), detail.get("drawing_label", ""),
                              json.dumps(devices, ensure_ascii=False), line["notes"], legacy)
                    if item["branch"] is None:
                        self.db.execute("INSERT INTO assignments VALUES (?,?,?,?,?,?,?,?)", (item["port_id"], *values))
                    else:
                        self.db.execute("INSERT INTO branch_assignments VALUES (?,?,?,?,?,?,?,?,?)", (item["port_id"], item["branch"], *values))
                    self.db.execute("DELETE FROM sampledb.wiring_lines WHERE id=?", (line["id"],))
        finally:
            self.db.execute("DETACH DATABASE sampledb")
        if self.undo_history:
            self.undo_history.clear()
        return backup

    def table_entries(self):
        headers = ["盘号", "端口", "端口类型", "开关分支", "线路用途", "占用状态", "样品", "样品引脚",
                   "历史图纸编号 / 信号名称", "器件", "比特图纸编号", "低温线路", "备注"]
        entries = []
        for p in self.ports():
            circuit = self.circuit(p["id"])
            branches = circuit.get("switch", [])
            assigned = {r["branch"]: r for r in self.branch_assignments(p["id"])}
            components = "\n".join(" / ".join(str(v) for v in (c.get("stage"), c.get("kind"),
                c.get("value") if c.get("value") is not None else "", c.get("unit"), c.get("mark"),
                c.get("terminal"), c.get("notes")) if v not in (None, ""))
                for c in circuit.get("components", []))
            for branch in branches or [None]:
                row = assigned.get(branch) if branch is not None else p
                sample_id = row["sample_id"] if row else None
                saved = json.loads(row["devices_json"] or "[]") if row else []
                devices = self.resolve_devices(sample_id, saved) if sample_id else []
                numbers = ", ".join(f"{d['label']}: {d.get('drawing_number', '') or '未填写'}"
                    + (f"（{d['status']}）" if d.get("status") else "") for d in devices if d.get("kind") == "qubit")
                values = [p["board"], p["number"], display_line_type(p["kind"]), branch or "",
                          display_line_type(row["usage"] or "") if row else "", "已分配" if sample_id else "空闲",
                          ((row["sample_name"] or "") + ("（样品不可用）" if sample_id and not self.sample_path(sample_id).is_file() else "")) if row else "", row["pin"] or "" if row else "",
                          row["drawing"] or "" if row else "", ", ".join(d["label"] for d in devices), numbers,
                          components, row["notes"] or "" if row else ""]
                entries.append((p["id"], branch, values))
        return headers, entries

    def table(self):
        headers, entries = self.table_entries()
        return headers, [entry[2] for entry in entries]

    def export_table(self, path):
        headers, rows = self.table()
        if Path(path).suffix.lower() == ".csv":
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream); writer.writerow(headers); writer.writerows(rows)
        else:
            book = Workbook(); sheet = book.active; sheet.title = "制冷机接线"
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
            sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions
            book.save(path)

    def export_bundle(self, path):
        samples = [(s, self.sample_path(s["id"])) for s in self.samples()]
        for _, source in samples:
            with closing(_read_chip(source)):
                pass
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fridge_copy = root / "project.qfridge"
            with closing(sqlite3.connect(fridge_copy)) as copy:
                self.db.backup(copy)
                for sample, source in samples:
                    rel = Path("samples") / sample["id"] / source.name
                    target = root / rel; target.parent.mkdir(parents=True)
                    with closing(_read_chip(source)) as src, closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
                    copy.execute("UPDATE samples SET path=? WHERE id=?", (rel.as_posix(), sample["id"]))
                copy.commit()
            staged = root / "bundle.zip"
            with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in root.rglob("*"):
                    if file.is_file() and file != staged:
                        archive.write(file, file.relative_to(root).as_posix())
            # Copy to a sibling first, so an existing destination survives failures.
            import shutil
            target = Path(path).resolve()
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".zip", delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copyfile(staged, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

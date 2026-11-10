"""Sample-box geometry and chip-side port bindings."""

import json
from uuid import uuid4

from .sample_box_templates import DEFAULT_TEMPLATE, builtin_sample_box_template
from .undo import undoable


BOX_ROLES = {"single": "", "read_in": "读入", "read_out": "读出"}

SAMPLE_BOX_STATEMENTS = (
    """CREATE TABLE sample_box_ports (
        id TEXT PRIMARY KEY, number TEXT NOT NULL UNIQUE, x INTEGER NOT NULL, y INTEGER NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('photo','inferred')),
        verified INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1)), signal_name TEXT NOT NULL DEFAULT '',
        UNIQUE(x,y))""",
    """CREATE TABLE sample_box_bindings (
        port_id TEXT PRIMARY KEY REFERENCES sample_box_ports(id) ON DELETE CASCADE,
        device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('single','read_in','read_out')), UNIQUE(device_id,role))""",
)
SAMPLE_BOX_SCHEMA = ";\n".join(SAMPLE_BOX_STATEMENTS) + ";\n"


def sample_box_ports(db):
    return db.execute("""SELECT p.*, b.device_id, b.role, d.label AS device_label,
        d.kind AS device_kind, d.subtype AS device_subtype, d.drawing_number FROM sample_box_ports p
        LEFT JOIN sample_box_bindings b ON b.port_id=p.id
        LEFT JOIN devices d ON d.id=b.device_id ORDER BY p.y,p.x""").fetchall()


def box_number_source(port):
    source = "照片刻字" if port["source"] == "photo" else "对称推断"
    return f"{source}（已核对）" if port["verified"] else source


class SampleBoxStore:
    def sample_box_ports(self):
        return sample_box_ports(self.db)

    def sample_box_template(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='sample_box.template'").fetchone()
        return row[0] if row else None

    def sample_box_appearance(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='sample_box.appearance'").fetchone()
        if row:
            return json.loads(row[0])
        template = builtin_sample_box_template(DEFAULT_TEMPLATE)
        return dict(name=template.name, drawing=template.drawing)

    def sample_box_summary(self, device_id):
        ports = self.db.execute("""SELECT p.number,b.role FROM sample_box_bindings b
            JOIN sample_box_ports p ON p.id=b.port_id WHERE b.device_id=? ORDER BY b.role""", (device_id,)).fetchall()
        values = [f"{p['number']} {BOX_ROLES[p['role']]}".strip() for p in ports]
        return "样品盒接口：" + "、".join(values) if values else ""

    @undoable("应用样品盒模板")
    def enable_sample_box(self, template=DEFAULT_TEMPLATE, *, replace=False):
        if isinstance(template, str):
            template = builtin_sample_box_template(template)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.sample_box_template()
            if current and not replace:
                raise ValueError("当前项目已应用样品盒模板")
            # Use fresh rowids so undo treats a replacement as deleting the old
            # ports and inserting new ones instead of swapping unique coordinates.
            start = self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM sample_box_ports").fetchone()[0] + 1
            if current:
                self.db.execute("DELETE FROM sample_box_ports")
            self.db.executemany("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (
                ("sample_box.template", template.key),
                ("sample_box.appearance", json.dumps(dict(name=template.name, drawing=template.drawing), ensure_ascii=False)),
            ))
            self.db.executemany("INSERT INTO sample_box_ports (rowid,id,number,x,y,source) VALUES (?,?,?,?,?,?)", (
                (rowid, uuid4().hex, *port) for rowid, port in enumerate(template.ports, start)
            ))

    @undoable("保存样品盒接口")
    def save_sample_box_port(self, port_id, number, verified, device_id=None, role="single", signal_name=""):
        number = number.strip()
        if not number:
            raise ValueError("接口编号不能为空")
        if device_id is not None:
            kind = self.device(device_id)["kind"]
            if role not in (("read_in", "read_out") if kind == "readout" else ("single",)):
                raise ValueError("读取线需要指定读入或读出，其他元件只能登记一个接口")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.db.execute("SELECT 1 FROM sample_box_ports WHERE id=?", (port_id,)).fetchone():
                raise ValueError("样品盒接口已不存在")
            if self.db.execute("SELECT 1 FROM sample_box_ports WHERE number=? AND id!=?", (number, port_id)).fetchone():
                raise ValueError(f"接口编号 {number} 已存在")
            if device_id is not None:
                occupied = self.db.execute("""SELECT p.number FROM sample_box_bindings b
                    JOIN sample_box_ports p ON p.id=b.port_id
                    WHERE b.device_id=? AND b.role=? AND b.port_id!=?""", (device_id, role, port_id)).fetchone()
                if occupied:
                    raise ValueError(f"此元件的{BOX_ROLES[role] or '关联'}接口已登记在 {occupied[0]} 号")
                self.db.execute("""INSERT INTO sample_box_bindings VALUES (?,?,?)
                    ON CONFLICT(port_id) DO UPDATE SET device_id=excluded.device_id,role=excluded.role""",
                                (port_id, device_id, role))
            else:
                self.db.execute("DELETE FROM sample_box_bindings WHERE port_id=?", (port_id,))
            self.db.execute("UPDATE sample_box_ports SET number=?,verified=?,signal_name=? WHERE id=?",
                            (number, int(verified), signal_name.strip(), port_id))

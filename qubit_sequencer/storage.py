"""Versioned .qchip persistence and domain operations.

No Qt dependency belongs here: the same repository serves the GUI, exporters and tests.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from .attachment_store import AttachmentStore, AttachmentInput, AttachmentChange, ATTACHMENT_SCHEMA

SCHEMA_VERSION = 3
FORMAT_NAME = "qchip"
BEIJING = timezone(timedelta(hours=8), "UTC+08:00")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def beijing_now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def beijing_timestamp(value: str) -> str:
    """Normalize explicit offsets; offset-free input means Beijing local time."""
    instant = datetime.fromisoformat(value.strip())
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=BEIJING)
    return instant.astimezone(BEIJING).isoformat()


def display_timestamp(value: str) -> str:
    """Read legacy timestamps without rewriting stored history or losing text."""
    try:
        return beijing_timestamp(value)
    except (TypeError, ValueError):
        return value


def new_id() -> str:
    return str(uuid4())


# key, label, owner scope, category, value type, canonical display unit
PRESETS = [
    ("qubit.f01", "比特频率 f_{10}", "qubit", "measured", "number", "GHz"),
    ("qubit.anharmonicity", "非谐", "qubit", "measured", "number", "MHz"),
    ("qubit.readout_frequency", "读取频率", "qubit", "measured", "number", "GHz"),
    ("qubit.readout_power", "读取功率", "qubit", "measured", "number", "dBm"),
    ("qubit.t1", "T_{1}", "qubit", "measured", "number", "µs"),
    ("qubit.t2_star", "Ramsey T_{2}^{*}", "qubit", "measured", "number", "µs"),
    ("qubit.t2_echo", "Echo T_{2}", "qubit", "measured", "number", "µs"),
    ("qubit.design_f01", "设计频率 f_{10}", "qubit", "design", "number", "GHz"),
    ("qubit.ej_over_h", "E_{J}/h", "qubit", "design", "number", "GHz"),
    ("qubit.ec_over_h", "E_{C}/h", "qubit", "design", "number", "GHz"),
    ("qubit.c_sigma", r"总电容 C_{\Sigma}", "qubit", "design", "number", "fF"),
    ("qubit.ic", "结临界电流 I_{c}", "qubit", "design", "number", "nA"),
    ("qubit.junction_asymmetry", "结不对称度", "qubit", "design", "number", ""),
    ("coupler.f01", "耦合器频率 f_{10}", "coupler", "measured", "number", "GHz"),
    ("coupler.anharmonicity", "耦合器非谐", "coupler", "measured", "number", "MHz"),
    ("coupler.t1", "耦合器 T_{1}", "coupler", "measured", "number", "µs"),
    ("coupler.t2_star", "耦合器 Ramsey T_{2}^{*}", "coupler", "measured", "number", "µs"),
    ("coupler.t2_echo", "耦合器 Echo T_{2}", "coupler", "measured", "number", "µs"),
    ("coupler.design_f01", "耦合器设计频率 f_{10}", "coupler", "design", "number", "GHz"),
    ("coupler.ej_over_h", "耦合器 E_{J}/h", "coupler", "design", "number", "GHz"),
    ("coupler.ec_over_h", "耦合器 E_{C}/h", "coupler", "design", "number", "GHz"),
    ("coupler.c_sigma", r"耦合器总电容 C_{\Sigma}", "coupler", "design", "number", "fF"),
    ("coupler.ic", "耦合器结临界电流 I_{c}", "coupler", "design", "number", "nA"),
    ("coupler.junction_asymmetry", "耦合器结不对称度", "coupler", "design", "number", ""),
    ("coupling.g_direct", "直接耦合强度", "coupling", "measured", "number", "MHz"),
    ("coupling.g_effective", "有效比特耦合强度", "coupling", "measured", "number", "MHz"),
    ("coupling.c_design", "设计耦合电容", "coupling", "design", "number", "fF"),
    ("link.g_qc", "比特-耦合器强度", "link", "measured", "number", "MHz"),
    ("link.c_design", "比特-耦合器设计电容", "link", "design", "number", "fF"),
]

# Read old built-in labels through a projection. IDs, keys, values and customized
# labels remain in the original file; no schema or historical-data rewrite.
LEGACY_LABELS = {
    "qubit.f01": "比特频率 f01", "qubit.design_f01": "设计频率 f01",
    "qubit.t1": "T1", "qubit.t2_star": "Ramsey T2*", "qubit.t2_echo": "Echo T2",
    "qubit.ej_over_h": "EJ/h", "qubit.ec_over_h": "EC/h",
    "qubit.c_sigma": "总电容 CΣ", "qubit.ic": "结临界电流 Ic",
    "coupler.f01": "耦合器频率 f01", "coupler.design_f01": "耦合器设计频率",
    "coupler.t1": "耦合器 T1", "coupler.t2_star": "耦合器 Ramsey T2*",
    "coupler.t2_echo": "耦合器 Echo T2", "coupler.ej_over_h": "耦合器 EJ/h",
    "coupler.ec_over_h": "耦合器 EC/h", "coupler.c_sigma": "耦合器总电容 CΣ",
    "coupler.ic": "耦合器结临界电流 Ic",
}
_PRESET_LABELS = {row[0]: row[1] for row in PRESETS}
_LABEL_CASE = "CASE " + " ".join(
    f"WHEN is_custom=0 AND key='{key}' AND label='{old}' THEN '{_PRESET_LABELS[key]}'"
    for key, old in LEGACY_LABELS.items()
) + " ELSE label END AS label"
_DEFINITION_COLUMNS = f"key,{_LABEL_CASE},scope,category,value_type,unit,is_custom"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE devices (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('qubit','coupler')),
 subtype TEXT NOT NULL, label TEXT NOT NULL UNIQUE, role TEXT NOT NULL DEFAULT '',
 x REAL NOT NULL, y REAL NOT NULL, dx REAL NOT NULL DEFAULT 0, dy REAL NOT NULL DEFAULT 0,
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE couplings (
 id TEXT PRIMARY KEY, qubit_a TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 qubit_b TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 mode TEXT NOT NULL CHECK(mode IN ('direct','coupler')),
 coupler_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 CHECK(qubit_a != qubit_b),
 CHECK((mode='direct' AND coupler_id IS NULL) OR (mode='coupler' AND coupler_id IS NOT NULL))
);
CREATE INDEX couplings_a ON couplings(qubit_a);
CREATE INDEX couplings_b ON couplings(qubit_b);
CREATE TABLE links (
 id TEXT PRIMARY KEY, qubit_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 coupler_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(qubit_id,coupler_id)
);
CREATE INDEX links_coupler ON links(coupler_id);
CREATE TABLE wiring_lines (
 id TEXT PRIMARY KEY, line_type TEXT NOT NULL, cryostat TEXT NOT NULL DEFAULT '',
 port TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE wiring_assignments (
 line_id TEXT NOT NULL REFERENCES wiring_lines(id) ON DELETE CASCADE,
 device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 PRIMARY KEY(line_id, device_id)
);
CREATE TABLE parameter_definitions (
 key TEXT PRIMARY KEY, label TEXT NOT NULL, scope TEXT NOT NULL
 CHECK(scope IN ('qubit','coupler','coupling','link')),
 category TEXT NOT NULL CHECK(category IN ('measured','design','custom')),
 value_type TEXT NOT NULL CHECK(value_type IN ('number','text')),
 unit TEXT NOT NULL DEFAULT '', is_custom INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE parameter_records (
 id TEXT PRIMARY KEY, definition_key TEXT NOT NULL REFERENCES parameter_definitions(key),
 device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
 coupling_id TEXT REFERENCES couplings(id) ON DELETE CASCADE,
 link_id TEXT REFERENCES links(id) ON DELETE CASCADE,
 point TEXT NOT NULL DEFAULT 'default', bias_value REAL, bias_unit TEXT NOT NULL DEFAULT '',
 numeric_value REAL, text_value TEXT, observed_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', is_current INTEGER NOT NULL DEFAULT 1,
 CHECK((device_id IS NOT NULL) + (coupling_id IS NOT NULL) + (link_id IS NOT NULL) = 1)
);
CREATE UNIQUE INDEX current_device_parameter ON parameter_records(device_id,definition_key,point)
 WHERE is_current=1 AND device_id IS NOT NULL;
CREATE UNIQUE INDEX current_coupling_parameter ON parameter_records(coupling_id,definition_key,point)
 WHERE is_current=1 AND coupling_id IS NOT NULL;
CREATE UNIQUE INDEX current_link_parameter ON parameter_records(link_id,definition_key,point)
 WHERE is_current=1 AND link_id IS NOT NULL;
CREATE TABLE calibration_records (
 id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
 coupling_id TEXT REFERENCES couplings(id) ON DELETE CASCADE,
 experiment_type TEXT NOT NULL, measured_at TEXT NOT NULL, point TEXT NOT NULL DEFAULT 'default',
 fit_json TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '',
 CHECK((device_id IS NOT NULL) + (coupling_id IS NOT NULL) = 1)
);
CREATE TABLE attachments (
 id TEXT PRIMARY KEY, calibration_id TEXT NOT NULL REFERENCES calibration_records(id) ON DELETE CASCADE,
 file_name TEXT NOT NULL, mime_type TEXT NOT NULL, sha256 TEXT NOT NULL, data BLOB NOT NULL
);
"""


SCHEMA += ATTACHMENT_SCHEMA


class ChipStore(AttachmentStore):
    def __init__(self, path: str | Path, connection: sqlite3.Connection):
        self.path = Path(path)
        self.db = connection
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")

    @classmethod
    def create(cls, path: str | Path, name: str, template: str = "custom") -> "ChipStore":
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.executemany("INSERT INTO meta(key,value) VALUES (?,?)", [
                ("format", FORMAT_NAME), ("chip_name", name), ("template", template),
                ("created_at", beijing_now()),
            ])
            db.executemany(
                "INSERT INTO parameter_definitions VALUES (?,?,?,?,?,?,0)", PRESETS
            )
            db.commit()
            return cls(path, db)
        except Exception:
            db.close()
            path.unlink(missing_ok=True)
            raise

    @classmethod
    def open(cls, path: str | Path) -> "ChipStore":
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        with closing(sqlite3.connect(path)) as probe:
            version = probe.execute("PRAGMA user_version").fetchone()[0]
        if 0 < version < SCHEMA_VERSION:
            from .migrations import migrate
            migrate(path)
        db = sqlite3.connect(path)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ValueError(f"Unsupported .qchip schema version: {version}")
            row = db.execute("SELECT value FROM meta WHERE key='format'").fetchone()
            if row is None or row[0] != FORMAT_NAME:
                raise ValueError("Not a .qchip database")
            return cls(path, db)
        except Exception:
            db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ChipStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.execute("SELECT * FROM meta")}

    def set_name(self, name: str) -> None:
        with self.db:
            self.db.execute("UPDATE meta SET value=? WHERE key='chip_name'", (name.strip(),))

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))

    def add_device(self, kind: str, subtype: str, label: str, x: float, y: float,
                   role: str = "", notes: str = "") -> str:
        if kind not in ("qubit", "coupler"):
            raise ValueError("kind must be qubit or coupler")
        if kind == "qubit" and subtype not in ("fixed_transmon", "tunable_transmon"):
            raise ValueError("invalid qubit subtype")
        if kind == "coupler" and subtype not in ("fixed_coupler", "tunable_coupler"):
            raise ValueError("invalid coupler subtype")
        if not label.strip() or not all(math.isfinite(v) for v in (x, y)):
            raise ValueError("invalid label or position")
        if kind == "qubit" and self.db.execute(
            "SELECT 1 FROM devices WHERE kind='qubit' AND x=? AND y=?", (x, y)
        ).fetchone():
            raise ValueError("qubit position already occupied")
        ident = new_id()
        with self.db:
            self.db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (ident, kind, subtype, label.strip(), role, x, y, 0, 0, notes, beijing_now()))
        return ident

    def update_device(self, ident: str, **changes: object) -> None:
        allowed = {"label", "subtype", "role", "x", "y", "dx", "dy", "notes"}
        if not changes or set(changes) - allowed:
            raise ValueError("invalid device fields")
        old = self.device(ident)
        subtype = changes.get("subtype", old["subtype"])
        if old["kind"] == "qubit" and subtype not in ("fixed_transmon", "tunable_transmon"):
            raise ValueError("invalid qubit subtype")
        if old["kind"] == "coupler" and subtype not in ("fixed_coupler", "tunable_coupler"):
            raise ValueError("invalid coupler subtype")
        if "label" in changes and not str(changes["label"]).strip():
            raise ValueError("label cannot be empty")
        for field in ("x", "y", "dx", "dy"):
            if field in changes and not math.isfinite(float(changes[field])):
                raise ValueError("position must be finite")
        if old["kind"] == "qubit" and ("x" in changes or "y" in changes):
            x = float(changes.get("x", old["x"]))
            y = float(changes.get("y", old["y"]))
            if self.db.execute(
                "SELECT 1 FROM devices WHERE kind='qubit' AND id!=? AND x=? AND y=?", (ident, x, y)
            ).fetchone():
                raise ValueError("qubit position already occupied")
        sql = ", ".join(f"{key}=?" for key in changes)
        with self.db:
            self.db.execute(f"UPDATE devices SET {sql} WHERE id=?", (*changes.values(), ident))

    def device(self, ident: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM devices WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return row

    def devices(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.db.execute("SELECT * FROM devices WHERE kind=? ORDER BY label", (kind,)).fetchall()
        return self.db.execute("SELECT * FROM devices ORDER BY kind,label").fetchall()

    def delete_device(self, ident: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM devices WHERE id=?", (ident,))

    def add_coupling(self, q1: str, q2: str, mode: str = "direct",
                     coupler_id: str | None = None, notes: str = "") -> str:
        if self.device(q1)["kind"] != "qubit" or self.device(q2)["kind"] != "qubit" or q1 == q2:
            raise ValueError("coupling endpoints must be distinct qubits")
        if mode not in ("direct", "coupler") or (mode == "direct") != (coupler_id is None):
            raise ValueError("invalid coupling mode/coupler combination")
        if coupler_id and self.device(coupler_id)["kind"] != "coupler":
            raise ValueError("mediator must be a coupler")
        if coupler_id:
            existing = {r[0] for r in self.db.execute(
                "SELECT qubit_id FROM links WHERE coupler_id=?", (coupler_id,))}
            if len(existing | {q1, q2}) > 2:
                raise ValueError("a coupler can connect at most two qubits")
        q1, q2 = sorted((q1, q2))
        duplicate = self.db.execute(
            "SELECT 1 FROM couplings WHERE qubit_a=? AND qubit_b=? AND mode=? AND coupler_id IS ?",
            (q1, q2, mode, coupler_id),
        ).fetchone()
        if duplicate:
            raise ValueError("coupling already exists")
        ident = new_id()
        with self.db:
            self.db.execute("INSERT INTO couplings VALUES (?,?,?,?,?,?,?)",
                            (ident, q1, q2, mode, coupler_id, notes, beijing_now()))
            if coupler_id:
                self._ensure_link(q1, coupler_id)
                self._ensure_link(q2, coupler_id)
        return ident

    def _ensure_link(self, qubit_id: str, coupler_id: str) -> str:
        row = self.db.execute("SELECT id FROM links WHERE qubit_id=? AND coupler_id=?",
                              (qubit_id, coupler_id)).fetchone()
        if row:
            return row[0]
        ident = new_id()
        self.db.execute("INSERT INTO links VALUES (?,?,?,?,?)",
                        (ident, qubit_id, coupler_id, "", beijing_now()))
        return ident

    def add_link(self, qubit_id: str, coupler_id: str, notes: str = "") -> str:
        if self.device(qubit_id)["kind"] != "qubit" or self.device(coupler_id)["kind"] != "coupler":
            raise ValueError("link must connect a qubit and a coupler")
        existing = self.links(coupler_id=coupler_id)
        if any(r["qubit_id"] == qubit_id for r in existing):
            raise ValueError("link already exists")
        if len(existing) >= 2:
            raise ValueError("a coupler can connect at most two qubits")
        ident = new_id()
        with self.db:
            self.db.execute("INSERT INTO links VALUES (?,?,?,?,?)",
                            (ident, qubit_id, coupler_id, notes, beijing_now()))
            if len(existing) == 1:
                other = existing[0]["qubit_id"]
                a, b = sorted((other, qubit_id))
                if not self.db.execute(
                    "SELECT 1 FROM couplings WHERE qubit_a=? AND qubit_b=? AND mode='coupler' AND coupler_id=?",
                    (a, b, coupler_id),
                ).fetchone():
                    self.db.execute("INSERT INTO couplings VALUES (?,?,?,?,?,?,?)",
                                    (new_id(), a, b, "coupler", coupler_id, "", beijing_now()))
        return ident

    def link(self, ident: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM links WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return row

    def links(self, coupler_id: str | None = None) -> list[sqlite3.Row]:
        if coupler_id is None:
            return self.db.execute("SELECT * FROM links ORDER BY created_at,id").fetchall()
        return self.db.execute("SELECT * FROM links WHERE coupler_id=? ORDER BY created_at,id",
                               (coupler_id,)).fetchall()

    def update_link_notes(self, ident: str, notes: str) -> None:
        self.link(ident)
        with self.db:
            self.db.execute("UPDATE links SET notes=? WHERE id=?", (notes, ident))

    def delete_link(self, ident: str) -> None:
        link = self.link(ident)
        with self.db:
            self.db.execute("DELETE FROM couplings WHERE mode='coupler' AND coupler_id=? AND "
                            "(qubit_a=? OR qubit_b=?)", (link["coupler_id"], link["qubit_id"], link["qubit_id"]))
            self.db.execute("DELETE FROM links WHERE id=?", (ident,))

    def coupling(self, ident: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM couplings WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return row

    def couplings(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM couplings ORDER BY created_at,id").fetchall()

    def delete_coupling(self, ident: str) -> None:
        coupling = self.coupling(ident)
        with self.db:
            self.db.execute("DELETE FROM couplings WHERE id=?", (ident,))
            if coupling["coupler_id"]:
                self.db.execute("DELETE FROM links WHERE coupler_id=? AND qubit_id IN (?,?)",
                                (coupling["coupler_id"], coupling["qubit_a"], coupling["qubit_b"]))

    def update_coupling_notes(self, ident: str, notes: str) -> None:
        self.coupling(ident)
        with self.db:
            self.db.execute("UPDATE couplings SET notes=? WHERE id=?", (notes, ident))

    def add_line(self, line_type: str, cryostat: str = "", port: str = "",
                 channel: str = "", notes: str = "", devices: list[str] | None = None) -> str:
        if not line_type.strip():
            raise ValueError("line type required")
        ident = new_id()
        with self.db:
            self.db.execute("INSERT INTO wiring_lines VALUES (?,?,?,?,?,?)",
                            (ident, line_type.strip(), cryostat, port, channel, notes))
            for device_id in devices or []:
                self.db.execute("INSERT INTO wiring_assignments VALUES (?,?)", (ident, device_id))
        return ident

    def lines(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM wiring_lines ORDER BY line_type,cryostat,port").fetchall()

    def line(self, line_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM wiring_lines WHERE id=?", (line_id,)).fetchone()
        if row is None:
            raise KeyError(line_id)
        return row

    def update_line(self, line_id: str, line_type: str, cryostat: str = "", port: str = "",
                    channel: str = "", notes: str = "") -> None:
        self.line(line_id)
        if not line_type.strip():
            raise ValueError("line type required")
        with self.db:
            self.db.execute(
                "UPDATE wiring_lines SET line_type=?,cryostat=?,port=?,channel=?,notes=? WHERE id=?",
                (line_type.strip(), cryostat, port, channel, notes, line_id),
            )

    def line_devices(self, line_id: str) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT device_id FROM wiring_assignments WHERE line_id=?", (line_id,))]

    def assign_line(self, line_id: str, device_id: str) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO wiring_assignments VALUES (?,?)", (line_id, device_id))

    def unassign_line(self, line_id: str, device_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM wiring_assignments WHERE line_id=? AND device_id=?",
                            (line_id, device_id))

    def delete_line(self, line_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM wiring_lines WHERE id=?", (line_id,))

    def add_definition(self, key: str, label: str, scope: str, value_type: str = "number",
                       unit: str = "", category: str = "custom") -> None:
        if scope not in ("qubit", "coupler", "coupling", "link") or value_type not in ("number", "text"):
            raise ValueError("invalid parameter definition")
        if category not in ("design", "measured", "custom") or not key.strip() or not label.strip():
            raise ValueError("invalid parameter definition")
        if not key.strip().startswith(scope + "."):
            raise ValueError(f"parameter key must start with {scope}.")
        with self.db:
            self.db.execute("INSERT INTO parameter_definitions VALUES (?,?,?,?,?,?,1)",
                            (key.strip(), label.strip(), scope, category, value_type, unit.strip()))

    def definitions(self, scope: str | None = None) -> list[sqlite3.Row]:
        ordering = "ORDER BY CASE category WHEN 'measured' THEN 0 WHEN 'design' THEN 1 ELSE 2 END, key"
        if scope:
            return self.db.execute(
                f"SELECT {_DEFINITION_COLUMNS} FROM parameter_definitions WHERE scope=? {ordering}", (scope,)
            ).fetchall()
        return self.db.execute(f"SELECT {_DEFINITION_COLUMNS} FROM parameter_definitions {ordering},scope").fetchall()

    def definition(self, key: str) -> sqlite3.Row:
        row = self.db.execute(f"SELECT {_DEFINITION_COLUMNS} FROM parameter_definitions WHERE key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return row

    def set_parameter(self, owner_id: str, key: str, value: float | str | None,
                      point: str = "default", bias_value: float | None = None,
                      bias_unit: str = "", observed_at: str | None = None,
                      source: str = "", notes: str = "", attachments=()) -> str:
        definition = self.definition(key)
        if not point.strip():
            raise ValueError("working point required")
        device = self.db.execute("SELECT kind FROM devices WHERE id=?", (owner_id,)).fetchone()
        coupling = self.db.execute("SELECT 1 FROM couplings WHERE id=?", (owner_id,)).fetchone()
        link = self.db.execute("SELECT 1 FROM links WHERE id=?", (owner_id,)).fetchone()
        if device:
            if definition["scope"] != device["kind"]:
                raise ValueError("parameter scope mismatch")
            owner_col, device_id, coupling_id, link_id = "device_id", owner_id, None, None
        elif coupling:
            if definition["scope"] != "coupling":
                raise ValueError("parameter scope mismatch")
            owner_col, device_id, coupling_id, link_id = "coupling_id", None, owner_id, None
        elif link:
            if definition["scope"] != "link":
                raise ValueError("parameter scope mismatch")
            owner_col, device_id, coupling_id, link_id = "link_id", None, None, owner_id
        else:
            raise KeyError(owner_id)
        numeric = text = None
        if value is not None:
            if definition["value_type"] == "number":
                numeric = float(value)
                if not math.isfinite(numeric):
                    raise ValueError("parameter must be finite")
            else:
                text = str(value)
        if bias_value is not None and not math.isfinite(float(bias_value)):
            raise ValueError("bias must be finite")
        timestamp = beijing_timestamp(observed_at) if observed_at is not None else beijing_now()
        ident = new_id()
        with self.db:
            self.db.execute(
                f"UPDATE parameter_records SET is_current=0 WHERE {owner_col}=? AND definition_key=? AND point=? AND is_current=1",
                (owner_id, key, point),
            )
            self.db.execute(
                """INSERT INTO parameter_records
                (id,definition_key,device_id,coupling_id,link_id,point,bias_value,bias_unit,
                 numeric_value,text_value,observed_at,source,notes,is_current)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (ident, key, device_id, coupling_id, link_id, point, bias_value, bias_unit,
                 numeric, text, timestamp, source, notes),
            )
            self._apply_attachment_changes(
                ident, (AttachmentChange(content=payload) for payload in attachments)
            )
        return ident

    def records(self, owner_id: str | None = None, current_only: bool = False,
                point: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM parameter_records WHERE 1=1"
        args: list[object] = []
        if owner_id:
            query += " AND (device_id=? OR coupling_id=? OR link_id=?)"
            args.extend([owner_id, owner_id, owner_id])
        if current_only:
            query += " AND is_current=1"
        if point is not None:
            query += " AND point=?"
            args.append(point)
        query += " ORDER BY julianday(observed_at) DESC,id DESC"
        return self.db.execute(query, args).fetchall()

    def current_values(self, point: str = "default") -> dict[tuple[str, str], object]:
        values = {}
        for row in self.records(current_only=True, point=point):
            values[(row["device_id"] or row["coupling_id"] or row["link_id"], row["definition_key"])] = (
                row["numeric_value"] if row["numeric_value"] is not None else row["text_value"]
            )
        return values

    def add_calibration(self, owner_id: str, experiment_type: str, fit_result: dict,
                        point: str = "default", notes: str = "") -> str:
        device = self.db.execute("SELECT 1 FROM devices WHERE id=?", (owner_id,)).fetchone()
        coupling = self.db.execute("SELECT 1 FROM couplings WHERE id=?", (owner_id,)).fetchone()
        if not (device or coupling):
            raise KeyError(owner_id)
        ident = new_id()
        with self.db:
            self.db.execute("INSERT INTO calibration_records VALUES (?,?,?,?,?,?,?,?)",
                            (ident, owner_id if device else None, owner_id if coupling else None,
                             experiment_type, beijing_now(), point, json.dumps(fit_result), notes))
        return ident

    def add_attachment(self, calibration_id: str, file_name: str,
                       mime_type: str, data: bytes) -> str:
        ident = new_id()
        with self.db:
            self.db.execute("INSERT INTO attachments VALUES (?,?,?,?,?,?)",
                            (ident, calibration_id, file_name, mime_type,
                             hashlib.sha256(data).hexdigest(), data))
        return ident

    def backup_to(self, destination: str | Path) -> None:
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from source")
        if destination.exists():
            raise FileExistsError(destination)
        with closing(sqlite3.connect(destination)) as target:
            self.db.backup(target)

    def validate(self) -> list[str]:
        issues = []
        if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            issues.append("SQLite integrity check failed")
        issues += ["Foreign key violation" for _ in self.db.execute("PRAGMA foreign_key_check")]
        for c in self.couplings():
            if self.device(c["qubit_a"])["kind"] != "qubit" or self.device(c["qubit_b"])["kind"] != "qubit":
                issues.append(f"Invalid coupling endpoints: {c['id']}")
            if c["coupler_id"] and self.device(c["coupler_id"])["kind"] != "coupler":
                issues.append(f"Invalid mediator: {c['id']}")
            if c["coupler_id"]:
                endpoints = {r["qubit_id"] for r in self.links(c["coupler_id"])}
                if not {c["qubit_a"], c["qubit_b"]}.issubset(endpoints):
                    issues.append(f"Mediated coupling missing link: {c['id']}")
        for link in self.links():
            if self.device(link["qubit_id"])["kind"] != "qubit" or self.device(link["coupler_id"])["kind"] != "coupler":
                issues.append(f"Invalid qubit-coupler link: {link['id']}")
        for blob in self.db.execute("SELECT sha256,byte_size,data FROM attachment_blobs"):
            if len(blob["data"]) != blob["byte_size"] or hashlib.sha256(blob["data"]).hexdigest() != blob["sha256"]:
                issues.append(f"Attachment checksum mismatch: {blob['sha256']}")
        return issues

"""Versioned .qchip upgrades. An online SQLite backup precedes every upgrade."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from .storage import SCHEMA_VERSION


def _upgrade_1_to_2(db: sqlite3.Connection) -> None:
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE links (
            id TEXT PRIMARY KEY, qubit_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            coupler_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            UNIQUE(qubit_id,coupler_id))""")
        db.execute("CREATE INDEX links_coupler ON links(coupler_id)")
        for c in db.execute("SELECT * FROM couplings WHERE mode='coupler'").fetchall():
            for qid in (c["qubit_a"], c["qubit_b"]):
                db.execute("INSERT OR IGNORE INTO links VALUES (?,?,?,?,?)",
                           (str(uuid4()), qid, c["coupler_id"], "", c["created_at"]))

        db.execute("ALTER TABLE parameter_records RENAME TO parameter_records_v1")
        db.execute("""CREATE TABLE parameter_definitions_v2 (
            key TEXT PRIMARY KEY, label TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('qubit','coupler','coupling','link')),
            category TEXT NOT NULL CHECK(category IN ('measured','design','custom')),
            value_type TEXT NOT NULL CHECK(value_type IN ('number','text')),
            unit TEXT NOT NULL DEFAULT '', is_custom INTEGER NOT NULL DEFAULT 0)""")
        db.execute("INSERT INTO parameter_definitions_v2 SELECT * FROM parameter_definitions")
        db.execute("DROP TABLE parameter_definitions")
        db.execute("ALTER TABLE parameter_definitions_v2 RENAME TO parameter_definitions")
        db.executemany("INSERT INTO parameter_definitions VALUES (?,?,?,?,?,?,0)", [
            ("link.g_qc", "比特-耦合器强度", "link", "measured", "number", "MHz"),
            ("link.c_design", "比特-耦合器设计电容", "link", "design", "number", "fF"),
        ])
        db.execute("""CREATE TABLE parameter_records (
            id TEXT PRIMARY KEY, definition_key TEXT NOT NULL REFERENCES parameter_definitions(key),
            device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
            coupling_id TEXT REFERENCES couplings(id) ON DELETE CASCADE,
            link_id TEXT REFERENCES links(id) ON DELETE CASCADE,
            point TEXT NOT NULL DEFAULT 'default', bias_value REAL, bias_unit TEXT NOT NULL DEFAULT '',
            numeric_value REAL, text_value TEXT, observed_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '', is_current INTEGER NOT NULL DEFAULT 1,
            CHECK((device_id IS NOT NULL) + (coupling_id IS NOT NULL) + (link_id IS NOT NULL) = 1))""")
        db.execute("""INSERT INTO parameter_records
            (id,definition_key,device_id,coupling_id,link_id,point,bias_value,bias_unit,
             numeric_value,text_value,observed_at,source,notes,is_current)
            SELECT id,definition_key,device_id,coupling_id,NULL,point,bias_value,bias_unit,
                   numeric_value,text_value,observed_at,source,notes,is_current
            FROM parameter_records_v1""")
        for row in db.execute("""SELECT p.*, c.qubit_a, c.qubit_b, c.coupler_id
                              FROM parameter_records p JOIN couplings c ON c.id=p.coupling_id
                              WHERE p.definition_key IN ('coupling.g_q1c','coupling.g_q2c')
                                AND c.coupler_id IS NOT NULL""").fetchall():
            qid = row["qubit_a"] if row["definition_key"] == "coupling.g_q1c" else row["qubit_b"]
            link = db.execute("SELECT id FROM links WHERE qubit_id=? AND coupler_id=?",
                              (qid, row["coupler_id"])).fetchone()
            db.execute("""INSERT INTO parameter_records
                (id,definition_key,device_id,coupling_id,link_id,point,bias_value,bias_unit,
                 numeric_value,text_value,observed_at,source,notes,is_current)
                VALUES (?,?,NULL,NULL,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()), "link.g_qc", link[0], row["point"], row["bias_value"],
                 row["bias_unit"], row["numeric_value"], row["text_value"],
                 row["observed_at"], row["source"], row["notes"], row["is_current"]))
            db.execute("DELETE FROM parameter_records WHERE id=?", (row["id"],))
        db.execute("""DELETE FROM parameter_definitions
                      WHERE key IN ('coupling.g_q1c','coupling.g_q2c')
                      AND NOT EXISTS (SELECT 1 FROM parameter_records p
                                      WHERE p.definition_key=parameter_definitions.key)""")
        db.execute("DROP TABLE parameter_records_v1")
        db.execute("""CREATE UNIQUE INDEX current_device_parameter ON parameter_records(device_id,definition_key,point)
                     WHERE is_current=1 AND device_id IS NOT NULL""")
        db.execute("""CREATE UNIQUE INDEX current_coupling_parameter ON parameter_records(coupling_id,definition_key,point)
                     WHERE is_current=1 AND coupling_id IS NOT NULL""")
        db.execute("""CREATE UNIQUE INDEX current_link_parameter ON parameter_records(link_id,definition_key,point)
                     WHERE is_current=1 AND link_id IS NOT NULL""")
        db.execute("PRAGMA user_version=2")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys=ON")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Migration completed with foreign key violations; restore backup")


MIGRATIONS = {1: _upgrade_1_to_2}


def migrate(path: str | Path) -> Path | None:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        row = db.execute("SELECT value FROM meta WHERE key='format'").fetchone()
        if row is None or row[0] != "qchip":
            raise ValueError("Not a .qchip file")
        if version == SCHEMA_VERSION:
            return None
        if version > SCHEMA_VERSION:
            raise ValueError(f"File version {version} is newer than this application")
        if any(step not in MIGRATIONS for step in range(version, SCHEMA_VERSION)):
            raise ValueError(f"No migration path from version {version} to {SCHEMA_VERSION}")
        backup = path.with_name(f"{path.stem}.pre-v{version}.qchip")
        if backup.exists():
            raise FileExistsError(backup)
        with closing(sqlite3.connect(backup)) as target:
            db.backup(target)
        for step in range(version, SCHEMA_VERSION):
            MIGRATIONS[step](db)
        return backup

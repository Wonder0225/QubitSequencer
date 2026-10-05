"""Versioned files belonging to a specific parameter measurement (no Qt)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


ATTACHMENT_STATEMENTS = (
    """CREATE TABLE attachment_blobs (
        sha256 TEXT PRIMARY KEY, byte_size INTEGER NOT NULL CHECK(byte_size>=0),
        data BLOB NOT NULL)""",
    """CREATE TABLE record_attachments (
        id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES parameter_records(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
        removed INTEGER NOT NULL DEFAULT 0 CHECK(removed IN (0,1)))""",
    "CREATE INDEX record_attachments_record ON record_attachments(record_id)",
    """CREATE TABLE attachment_versions (
        attachment_id TEXT NOT NULL REFERENCES record_attachments(id) ON DELETE CASCADE,
        version INTEGER NOT NULL CHECK(version>0), file_name TEXT NOT NULL,
        mime_type TEXT NOT NULL, encoding TEXT, notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, sha256 TEXT NOT NULL REFERENCES attachment_blobs(sha256),
        PRIMARY KEY(attachment_id,version))""",
    "CREATE INDEX attachment_versions_blob ON attachment_versions(sha256)",
    """CREATE TRIGGER attachment_blob_cleanup AFTER DELETE ON attachment_versions BEGIN
        DELETE FROM attachment_blobs WHERE sha256=OLD.sha256
          AND NOT EXISTS (SELECT 1 FROM attachment_versions WHERE sha256=OLD.sha256);
        END""",
)
ATTACHMENT_SCHEMA = ";\n".join(ATTACHMENT_STATEMENTS) + ";\n"


@dataclass(frozen=True)
class AttachmentInput:
    file_name: str
    data: bytes
    mime_type: str = "application/octet-stream"
    notes: str = ""
    encoding: str | None = None


@dataclass(frozen=True)
class AttachmentChange:
    attachment_id: str | None = None
    content: AttachmentInput | None = None
    removed: bool = False
    expected_version: int | None = None
    force_revision: bool = False


def safe_file_name(name: str) -> str:
    # Both separators are rejected even when reading a file made on another OS.
    if not name.strip() or any(c in name for c in '/\\\x00') or name in (".", ".."):
        raise ValueError("附件名称不能为空，且不能包含路径分隔符")
    if any(c in name for c in '<>:"|?*') or name.endswith((" ", ".")):
        raise ValueError("附件名称包含不支持的字符或结尾")
    stem = name.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or stem in {
        f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
    }:
        raise ValueError("附件名称是系统保留名称")
    return name


class AttachmentStore:
    """Mixed into ChipStore; all writes share its SQLite connection/transaction."""

    def record(self, record_id: str):
        row = self.db.execute("SELECT * FROM parameter_records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return row

    def parameter_attachments(self, record_id: str, include_removed: bool = False):
        self.record(record_id)
        return self.db.execute(
            """SELECT a.id,a.record_id,a.created_at,a.modified_at,a.removed,
                      v.version,v.file_name,v.mime_type,v.encoding,v.notes,v.sha256,b.byte_size
               FROM record_attachments a JOIN attachment_versions v ON v.attachment_id=a.id
               JOIN attachment_blobs b ON b.sha256=v.sha256
               WHERE a.record_id=? AND (? OR a.removed=0)
                 AND v.version=(SELECT MAX(version) FROM attachment_versions WHERE attachment_id=a.id)
               ORDER BY a.created_at,a.id""", (record_id, include_removed)
        ).fetchall()

    def attachment_counts(self):
        return dict(self.db.execute(
            "SELECT record_id,COUNT(*) FROM record_attachments WHERE removed=0 GROUP BY record_id"
        ))

    def attachment_versions(self, attachment_id: str):
        return self.db.execute(
            """SELECT v.*,b.byte_size FROM attachment_versions v
               JOIN attachment_blobs b ON b.sha256=v.sha256
               WHERE attachment_id=? ORDER BY version DESC""", (attachment_id,)
        ).fetchall()

    def read_attachment(self, attachment_id: str, version: int | None = None):
        if version is None:
            row = self.db.execute(
                """SELECT v.*,b.data,b.byte_size FROM attachment_versions v
                   JOIN attachment_blobs b ON b.sha256=v.sha256 WHERE attachment_id=?
                   ORDER BY version DESC LIMIT 1""", (attachment_id,)
            ).fetchone()
        else:
            row = self.db.execute(
                """SELECT v.*,b.data,b.byte_size FROM attachment_versions v
                   JOIN attachment_blobs b ON b.sha256=v.sha256
                   WHERE attachment_id=? AND version=?""", (attachment_id, version)
            ).fetchone()
        if row is None:
            raise KeyError((attachment_id, version))
        if len(row["data"]) != row["byte_size"] or hashlib.sha256(row["data"]).hexdigest() != row["sha256"]:
            raise ValueError("附件校验失败，内容可能损坏")
        return row

    def _write_attachment_version(self, attachment_id: str, payload: AttachmentInput,
                                  force_revision: bool = False):
        from .storage import beijing_now

        safe_file_name(payload.file_name)
        if not isinstance(payload.data, bytes):
            raise TypeError("附件内容必须为 bytes")
        if payload.encoding not in (None, "utf-8", "utf-8-sig", "gb18030"):
            raise ValueError("不支持的文本编码")
        digest = hashlib.sha256(payload.data).hexdigest()
        self.db.execute(
            "INSERT OR IGNORE INTO attachment_blobs VALUES (?,?,?)",
            (digest, len(payload.data), payload.data),
        )
        latest = self.db.execute(
            "SELECT * FROM attachment_versions WHERE attachment_id=? ORDER BY version DESC LIMIT 1",
            (attachment_id,),
        ).fetchone()
        if not force_revision and latest and (latest["file_name"], latest["mime_type"], latest["encoding"],
                       latest["notes"], latest["sha256"]) == (
            payload.file_name, payload.mime_type, payload.encoding, payload.notes, digest
        ):
            return latest["version"]
        version = latest["version"] + 1 if latest else 1
        self.db.execute(
            "INSERT INTO attachment_versions VALUES (?,?,?,?,?,?,?,?)",
            (attachment_id, version, payload.file_name, payload.mime_type,
             payload.encoding, payload.notes, beijing_now(), digest),
        )
        return version

    def _apply_attachment_changes(self, record_id: str, changes):
        from .storage import beijing_now, new_id

        self.record(record_id)
        ids = []
        for change in changes:
            now = beijing_now()
            ident = change.attachment_id
            if ident is None:
                if change.content is None or change.removed:
                    raise ValueError("新增附件必须包含内容")
                ident = new_id()
                self.db.execute(
                    "INSERT INTO record_attachments VALUES (?,?,?,?,0)", (ident, record_id, now, now)
                )
            else:
                existing = self.db.execute(
                    "SELECT * FROM record_attachments WHERE id=? AND record_id=?", (ident, record_id)
                ).fetchone()
                if existing is None:
                    raise ValueError("附件不属于这条参数记录")
                latest = self.db.execute(
                    "SELECT MAX(version) FROM attachment_versions WHERE attachment_id=?", (ident,)
                ).fetchone()[0]
                if change.expected_version is not None and change.expected_version != latest:
                    raise ValueError("附件版本已变化，请重新打开记录后编辑")
            if change.content is not None:
                self._write_attachment_version(ident, change.content, change.force_revision)
            self.db.execute(
                "UPDATE record_attachments SET removed=?,modified_at=? WHERE id=?",
                (int(change.removed), now, ident),
            )
            ids.append(ident)
        return ids

    def commit_attachment_changes(self, record_id: str, changes):
        with self.db:
            return self._apply_attachment_changes(record_id, changes)

    def restore_attachment(self, attachment_id: str, version: int):
        row = self.read_attachment(attachment_id, version)
        owner = self.db.execute("SELECT record_id FROM record_attachments WHERE id=?", (attachment_id,)).fetchone()
        payload = AttachmentInput(row["file_name"], row["data"], row["mime_type"], row["notes"], row["encoding"])
        self.commit_attachment_changes(owner["record_id"], [
            AttachmentChange(attachment_id, payload, force_revision=True)
        ])
        return self.attachment_versions(attachment_id)[0]["version"]

    def export_attachment(self, attachment_id: str, destination: str | Path,
                          version: int | None = None):
        row = self.read_attachment(attachment_id, version)
        path = Path(destination)
        if path.resolve() == self.path.resolve():
            raise ValueError("不能覆盖当前芯片文件")
        with path.open("xb") as stream:
            stream.write(row["data"])
        return path

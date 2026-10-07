"""Session undo with row deltas, including SQLite cascades and UI drafts.

Temporary triggers observe only rows touched by an edit. Images and attachments
are retained only when changed, without copying the entire database per step.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from time import monotonic


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


@dataclass
class UndoStep:
    label: str
    rows: dict
    states: list
    after_rows: dict
    after_states: list
    merge_key: object
    time: float


class EditHistory:
    def __init__(self, changed=None, limit=100):
        self.changed = changed or (lambda: None)
        self.limit = limit
        self.steps = []
        self.redo_steps = []
        self.db = None
        self.columns = {}
        self.providers = []
        self.replaying = False
        self._depth = 0
        self._before = {}

    def add_state(self, capture, restore):
        self.providers.append((capture, restore))

    def clear(self):
        self.steps.clear()
        self.redo_steps.clear()
        self.changed()

    def bind(self, db):
        self.detach()
        self.clear()
        self.db = db
        self.columns = {
            row[0]: [column[1] for column in db.execute(f"PRAGMA table_info({_quote(row[0])})")]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        db.create_function("qchip_undo_capture", -1, self._capture_row)
        for table, columns in self.columns.items():
            quoted = _quote(table)
            literal = "'" + table.replace("'", "''") + "'"
            old_values = ",".join(f"OLD.{_quote(column)}" for column in columns)
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TEMP TRIGGER {_quote('undo_' + table + '_' + operation)}
                    BEFORE {operation} ON main.{quoted} BEGIN
                    SELECT qchip_undo_capture({literal},OLD.rowid,0,{old_values}); END""")
            db.execute(f"""CREATE TEMP TRIGGER {_quote('undo_' + table + '_INSERT')}
                AFTER INSERT ON main.{quoted} BEGIN
                SELECT qchip_undo_capture({literal},NEW.rowid,1); END""")

    def detach(self):
        if self.db is not None:
            for table in self.columns:
                for operation in ("UPDATE", "DELETE", "INSERT"):
                    self.db.execute(f"DROP TRIGGER IF EXISTS temp.{_quote('undo_' + table + '_' + operation)}")
            self.db.create_function("qchip_undo_capture", -1, None)
        self.db = None
        self.columns = {}

    def _capture_row(self, table, rowid, inserted, *values):
        if self._depth and not self.replaying:
            self._before.setdefault((table, rowid), None if inserted else tuple(values))

    def _row(self, table, rowid):
        row = self.db.execute(f"SELECT * FROM {_quote(table)} WHERE rowid=?", (rowid,)).fetchone()
        return tuple(row) if row is not None else None

    @contextmanager
    def action(self, label, merge_key=None):
        if self.replaying or self._depth:
            yield
            return
        self._depth = 1
        self._before = {}
        states = [capture() for capture, _ in self.providers]
        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            self._depth = 0
            after = {key: self._row(*key) for key in self._before}
            rows = {key: before for key, before in self._before.items() if after[key] != before}
            after = {key: after[key] for key in rows}
            # Store changed columns for updates, so rotating an image does not
            # retain another copy of the unchanged image bytes at every step.
            for key, before in list(rows.items()):
                if before is not None and after[key] is not None:
                    changed = [i for i, value in enumerate(before) if value != after[key][i]]
                    rows[key] = {i: before[i] for i in changed}
                    after[key] = {i: after[key][i] for i in changed}
            after_states = [capture() for capture, _ in self.providers]
            state_changed = succeeded and states != after_states
            self._before = {}
            if rows or state_changed:
                self.redo_steps.clear()
                now = monotonic()
                if (merge_key is not None and self.steps and self.steps[-1].merge_key == merge_key
                        and now - self.steps[-1].time < 1):
                    step = self.steps[-1]
                    for key, before in rows.items():
                        if isinstance(step.rows.get(key), dict) and isinstance(before, dict):
                            for index, value in before.items():
                                step.rows[key].setdefault(index, value)
                        else:
                            step.rows.setdefault(key, before)
                    step.after_rows = {}
                    for key, before in step.rows.items():
                        current = self._row(*key)
                        step.after_rows[key] = ({i: current[i] for i in before}
                                                if isinstance(before, dict) else current)
                    step.after_states = after_states
                    step.time = now
                    unchanged = [key for key in step.rows if step.rows[key] == step.after_rows[key]]
                    for key in unchanged:
                        del step.rows[key]
                        del step.after_rows[key]
                    if not step.rows and step.states == step.after_states:
                        self.steps.pop()
                else:
                    self.steps.append(UndoStep(label, rows, states, {key: after[key] for key in rows},
                                               after_states, merge_key, now))
                    del self.steps[:-self.limit]
                self.changed()

    @property
    def label(self):
        return self.steps[-1].label if self.steps else ""

    @property
    def redo_label(self):
        return self.redo_steps[-1].label if self.redo_steps else ""

    def _restore(self, rows, states):
        if rows:
            # Materialize updates before deletions can cascade into a row that
            # also changed in the same compound edit.
            targets = {}
            for key, values in rows.items():
                if isinstance(values, dict):
                    row = list(self._row(*key))
                    for index, value in values.items():
                        row[index] = value
                    targets[key] = tuple(row)
                else:
                    targets[key] = values
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                # Cleanup triggers may restore children before parents. SQLite
                # checks foreign keys once the entire edit has been restored.
                self.db.execute("PRAGMA defer_foreign_keys=ON")
                for (table, rowid), before in targets.items():
                    if before is None:
                        self.db.execute(f"DELETE FROM {_quote(table)} WHERE rowid=?", (rowid,))
                for (table, rowid), before in targets.items():
                    if before is None:
                        continue
                    columns = self.columns[table]
                    if self._row(table, rowid) is None:
                        names = ",".join(["rowid"] + [_quote(column) for column in columns])
                        placeholders = ",".join("?" for _ in range(len(columns) + 1))
                        self.db.execute(f"INSERT INTO {_quote(table)} ({names}) VALUES ({placeholders})",
                                        (rowid, *before))
                    else:
                        assignments = ",".join(f"{_quote(column)}=?" for column in columns)
                        self.db.execute(f"UPDATE {_quote(table)} SET {assignments} WHERE rowid=?",
                                        (*before, rowid))
        for (_, restore), state in zip(self.providers, states):
            restore(state)

    def _replay(self, source, destination, forward):
        if not source:
            return ""
        step = source[-1]
        self.replaying = True
        try:
            self._restore(step.after_rows if forward else step.rows,
                          step.after_states if forward else step.states)
            destination.append(source.pop())
            if self.steps:
                self.steps[-1].time = 0
        finally:
            self.replaying = False
            self.changed()
        return step.label

    def undo(self):
        return self._replay(self.steps, self.redo_steps, False)

    def redo(self):
        return self._replay(self.redo_steps, self.steps, True)


def undoable(label, merge=False):
    """Group a public mutation; repositories without UI history stay unchanged."""
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            history = getattr(self, "undo_history", None)
            if history is None:
                return function(self, *args, **kwargs)
            key = (merge(self, *args, **kwargs) if callable(merge)
                   else (id(self), function.__name__, args[:3]) if merge else None)
            with history.action(label, key):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate

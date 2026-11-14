"""SQLite storage for task state and the audit log.

SQLite keeps the project zero-setup. All access goes through one lock so the
worker threads can share it safely.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    thread_id         TEXT NOT NULL,
    sender            TEXT NOT NULL,
    subject           TEXT NOT NULL,
    source_path       TEXT NOT NULL,
    status            TEXT NOT NULL,
    intent            TEXT,
    summary           TEXT,
    estimated_minutes INTEGER,
    gate_reasons      TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    output_dir        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  TEXT NOT NULL,
    event    TEXT NOT NULL,
    detail   TEXT,
    actor    TEXT NOT NULL DEFAULT 'system',
    at       TEXT NOT NULL
);
"""

STATUSES = (
    "received", "classified", "awaiting_approval", "approved", "rejected",
    "refused", "running", "completed", "failed",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- tasks --------------------------------------------------------------
    def insert_task(self, *, task_id, thread_id, sender, subject, source_path) -> None:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tasks (id, thread_id, sender, subject, source_path, status,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, thread_id, sender, subject, str(source_path), "received", now, now),
            )

    def update_task(self, task_id: str, **fields) -> None:
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}")
        if "gate_reasons" in fields and not isinstance(fields["gate_reasons"], str):
            fields["gate_reasons"] = json.dumps(fields["gate_reasons"])
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))

    def increment_attempts(self, task_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE tasks SET attempts = attempts + 1 WHERE id = ?", (task_id,))

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_dict(row) if row else None

    def list_tasks(self, status: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM tasks", ()
        if status:
            sql, args = sql + " WHERE status = ?", (status,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY created_at, id", args).fetchall()
        return [_task_dict(r) for r in rows]

    def known_ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT id FROM tasks")}

    # -- audit --------------------------------------------------------------
    def audit(self, task_id: str, event: str, detail: str = "", actor: str = "system") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit_log (task_id, event, detail, actor, at) VALUES (?,?,?,?,?)",
                (task_id, event, detail, actor, _now()),
            )

    def audit_log(self, task_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM audit_log", ()
        if task_id:
            sql, args = sql + " WHERE task_id = ?", (task_id,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(r) for r in rows]


def _task_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["gate_reasons"] = json.loads(d["gate_reasons"]) if d.get("gate_reasons") else []
    return d

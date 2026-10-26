"""Ingestion, dedupe, recent-event, and ClickUp<->Kanban link state.

This is intentionally not an execution queue or task engine. Hermes Kanban owns
execution state. This store only remembers which webhook deliveries were already
handled, a short audit trail of routing outcomes, and which Kanban task was
created for which ClickUp task so the reconciler can mirror progress back to
ClickUp.

The store is SQLite-backed. It defaults to an in-memory database, which keeps
tests and previews free of filesystem writes. Long-running services should pass
a file path (``--state-db`` or ``CLICKUP_ROUTER_STATE_DB``) so dedupe keys and
links survive restarts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

STATE_DB_ENV = "CLICKUP_ROUTER_STATE_DB"

# Link lifecycle. "open" links are polled by the reconciler; the rest are final.
LINK_OPEN = "open"
LINK_DONE = "done"
LINK_ARCHIVED = "archived"
LINK_CLOSED_IN_CLICKUP = "closed_in_clickup"
LINK_MISSING = "missing"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    workspace TEXT NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    extra TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_workspace ON events (workspace, id);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT NOT NULL,
    clickup_task_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    kanban_task_id TEXT NOT NULL DEFAULT '',
    kanban_status TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    notified_status TEXT NOT NULL DEFAULT '',
    notified_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS links_kanban_task
    ON links (workspace, kanban_task_id) WHERE kanban_task_id != '';
CREATE INDEX IF NOT EXISTS links_open ON links (workspace, state);
CREATE INDEX IF NOT EXISTS links_task ON links (workspace, clickup_task_id, profile);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Link:
    id: int
    workspace: str
    clickup_task_id: str
    profile: str
    external_ref: str
    kanban_task_id: str
    kanban_status: str
    state: str
    notified_status: str
    notified_at: float
    created_at: float
    updated_at: float


class RouterState:
    def __init__(self, max_events: int = 100, path: str | Path | None = None) -> None:
        self.max_events = max_events
        self._lock = threading.RLock()
        self.path = str(path) if path else ":memory:"
        self._conn = self._open(self.path)

    # -- lifecycle -------------------------------------------------------
    @staticmethod
    def _open(path: str) -> sqlite3.Connection:
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path if path == ":memory:" else str(Path(path).expanduser()), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        return conn

    def configure(self, path: str | Path | None) -> None:
        """Re-point the store at ``path`` (or back to memory when empty)."""
        target = str(path) if path else ":memory:"
        with self._lock:
            old = self._conn
            self._conn = self._open(target)
            self.path = target
            old.close()

    def reset(self) -> None:
        """Drop all state. Intended for tests and disposable previews."""
        with self._lock:
            for table in ("seen", "events", "links", "meta"):
                self._conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names

    # -- dedupe ----------------------------------------------------------
    def mark_seen(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("INSERT OR IGNORE INTO seen (key, created_at) VALUES (?, ?)", (key, time.time()))
            return cur.rowcount == 1

    def has_seen(self, key: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone() is not None

    def forget_seen(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM seen WHERE key = ?", (key,))

    def prune_seen(self, max_age_seconds: float) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM seen WHERE created_at < ?", (time.time() - max_age_seconds,))
            return cur.rowcount

    # -- audit trail -----------------------------------------------------
    def record_event(self, workspace: str, event_id: str, status: str, summary: str, extra: Any = None) -> None:
        if is_dataclass(extra):
            extra = asdict(extra)
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (time, workspace, event_id, status, summary, extra) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), workspace, event_id, status, summary, json.dumps(extra or {}, default=str)),
            )
            self._conn.execute(
                "DELETE FROM events WHERE workspace = ? AND id NOT IN (SELECT id FROM events WHERE workspace = ? ORDER BY id DESC LIMIT ?)",
                (workspace, workspace, self.max_events),
            )

    def recent(self, workspace: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            if workspace is None:
                rows = self._conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM events WHERE workspace = ? ORDER BY id DESC LIMIT ?", (workspace, limit)).fetchall()
        return [
            {
                "time": row["time"],
                "workspace": row["workspace"],
                "event_id": row["event_id"],
                "status": row["status"],
                "summary": row["summary"],
                "extra": json.loads(row["extra"] or "{}"),
            }
            for row in rows
        ]

    # -- small key/value metadata ---------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value, default=str)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def record_error(self, message: str) -> None:
        self.set_meta("last_error", str(message)[:500])

    @property
    def last_error(self) -> str:
        return str(self.get_meta("last_error", "") or "")

    # -- ClickUp <-> Kanban links ---------------------------------------
    def add_link(self, workspace: str, clickup_task_id: str, profile: str, external_ref: str, kanban_task_id: str = "", kanban_status: str = "") -> int:
        now = time.time()
        with self._lock:
            if kanban_task_id:
                row = self._conn.execute(
                    "SELECT id FROM links WHERE workspace = ? AND kanban_task_id = ?", (workspace, kanban_task_id)
                ).fetchone()
                if row:
                    return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO links (workspace, clickup_task_id, profile, external_ref, kanban_task_id, kanban_status, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (workspace, clickup_task_id, profile, external_ref, kanban_task_id, kanban_status, LINK_OPEN, now, now),
            )
            return int(cur.lastrowid)

    def update_link(self, link_id: int, **fields: Any) -> None:
        allowed = {"kanban_task_id", "kanban_status", "state", "notified_status", "notified_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with self._lock:
            self._conn.execute(f"UPDATE links SET {assignments} WHERE id = ?", (*updates.values(), link_id))  # noqa: S608 - allow-listed columns

    def open_links(self, workspace: str, limit: int | None = None) -> list[Link]:
        query = "SELECT * FROM links WHERE workspace = ? AND state = ? ORDER BY updated_at ASC"
        params: tuple[Any, ...] = (workspace, LINK_OPEN)
        if limit is not None:
            query += " LIMIT ?"
            params += (limit,)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [Link(**dict(row)) for row in rows]

    def has_open_link(self, workspace: str, clickup_task_id: str, profile: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM links WHERE workspace = ? AND clickup_task_id = ? AND profile = ? AND state = ?",
                (workspace, clickup_task_id, profile, LINK_OPEN),
            ).fetchone()
        return row is not None

    def known_task_ids(self, workspace: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT clickup_task_id FROM links WHERE workspace = ?", (workspace,)).fetchall()
        return {str(row["clickup_task_id"]) for row in rows}

    def link_counts(self, workspace: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT state, COUNT(*) AS n FROM links WHERE workspace = ? GROUP BY state", (workspace,)).fetchall()
        return {str(row["state"]): int(row["n"]) for row in rows}


STATE = RouterState()

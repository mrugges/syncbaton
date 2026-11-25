"""SQLite state: held projects, last check-ins, the event log and reported conflicts.

One connection is shared by all threads and guarded by a single condition lock, so every
read-modify-write that runs inside ``transaction()`` is atomic. The same condition wakes
long-polling readers when an event is added.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS locks (
    folder TEXT NOT NULL, project TEXT NOT NULL, holder TEXT NOT NULL,
    since REAL NOT NULL, heartbeat REAL NOT NULL,
    PRIMARY KEY (folder, project)
);
CREATE TABLE IF NOT EXISTS checkins (
    folder TEXT NOT NULL, project TEXT NOT NULL, user TEXT NOT NULL,
    at REAL NOT NULL, note TEXT NOT NULL,
    PRIMARY KEY (folder, project)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, type TEXT NOT NULL,
    folder TEXT NOT NULL, project TEXT NOT NULL, user TEXT,
    note TEXT, previous_holder TEXT, path TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
    folder TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY (folder, path)
);
"""

# The extra field each event type carries, if any.
EVENT_EXTRA = {"checkin": "note", "take_over": "previous_holder", "conflict": "path"}
EVENT_PAGE = 500

Event = dict[str, Any]

logger = logging.getLogger(__name__)


class Store:
    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._cond = threading.Condition()
        self._depth = 0
        self._pending: list[Event] = []
        self._listeners: list[Callable[[Event], None]] = []
        with self._cond:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._cond:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """Hold the lock and run everything inside as one transaction; nesting joins the outer.

        Event listeners run after the outermost transaction has committed, and never for events
        of a transaction that was rolled back.
        """
        with self._cond:
            if self._depth:
                yield self._conn
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._pending = []
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    logger.exception("rollback failed")
                raise
            finally:
                self._depth = 0
            committed, self._pending = self._pending, []
        for event in committed:
            for listener in self._listeners:
                listener(event)

    def add_listener(self, listener: Callable[[Event], None]) -> None:
        self._listeners.append(listener)

    def add_event(
        self,
        type_: str,
        folder: str,
        project: str,
        user: str | None,
        at: str,
        *,
        note: str | None = None,
        previous_holder: str | None = None,
        path: str | None = None,
    ) -> Event:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO events (at, type, folder, project, user, note, previous_holder, path)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (at, type_, folder, project, user, note, previous_holder, path),
            )
            event = self._event(
                {
                    "id": cursor.lastrowid,
                    "at": at,
                    "type": type_,
                    "folder": folder,
                    "project": project,
                    "user": user,
                    "note": note,
                    "previous_holder": previous_holder,
                    "path": path,
                }
            )
            self._pending.append(event)
            self._cond.notify_all()
        return event

    @staticmethod
    def _event(row: Any) -> Event:
        event = {k: row[k] for k in ("id", "at", "type", "folder", "project", "user")}
        column = EVENT_EXTRA.get(event["type"])
        if column:
            event[column] = row[column]
        return event

    def events_after(self, since: int) -> list[Event]:
        with self._cond:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (since, EVENT_PAGE)
            ).fetchall()
        return [self._event(row) for row in rows]

    def latest_event_id(self) -> int:
        with self._cond:
            row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM events").fetchone()
        return int(row["id"])

    def wait_for_events(self, since: int, timeout: float) -> list[Event]:
        """Events after ``since``, waiting up to ``timeout`` seconds for the first one."""
        with self._cond:
            self._cond.wait_for(lambda: self.latest_event_id() > since, timeout)
            return self.events_after(since)

    def mark_conflict_reported(self, folder: str, path: str) -> bool:
        """Remember a conflict path. True the first time, False if it was already reported."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO conflicts (folder, path) VALUES (?, ?)", (folder, path)
            )
            return cursor.rowcount == 1

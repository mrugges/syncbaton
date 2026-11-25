"""Check-out, heartbeat and check-in rules. The store is the only state."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from syncbaton.store import Store
from syncbaton.timefmt import format_time


class LockError(Exception):
    pass


class Held(LockError):
    def __init__(self, holder: str, since: float | None):
        super().__init__(f"held by {holder}")
        self.holder, self.since = holder, since


class NotStale(Held):
    pass


class NotHolder(LockError):
    pass


class StillSyncing(LockError):
    def __init__(self, files: list[str]):
        super().__init__("Syncthing still needs files under the project")
        self.files = files


@dataclass(frozen=True)
class LastCheckin:
    user: str
    at: float
    note: str


@dataclass(frozen=True)
class ProjectState:
    name: str
    holder: str | None = None
    since: float | None = None
    heartbeat: float | None = None
    last_checkin: LastCheckin | None = None

    def is_stale(self, now: float, stale_after: float) -> bool:
        return self.heartbeat is not None and now - self.heartbeat > stale_after

    def to_json(self, now: float, stale_after: float) -> dict[str, Any]:
        last = self.last_checkin
        return {
            "name": self.name,
            "holder": self.holder,
            "since": None if self.since is None else format_time(self.since),
            "heartbeat": None if self.heartbeat is None else format_time(self.heartbeat),
            "stale": self.is_stale(now, stale_after),
            "last_checkin": None
            if last is None
            else {"user": last.user, "at": format_time(last.at), "note": last.note},
        }


class LockManager:
    def __init__(self, store: Store, stale_after: float, clock: Callable[[], float] = time.time):
        self.store = store
        self.stale_after = stale_after
        self.clock = clock

    def state(self, folder: str, project: str) -> ProjectState:
        return self.snapshot(folder, [project])[project]

    def snapshot(self, folder: str, projects: list[str]) -> dict[str, ProjectState]:
        with self.store.transaction() as conn:
            locks = {
                r["project"]: r
                for r in conn.execute("SELECT * FROM locks WHERE folder = ?", (folder,))
            }
            checkins = {
                r["project"]: r
                for r in conn.execute("SELECT * FROM checkins WHERE folder = ?", (folder,))
            }
        states = {}
        for name in projects:
            lock, checkin = locks.get(name), checkins.get(name)
            states[name] = ProjectState(
                name=name,
                holder=lock["holder"] if lock else None,
                since=lock["since"] if lock else None,
                heartbeat=lock["heartbeat"] if lock else None,
                last_checkin=(
                    LastCheckin(checkin["user"], checkin["at"], checkin["note"])
                    if checkin
                    else None
                ),
            )
        return states

    def checkout(
        self, folder: str, project: str, user: str, *, take_over: bool = False
    ) -> ProjectState:
        with self.store.transaction() as conn:
            now = self.clock()
            current = self.state(folder, project)
            if current.holder == user:
                conn.execute(
                    "UPDATE locks SET heartbeat = ? WHERE folder = ? AND project = ?",
                    (now, folder, project),
                )
                return self.state(folder, project)
            previous = current.holder
            if previous is not None and not take_over:
                raise Held(previous, current.since)
            if previous is not None and not current.is_stale(now, self.stale_after):
                raise NotStale(previous, current.since)
            conn.execute(
                "INSERT OR REPLACE INTO locks (folder, project, holder, since, heartbeat)"
                " VALUES (?, ?, ?, ?, ?)",
                (folder, project, user, now, now),
            )
            if previous is None:
                self.store.add_event("checkout", folder, project, user, format_time(now))
            else:
                self.store.add_event(
                    "take_over", folder, project, user, format_time(now), previous_holder=previous
                )
            return self.state(folder, project)

    def heartbeat(self, folder: str, project: str, user: str) -> ProjectState:
        with self.store.transaction() as conn:
            if self.state(folder, project).holder != user:
                raise NotHolder
            conn.execute(
                "UPDATE locks SET heartbeat = ? WHERE folder = ? AND project = ?",
                (self.clock(), folder, project),
            )
            return self.state(folder, project)

    def checkin(
        self,
        folder: str,
        project: str,
        user: str,
        note: str,
        gate: Callable[[], None],
    ) -> ProjectState:
        """Release the project. ``gate`` raises to refuse and runs without the store lock held."""
        if self.state(folder, project).holder != user:
            raise NotHolder
        gate()
        with self.store.transaction() as conn:
            if self.state(folder, project).holder != user:
                raise NotHolder
            now = self.clock()
            conn.execute("DELETE FROM locks WHERE folder = ? AND project = ?", (folder, project))
            conn.execute(
                "INSERT OR REPLACE INTO checkins (folder, project, user, at, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (folder, project, user, now, note),
            )
            self.store.add_event("checkin", folder, project, user, format_time(now), note=note)
            return self.state(folder, project)

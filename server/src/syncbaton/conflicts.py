"""Background scan for Syncthing conflict copies inside projects."""

import logging
import threading
import time
from collections.abc import Callable, Sequence

from syncbaton.config import FolderConfig
from syncbaton.projects import scan_conflicts
from syncbaton.store import Store
from syncbaton.timefmt import format_time

logger = logging.getLogger(__name__)

SCAN_INTERVAL = 60.0


class ConflictScanner:
    def __init__(
        self,
        folders: Sequence[FolderConfig],
        store: Store,
        clock: Callable[[], float] = time.time,
        interval: float = SCAN_INTERVAL,
    ):
        self._folders = folders
        self._store = store
        self._clock = clock
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> None:
        """Emit a ``conflict`` event for every conflict path not reported before."""
        for folder in self._folders:
            for hit in scan_conflicts(folder):
                with self._store.transaction():
                    if self._store.mark_conflict_reported(folder.id, hit.path):
                        self._store.add_event(
                            "conflict",
                            folder.id,
                            hit.project,
                            None,
                            format_time(self._clock()),
                            path=hit.path,
                        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="conflict-scan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("conflict scan failed")
            self._stop.wait(self._interval)

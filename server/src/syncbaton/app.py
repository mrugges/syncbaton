"""The pieces a request handler needs, bundled so tests can swap the clock and Syncthing."""

import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from syncbaton.config import Config, FolderConfig
from syncbaton.locks import LockManager, StillSyncing
from syncbaton.projects import paths_under
from syncbaton.store import Store
from syncbaton.syncthing import SyncthingClient, SyncthingError

MAX_FILES_REPORTED = 20


@dataclass
class App:
    config: Config
    store: Store
    syncthing: SyncthingClient
    clock: Callable[[], float] = time.time
    locks: LockManager = field(init=False)
    _device_id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.locks = LockManager(self.store, self.config.stale_after, self.clock)

    def authenticate(self, token: str) -> str | None:
        """Return the user a bearer token belongs to. Compares every hash, in constant time."""
        digest = hashlib.sha256(token.encode()).hexdigest()
        found = None
        for user, stored in self.config.users.items():
            if hmac.compare_digest(digest, stored):
                found = user
        return found

    def folder(self, folder_id: str) -> FolderConfig | None:
        return next((f for f in self.config.folders if f.id == folder_id), None)

    def device_id(self) -> str | None:
        """The server's Syncthing device ID, or None while Syncthing cannot be reached."""
        if self._device_id is None:
            try:
                self._device_id = self.syncthing.my_id()
            except SyncthingError:
                return None
        return self._device_id

    def check_synced(self, folder_id: str, project: str) -> None:
        """Raise ``StillSyncing`` while Syncthing needs files under ``project``."""
        pending = paths_under(self.syncthing.need(folder_id), project)
        if pending:
            raise StillSyncing(pending[:MAX_FILES_REPORTED])

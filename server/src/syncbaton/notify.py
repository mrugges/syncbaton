"""Optional push notifications for events: ntfy and a generic JSON webhook."""

import json
import logging
import queue
import threading
from typing import Any

from syncbaton._http import http_request
from syncbaton.config import NotifyConfig

logger = logging.getLogger(__name__)

_VERBS = {"checkout": "checked out", "checkin": "checked in"}


def describe(event: dict[str, Any]) -> str:
    where = f"{event['project']} ({event['folder']})"
    kind = event["type"]
    if kind == "conflict":
        return f"Sync conflict in {where}: {event['path']}"
    if kind == "take_over":
        return f"{event['user']} took over {where} from {event['previous_holder']}"
    text = f"{event['user']} {_VERBS.get(kind, kind)} {where}"
    note = event.get("note")
    return f"{text}: {note}" if note else text


class Notifier:
    """Delivers events from a worker thread so a slow or failing target never delays a request."""

    def __init__(self, config: NotifyConfig, timeout: float = 10.0):
        self._config = config
        self._timeout = timeout
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        if config.ntfy_url or config.webhook_url:
            self._thread = threading.Thread(target=self._run, name="notify", daemon=True)
            self._thread.start()

    def notify(self, event: dict[str, Any]) -> None:
        if self._thread:
            self._queue.put(event)

    def flush(self) -> None:
        self._queue.join()

    def close(self) -> None:
        if self._thread:
            self._queue.put(None)
            self._thread.join(self._timeout + 1)

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is None:
                    return
                self._deliver(event)
            finally:
                self._queue.task_done()

    def _deliver(self, event: dict[str, Any]) -> None:
        if self._config.ntfy_url:
            self._post(
                "ntfy",
                self._config.ntfy_url,
                describe(event).encode(),
                {
                    "Title": f"syncbaton: {event['type']}",
                    "Content-Type": "text/plain; charset=utf-8",
                },
            )
        if self._config.webhook_url:
            self._post(
                "webhook",
                self._config.webhook_url,
                json.dumps(event).encode(),
                {"Content-Type": "application/json"},
            )

    def _post(self, name: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        try:
            response = http_request("POST", url, headers=headers, body=body, timeout=self._timeout)
        except Exception as err:
            logger.warning("%s delivery failed: %s", name, err)
            return
        if response.status >= 400:
            logger.warning("%s delivery failed: HTTP %d", name, response.status)

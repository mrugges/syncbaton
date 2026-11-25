"""Client for the two Syncthing REST calls syncbaton needs."""

import json
import time
from http.client import HTTPException
from typing import Any
from urllib.parse import urlencode

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)
from tenacity.wait import wait_base

from syncbaton._http import http_request

NEED_PAGE_SIZE = 5000
NEED_MAX_PAGES = 1000
RETRY_BUDGET = 10.0
REQUEST_TIMEOUT = 4.0
_MIN_REQUEST_TIMEOUT = 0.5
_NEED_LISTS = ("progress", "queued", "rest")


class SyncthingError(Exception):
    """Syncthing could not be asked, or answered with something unusable."""


class _Transient(SyncthingError):
    """A failure worth retrying: connection problems and 5xx responses."""


def _need_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        msg = "unexpected response from Syncthing: not a JSON object"
        raise SyncthingError(msg)
    return body


def need_paths(body: Any) -> list[str]:
    """Names of the files in the ``progress``, ``queued`` and ``rest`` lists of a need response."""
    paths: list[str] = []
    for key in _NEED_LISTS:
        for entry in _need_object(body).get(key) or []:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                paths.append(entry["name"])
    return paths


def need_entry_count(body: Any) -> int:
    """How many entries (named or not) a need response page holds; a short page is the last."""
    return sum(len(_need_object(body).get(key) or []) for key in _NEED_LISTS)


class SyncthingClient:
    """Talks to Syncthing's REST API. Every call retries transient failures with exponential
    backoff, for at most ``budget`` seconds (plus the last request's timeout) in total."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        attempts: int = 4,
        wait: wait_base | None = None,
        timeout: float = REQUEST_TIMEOUT,
        budget: float = RETRY_BUDGET,
        page_size: int = NEED_PAGE_SIZE,
        max_pages: int = NEED_MAX_PAGES,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._attempts = attempts
        self._wait = wait if wait is not None else wait_exponential(multiplier=0.5, max=2)
        self._timeout = timeout
        self._budget = budget
        self._page_size = page_size
        self._max_pages = max_pages

    def my_id(self) -> str:
        body = self._get("/rest/system/status", time.monotonic() + self._budget)
        device_id = body.get("myID") if isinstance(body, dict) else None
        if not isinstance(device_id, str) or not device_id:
            msg = "unexpected response from Syncthing: no myID in system status"
            raise SyncthingError(msg)
        return device_id

    def need(self, folder: str) -> list[str]:
        """Every path Syncthing still needs in ``folder``.

        ``/rest/db/need`` returns one page (65536 entries unless asked otherwise), so this asks for
        pages until one comes back short. The whole walk shares one retry budget.
        """
        deadline = time.monotonic() + self._budget
        paths: list[str] = []
        for page in range(1, self._max_pages + 1):
            query = urlencode({"folder": folder, "page": page, "perpage": self._page_size})
            body = self._get("/rest/db/need?" + query, deadline)
            paths.extend(need_paths(body))
            if need_entry_count(body) < self._page_size:
                return paths
        msg = f"Syncthing reports more than {self._max_pages * self._page_size} files needed"
        raise SyncthingError(msg)

    def _get(self, target: str, deadline: float) -> Any:
        retrying = Retrying(
            retry=retry_if_exception_type(_Transient),
            stop=stop_after_attempt(self._attempts)
            | stop_after_delay(max(deadline - time.monotonic(), 0)),
            wait=self._wait,
            reraise=True,
        )
        return retrying(self._get_once, target, deadline)

    def _get_once(self, target: str, deadline: float) -> Any:
        path = target.split("?", 1)[0]
        timeout = max(min(self._timeout, deadline - time.monotonic()), _MIN_REQUEST_TIMEOUT)
        try:
            response = http_request(
                "GET",
                self._base_url + target,
                headers={"X-API-Key": self._api_key},
                timeout=timeout,
            )
        except (OSError, HTTPException) as err:
            msg = f"cannot reach Syncthing at {self._base_url}: {err}"
            raise _Transient(msg) from err
        if response.status != 200:
            msg = f"Syncthing returned HTTP {response.status} for {path}"
            raise (_Transient if response.status >= 500 else SyncthingError)(msg)
        try:
            return json.loads(response.body)
        except ValueError as err:
            msg = f"unexpected response from Syncthing for {path}: not JSON"
            raise SyncthingError(msg) from err

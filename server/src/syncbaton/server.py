"""HTTP front end for the API described in docs/api.md."""

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlsplit

from syncbaton.app import App
from syncbaton.config import FolderConfig
from syncbaton.locks import Held, NotHolder, NotStale, StillSyncing
from syncbaton.projects import list_project_names, resolve_project
from syncbaton.syncthing import SyncthingError
from syncbaton.timefmt import format_time

logger = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
MAX_WAIT = 60
MAX_INT_PARAM = 2**63 - 1
DEFAULT_WAIT = 30

Response = tuple[int, dict[str, Any]]


class ApiError(Exception):
    def __init__(self, status: int, code: str, **extra: Any):
        super().__init__(code)
        self.status = status
        self.payload = {"error": code, **extra}


def not_found() -> ApiError:
    return ApiError(404, "not_found")


def bad_request() -> ApiError:
    return ApiError(400, "bad_request")


def _json_body(raw: bytes) -> dict[str, Any]:
    """The request body as an object; an empty body is an empty object."""
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError as err:
        raise bad_request() from err
    if not isinstance(body, dict):
        raise bad_request()
    return body


def _int_param(query: dict[str, list[str]], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    text = values[0]
    if not text.isascii() or not text.isdigit():
        raise bad_request()
    value = int(text)
    if value > MAX_INT_PARAM:
        raise bad_request()
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "syncbaton"
    sys_version = ""
    timeout = 30  # seconds a client may stay silent before the connection is dropped

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default access log; ``_handle`` writes one line per request instead."""

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    @property
    def app(self) -> App:
        return cast("SyncbatonServer", self.server).app

    def _handle(self, method: str) -> None:
        self._user: str | None = None
        parts = urlsplit(self.path)
        try:
            body = self._read_body()
            status, payload = self._dispatch(method, parts, body)
        except ApiError as err:
            status, payload = err.status, err.payload
        except Exception:
            logger.exception("unhandled error")
            status, payload = 500, {"error": "internal"}
        self._send(status, payload)
        path = "".join(c if c.isprintable() else "?" for c in parts.path)
        logger.info("%s %s %d user=%s", method, path, status, self._user or "-")

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding") is not None:
            # Chunked bodies are not supported; reading none would leave them to be parsed as the
            # next request on this connection.
            self.close_connection = True
            raise bad_request()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as err:
            self.close_connection = True
            raise bad_request() from err
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            raise bad_request()
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _authenticate(self) -> str:
        header = self.headers.get("Authorization") or ""
        scheme, _, token = header.partition(" ")
        user = self.app.authenticate(token.strip()) if scheme.lower() == "bearer" else None
        if user is None:
            raise ApiError(401, "unauthorized")
        self._user = user
        return user

    def _dispatch(self, method: str, parts: Any, raw: bytes) -> Response:
        segments = [unquote(s) for s in parts.path.split("/")[1:]]
        if method == "GET" and segments == ["healthz"]:
            return 200, {"ok": True}
        user = self._authenticate()
        query = parse_qs(parts.query)
        if method == "GET" and segments == ["info"]:
            return self._info(user)
        if method == "GET" and segments == ["events"]:
            return self._events(query)
        if len(segments) >= 3 and segments[0] == "folders" and segments[2] == "projects":
            folder = self.app.folder(segments[1])
            if folder is None:
                raise not_found()
            if method == "GET" and len(segments) == 3:
                return self._projects(folder)
            if method == "POST" and len(segments) == 5:
                return self._action(folder, segments[3], segments[4], user, raw)
        raise not_found()

    def _info(self, user: str) -> Response:
        return 200, {
            "user": user,
            "device_id": self.app.device_id(),
            "folders": [{"id": f.id} for f in self.app.config.folders],
            "stale_after_seconds": self.app.config.stale_after,
        }

    def _project_json(self, folder: FolderConfig, name: str) -> dict[str, Any]:
        state = self.app.locks.state(folder.id, name)
        return state.to_json(self.app.clock(), self.app.config.stale_after)

    def _projects(self, folder: FolderConfig) -> Response:
        names = list_project_names(folder)
        states = self.app.locks.snapshot(folder.id, names)
        now, stale_after = self.app.clock(), self.app.config.stale_after
        return 200, {"projects": [states[n].to_json(now, stale_after) for n in names]}

    def _action(
        self, folder: FolderConfig, raw_name: str, action: str, user: str, raw: bytes
    ) -> Response:
        name = resolve_project(folder, raw_name)
        if name is None or action not in ("checkout", "heartbeat", "checkin"):
            raise not_found()
        body = _json_body(raw)
        locks = self.app.locks
        try:
            if action == "checkout":
                take_over = body.get("take_over", False)
                if not isinstance(take_over, bool):
                    raise bad_request()
                locks.checkout(folder.id, name, user, take_over=take_over)
            elif action == "heartbeat":
                locks.heartbeat(folder.id, name, user)
            else:
                note = body.get("note", "")
                if not isinstance(note, str):
                    raise bad_request()
                locks.checkin(
                    folder.id, name, user, note, gate=lambda: self.app.check_synced(folder.id, name)
                )
        except NotStale as err:
            raise self._held_error("not_stale", err) from err
        except Held as err:
            raise self._held_error("held", err) from err
        except NotHolder as err:
            raise ApiError(409, "not_holder") from err
        except StillSyncing as err:
            raise ApiError(409, "still_syncing", files=err.files) from err
        except SyncthingError as err:
            logger.warning("check-in refused, Syncthing unavailable: %s", err)
            raise ApiError(503, "syncthing_unavailable") from err
        return 200, self._project_json(folder, name)

    @staticmethod
    def _held_error(code: str, err: Held) -> ApiError:
        since = None if err.since is None else format_time(err.since)
        return ApiError(409, code, holder=err.holder, since=since)

    def _events(self, query: dict[str, list[str]]) -> Response:
        store = self.app.store
        latest = store.latest_event_id()
        since = _int_param(query, "since", latest)
        wait = min(_int_param(query, "wait", DEFAULT_WAIT), MAX_WAIT)
        events = store.wait_for_events(since, wait)
        last_id = events[-1]["id"] if events else min(since, store.latest_event_id())
        return 200, {"events": events, "last_id": last_id}


class SyncbatonServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: App):
        super().__init__(address, Handler)
        self.app = app


def make_server(app: App, host: str, port: int) -> SyncbatonServer:
    return SyncbatonServer((host, port), app)

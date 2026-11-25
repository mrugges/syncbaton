import hashlib
import http.client
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from tenacity import wait_none

from syncbaton.app import App
from syncbaton.config import (
    Config,
    FolderConfig,
    NotifyConfig,
    SyncthingConfig,
)
from syncbaton.server import make_server
from syncbaton.store import Store
from syncbaton.syncthing import SyncthingClient

TOKENS = {"alex": "token-alex", "sam": "token-sam"}
DEVICE_ID = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH"
API_KEY = "stub-api-key"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@dataclass
class StubSyncthing:
    """Answers the two Syncthing REST calls syncbaton uses and records what it was asked."""

    need: dict[str, Any] = field(default_factory=lambda: {"progress": [], "queued": [], "rest": []})
    status: dict[str, Any] = field(default_factory=lambda: {"myID": DEVICE_ID})
    raw_body: bytes | None = None
    fail_next: int = 0
    always_status: int | None = None
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    pages: list[tuple[int, int]] = field(default_factory=list)
    server: ThreadingHTTPServer | None = None

    def need_page(self, page: int, perpage: int):
        """What Syncthing answers: progress, queued and rest share the page window in that order."""
        if not isinstance(self.need, dict):
            return self.need
        flat = [(key, e) for key in ("progress", "queued", "rest") for e in self.need.get(key, [])]
        window = flat[(page - 1) * perpage : page * perpage]
        return {key: [e for k, e in window if k == key] for key in ("progress", "queued", "rest")}

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def set_need(self, *, progress=(), queued=(), rest=()):
        def entries(names):
            return [{"name": n, "size": 1} for n in names]

        self.need = {
            "progress": entries(progress),
            "queued": entries(queued),
            "rest": entries(rest),
            "page": 1,
            "perpage": 65536,
        }


@pytest.fixture
def stub() -> Iterator[StubSyncthing]:
    state = StubSyncthing()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            parts = urlsplit(self.path)
            state.requests.append((parts.path, self.headers.get("X-API-Key")))
            status, body = 200, {}
            if state.always_status:
                status, body = state.always_status, {"error": "stub"}
            elif state.fail_next > 0:
                state.fail_next -= 1
                status, body = 500, {"error": "stub"}
            elif self.headers.get("X-API-Key") != API_KEY:
                status, body = 403, {"error": "forbidden"}
            elif parts.path == "/rest/system/status":
                body = state.status
            elif parts.path == "/rest/db/need":
                folder = parse_qs(parts.query).get("folder", [None])[0]
                state.requests[-1] = (
                    f"{parts.path}?folder={folder}",
                    self.headers.get("X-API-Key"),
                )
                query = parse_qs(parts.query)
                page = int(query.get("page", ["1"])[0])
                perpage = int(query.get("perpage", ["65536"])[0])
                state.pages.append((page, perpage))
                body = state.need_page(page, perpage)
            else:
                status, body = 404, {}
            data = state.raw_body if state.raw_body is not None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    state.server = srv
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield state
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def music(tmp_path: Path) -> Path:
    root = tmp_path / "music"
    (root / "Song A.logicx" / "Alternatives").mkdir(parents=True)
    (root / "Song B.logicx").mkdir()
    (root / "notes.txt").write_text("x")
    return root


@pytest.fixture
def config(music: Path, stub: StubSyncthing) -> Config:
    return Config(
        listen_host="127.0.0.1",
        listen_port=0,
        stale_after=900,
        syncthing=SyncthingConfig(url=stub.url, api_key_file=Path("/unused")),
        folders=(FolderConfig(id="music", path=music, patterns=("*.logicx",)),),
        users={n: hashlib.sha256(t.encode()).hexdigest() for n, t in TOKENS.items()},
        notify=NotifyConfig(None, None),
    )


@pytest.fixture
def app(config: Config, stub: StubSyncthing, clock: Clock, tmp_path: Path) -> Iterator[App]:
    client = SyncthingClient(stub.url, API_KEY, attempts=3, wait=wait_none())
    store = Store(tmp_path / "state.db")
    yield App(config, store, client, clock)
    store.close()


@pytest.fixture
def base_url(app: App) -> Iterator[tuple[str, int]]:
    srv = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield "127.0.0.1", srv.server_address[1]
    srv.shutdown()
    srv.server_close()


class Api:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port

    def call(self, method, path, body=None, user: str | None = "alex", *, raw=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=40)
        hdrs = dict(headers or {})
        if user is not None:
            hdrs["Authorization"] = f"Bearer {TOKENS.get(user, user)}"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            payload = json.loads(resp.read() or b"null")
            return resp.status, payload
        finally:
            conn.close()

    def get(self, path, user: str | None = "alex", **kw):
        return self.call("GET", path, user=user, **kw)

    def post(self, path, body=None, user: str | None = "alex", **kw):
        return self.call("POST", path, body, user, **kw)


@pytest.fixture
def api(base_url: tuple[str, int]) -> Api:
    return Api(*base_url)


@pytest.fixture
def wait_until() -> Callable[..., bool]:
    import time

    def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    return _wait

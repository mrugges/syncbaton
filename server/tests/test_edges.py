import socket
import threading

import pytest
from tenacity import wait_none

from syncbaton._http import http_request
from syncbaton.conflicts import ConflictScanner
from syncbaton.syncthing import SyncthingClient, SyncthingError
from syncbaton.tokens import add_user_hash

from .conftest import API_KEY, TOKENS


def test_unsupported_method_is_404_json(api):
    assert api.call("PATCH", "/info")[1] == {"error": "not_found"}


def test_head_request_gets_headers_only(base_url):
    with socket.create_connection(base_url, timeout=5) as sock:
        sock.sendall(b"HEAD /healthz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        reply = sock.recv(4096)
    assert reply.startswith(b"HTTP/1.1 404") or reply.startswith(b"HTTP/1.1 401")
    assert reply.endswith(b"\r\n\r\n")


def test_bad_content_length_is_400(base_url):
    request = (
        "POST /folders/music/projects/x/checkout HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {TOKENS['alex']}\r\nContent-Length: abc\r\n\r\n"
    )
    with socket.create_connection(base_url, timeout=5) as sock:
        sock.sendall(request.encode())
        reply = sock.recv(4096)
    assert reply.startswith(b"HTTP/1.1 400")


def test_unhandled_error_is_500(api, app, monkeypatch):
    def boom(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(app.locks, "snapshot", boom)
    assert api.get("/folders/music/projects") == (500, {"error": "internal"})


def test_status_without_my_id_is_an_error(stub):
    stub.status = {}
    client = SyncthingClient(stub.url, API_KEY, attempts=1, wait=wait_none())
    with pytest.raises(SyncthingError, match="myID"):
        client.my_id()


def test_unsupported_url_scheme():
    with pytest.raises(ValueError, match="unsupported URL"):
        http_request("GET", "ftp://example.com/x")


def test_add_user_hash_rejects_broken_toml():
    with pytest.raises(ValueError, match="invalid TOML"):
        add_user_hash("[users]\nsam = \n", "alex", "a" * 64)


def test_scanner_thread_keeps_running_after_a_failed_scan(app, music, wait_until):
    calls = []

    class Flaky(ConflictScanner):
        def run_once(self):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("disk went away")
            return

    scanner = Flaky(app.config.folders, app.store, interval=0.02)
    scanner.start()
    assert wait_until(lambda: len(calls) >= 3)
    scanner.stop()
    n = len(calls)
    threading.Event().wait(0.1)
    assert len(calls) == n


def test_scanner_thread_reports_conflicts(app, music, wait_until):
    (music / "Song B.logicx" / "a.sync-conflict-20260102-030405-AAA.wav").write_text("x")
    scanner = ConflictScanner(app.config.folders, app.store, interval=0.02)
    scanner.start()
    assert wait_until(lambda: app.store.latest_event_id() == 1)
    scanner.stop()


def test_non_json_answer_is_an_error(stub):
    stub.raw_body = b"<html>proxy error</html>"
    client = SyncthingClient(stub.url, API_KEY, attempts=1, wait=wait_none())
    with pytest.raises(SyncthingError, match="not JSON"):
        client.my_id()

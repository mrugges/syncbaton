import socket
import threading

from syncbaton.server import Handler, make_server
from syncbaton.syncthing import SyncthingClient

from .conftest import TOKENS


def exchange(addr, request: bytes) -> bytes:
    """Send ``request`` and read until the server closes the connection."""
    with socket.create_connection(addr, timeout=5) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                return b"".join(chunks)
            chunks.append(data)


def test_chunked_request_is_rejected_and_connection_closed(base_url):
    request = (
        "POST /folders/music/projects/Song%20A.logicx/checkout HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {TOKENS['alex']}\r\nTransfer-Encoding: chunked\r\n\r\n"
        "0\r\n\r\nGET /healthz HTTP/1.1\r\nHost: x\r\n\r\n"
    )
    reply = exchange(base_url, request.encode())
    assert reply.startswith(b"HTTP/1.1 400")
    assert reply.count(b"HTTP/1.1") == 1  # the bytes after the chunked body were never parsed


def test_idle_connections_are_dropped(app, monkeypatch):
    monkeypatch.setattr(Handler, "timeout", 0.3)
    srv = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        assert exchange(srv.server_address[:2], b"") == b""
    finally:
        srv.shutdown()
        srv.server_close()


def test_device_id_lookup_does_not_serialize_requests(app):
    entered = threading.Semaphore(0)
    release = threading.Event()

    class Hung(SyncthingClient):
        def my_id(self):
            entered.release()
            release.wait(10)
            return "ID"

    app.syncthing = Hung("http://127.0.0.1:1", "k")
    results = []
    threads = [threading.Thread(target=lambda: results.append(app.device_id())) for _ in range(2)]
    for t in threads:
        t.start()
    try:
        assert entered.acquire(timeout=5)
        assert entered.acquire(timeout=5)  # the second caller got in while the first was blocked
    finally:
        release.set()
    for t in threads:
        t.join(5)
    assert results == ["ID", "ID"]

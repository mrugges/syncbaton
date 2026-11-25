import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from syncbaton.config import NotifyConfig
from syncbaton.notify import Notifier, describe

EVENT = {
    "id": 1,
    "at": "t",
    "type": "checkout",
    "folder": "music",
    "project": "Song A.logicx",
    "user": "alex",
}


@pytest.fixture
def sink():
    received = []
    status = {"code": 200}

    class H(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, dict(self.headers), body))
            self.send_response(status["code"])
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", received, status
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize(
    ("event", "text"),
    [
        (EVENT, "alex checked out Song A.logicx (music)"),
        (
            {**EVENT, "type": "checkin", "note": "new bass"},
            "alex checked in Song A.logicx (music): new bass",
        ),
        ({**EVENT, "type": "checkin", "note": ""}, "alex checked in Song A.logicx (music)"),
        (
            {**EVENT, "type": "take_over", "previous_holder": "sam"},
            "alex took over Song A.logicx (music) from sam",
        ),
        (
            {**EVENT, "type": "conflict", "user": None, "path": "Song A.logicx/x.sync-conflict-1"},
            "Sync conflict in Song A.logicx (music): Song A.logicx/x.sync-conflict-1",
        ),
    ],
)
def test_describe(event, text):
    assert describe(event) == text


def test_ntfy_and_webhook(sink):
    url, received, _ = sink
    n = Notifier(NotifyConfig(ntfy_url=f"{url}/topic", webhook_url=f"{url}/hook"))
    n.notify(EVENT)
    n.flush()
    by_path = {p: (h, b) for p, h, b in received}
    headers, body = by_path["/topic"]
    assert body.decode() == "alex checked out Song A.logicx (music)"
    assert headers["Title"] == "syncbaton: checkout"
    headers, body = by_path["/hook"]
    assert json.loads(body) == EVENT
    assert headers["Content-Type"] == "application/json"
    n.close()


def test_unicode_project_names_do_not_break_headers(sink):
    url, received, _ = sink
    n = Notifier(NotifyConfig(ntfy_url=f"{url}/t", webhook_url=None))
    n.notify({**EVENT, "project": "Sång Ö.logicx"})
    n.flush()
    assert received[0][2].decode() == "alex checked out Sång Ö.logicx (music)"
    n.close()


def test_failures_are_logged_and_do_not_stop_later_deliveries(sink, caplog):
    url, received, status = sink
    status["code"] = 500
    n = Notifier(NotifyConfig(ntfy_url=f"{url}/t", webhook_url="http://127.0.0.1:1/hook"))
    with caplog.at_level("WARNING", logger="syncbaton.notify"):
        n.notify(EVENT)
        n.flush()
    assert "ntfy delivery failed" in caplog.text
    assert "webhook delivery failed" in caplog.text
    status["code"] = 200
    n.notify(EVENT)
    n.flush()
    assert len(received) == 2
    n.close()


def test_no_targets_configured_is_a_no_op():
    n = Notifier(NotifyConfig(None, None))
    n.notify(EVENT)
    n.flush()
    n.close()

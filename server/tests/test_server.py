import threading
import time
from urllib.parse import quote

import pytest

from .conftest import DEVICE_ID

A = "Song%20A.logicx"
BASE = f"/folders/music/projects/{A}"


def test_healthz_needs_no_auth(api):
    assert api.get("/healthz", user=None) == (200, {"ok": True})


@pytest.mark.parametrize("user", [None, "not-a-token"])
def test_auth_required_everywhere_else(api, user):
    for method, path in [
        ("GET", "/info"),
        ("GET", "/folders/music/projects"),
        ("POST", f"{BASE}/checkout"),
        ("GET", "/events"),
        ("GET", "/nope"),
    ]:
        status, body = api.call(method, path, user=user)
        assert (status, body) == (401, {"error": "unauthorized"})


def test_non_bearer_scheme_is_unauthorized(api):
    status, _ = api.get("/info", user=None, headers={"Authorization": "Basic dG9rZW4tYWxleA=="})
    assert status == 401


def test_info(api):
    status, body = api.get("/info", user="sam")
    assert status == 200
    assert body == {
        "user": "sam",
        "device_id": DEVICE_ID,
        "folders": [{"id": "music"}],
        "stale_after_seconds": 900,
    }


def test_info_device_id_null_when_syncthing_down(api, stub):
    stub.always_status = 500
    status, body = api.get("/info")
    assert (status, body["device_id"]) == (200, None)


def test_project_listing(api):
    status, body = api.get("/folders/music/projects")
    assert status == 200
    assert body["projects"] == [
        {
            "name": n,
            "holder": None,
            "since": None,
            "heartbeat": None,
            "stale": False,
            "last_checkin": None,
        }
        for n in ("Song A.logicx", "Song B.logicx")
    ]


def test_unknown_folder_and_routes_are_404(api):
    for method, path in [
        ("GET", "/folders/nope/projects"),
        ("POST", "/folders/nope/projects/x/checkout"),
        ("GET", "/nothing"),
        ("GET", "/folders/music/projects/"),
        ("POST", f"{BASE}/explode"),
        ("GET", f"{BASE}/checkout"),
        ("DELETE", "/info"),
        ("PUT", f"{BASE}/checkout"),
    ]:
        status, body = api.call(method, path)
        assert (status, body["error"]) == (404, "not_found"), path


@pytest.mark.parametrize(
    "name",
    [
        "..",
        "%2e%2e",
        "a%2Fb",
        "..%2F..%2Fetc",
        "nope.logicx",
        "notes.txt",
        quote("/etc/passwd", safe=""),
        "Song%20A.logicx%2FAlternatives",
        "Song%00A.logicx",
    ],
)
def test_bad_project_names_are_404_for_every_action(api, name):
    for action in ("checkout", "heartbeat", "checkin"):
        status, body = api.post(f"/folders/music/projects/{name}/{action}")
        assert (status, body["error"]) == (404, "not_found"), (name, action)


def test_checkout_flow(api, clock):
    status, p = api.post(f"{BASE}/checkout")
    assert status == 200
    assert p["holder"] == "alex"
    assert p["since"] == p["heartbeat"] == "2027-01-15T08:00:00Z"
    assert p["stale"] is False

    status, listing = api.get("/folders/music/projects")
    assert listing["projects"][0]["holder"] == "alex"

    status, body = api.post(f"{BASE}/checkout", user="sam")
    assert (status, body) == (
        409,
        {"error": "held", "holder": "alex", "since": "2027-01-15T08:00:00Z"},
    )
    status, body = api.post(f"{BASE}/checkout", {"take_over": True}, user="sam")
    assert (status, body["error"], body["holder"]) == (409, "not_stale", "alex")

    clock.now += 901
    status, listing = api.get("/folders/music/projects")
    assert listing["projects"][0]["stale"] is True
    status, p = api.post(f"{BASE}/checkout", {"take_over": True}, user="sam")
    assert (status, p["holder"], p["stale"]) == (200, "sam", False)


def test_recheckout_by_holder_is_ok_and_refreshes_heartbeat(api, clock):
    api.post(f"{BASE}/checkout")
    clock.now += 60
    status, p = api.post(f"{BASE}/checkout")
    assert status == 200
    assert (p["since"], p["heartbeat"]) == ("2027-01-15T08:00:00Z", "2027-01-15T08:01:00Z")


def test_heartbeat(api, clock):
    assert api.post(f"{BASE}/heartbeat")[1] == {"error": "not_holder"}
    api.post(f"{BASE}/checkout")
    clock.now += 120
    status, p = api.post(f"{BASE}/heartbeat")
    assert (status, p["heartbeat"]) == (200, "2027-01-15T08:02:00Z")
    assert api.post(f"{BASE}/heartbeat", user="sam") == (409, {"error": "not_holder"})


def test_checkin_when_synced(api, stub):
    api.post(f"{BASE}/checkout")
    stub.set_need(rest=["Other.logicx/x", "Song A.logicx.bak"])
    status, p = api.post(f"{BASE}/checkin", {"note": "new bass"})
    assert status == 200
    assert (p["holder"], p["since"], p["heartbeat"], p["stale"]) == (None, None, None, False)
    assert p["last_checkin"] == {"user": "alex", "at": "2027-01-15T08:00:00Z", "note": "new bass"}
    assert ("/rest/db/need?folder=music", "stub-api-key") in stub.requests
    _, listing = api.get("/folders/music/projects")
    assert listing["projects"][0]["last_checkin"]["user"] == "alex"


def test_checkin_still_syncing_lists_at_most_20_files(api, stub):
    api.post(f"{BASE}/checkout")
    stub.set_need(
        progress=["Song A.logicx/Alternatives/big.wav"],
        queued=[f"Song A.logicx/Audio/{i}.wav" for i in range(30)],
        rest=["Song B.logicx/x"],
    )
    status, body = api.post(f"{BASE}/checkin", {"note": ""})
    assert (status, body["error"]) == (409, "still_syncing")
    assert len(body["files"]) == 20
    assert body["files"][0] == "Song A.logicx/Alternatives/big.wav"
    assert all(f.startswith("Song A.logicx/") for f in body["files"])
    _, listing = api.get("/folders/music/projects")
    assert listing["projects"][0]["holder"] == "alex"


def test_checkin_project_itself_needed(api, stub):
    api.post(f"{BASE}/checkout")
    stub.set_need(rest=["Song A.logicx"])
    assert api.post(f"{BASE}/checkin", {"note": ""})[1]["files"] == ["Song A.logicx"]


def test_checkin_refused_when_syncthing_unreachable(api, stub):
    api.post(f"{BASE}/checkout")
    stub.always_status = 500
    status, body = api.post(f"{BASE}/checkin", {"note": ""})
    assert (status, body["error"]) == (503, "syncthing_unavailable")
    stub.always_status = None
    _, listing = api.get("/folders/music/projects")
    assert listing["projects"][0]["holder"] == "alex"


def test_checkin_by_non_holder_does_not_ask_syncthing(api, stub):
    api.post(f"{BASE}/checkout")
    assert api.post(f"{BASE}/checkin", {"note": ""}, user="sam") == (409, {"error": "not_holder"})
    assert stub.requests == []


@pytest.mark.parametrize(
    ("path", "raw", "body"),
    [
        (f"{BASE}/checkin", b"{not json", None),
        (f"{BASE}/checkin", None, ["list"]),
        (f"{BASE}/checkin", None, {"note": 5}),
        (f"{BASE}/checkout", b"{not json", None),
        (f"{BASE}/checkout", None, {"take_over": "yes"}),
        (f"{BASE}/checkout", None, [1]),
    ],
)
def test_bad_bodies_are_400(api, path, raw, body):
    api.post(f"{BASE}/checkout")
    status, payload = api.post(path, body, raw=raw)
    assert (status, payload["error"]) == (400, "bad_request")


def test_empty_bodies_are_accepted(api):
    assert api.post(f"{BASE}/checkout")[0] == 200
    assert api.post(f"{BASE}/checkin")[0] == 200


def test_oversized_body_is_400(api):
    status, body = api.post(f"{BASE}/checkout", raw=b"x" * 200_000)
    assert (status, body["error"]) == (400, "bad_request")


def test_events_since_and_default(api):
    assert api.get("/events?wait=0") == (200, {"events": [], "last_id": 0})
    api.post(f"{BASE}/checkout")
    status, body = api.get("/events?since=0&wait=0")
    assert status == 200
    assert body["last_id"] == 1
    event = body["events"][0]
    assert event == {
        "id": 1,
        "at": "2027-01-15T08:00:00Z",
        "type": "checkout",
        "folder": "music",
        "project": "Song A.logicx",
        "user": "alex",
    }
    assert api.get("/events?wait=0") == (200, {"events": [], "last_id": 1})
    api.post(f"{BASE}/checkin", {"note": "n"})
    _, body = api.get("/events?since=1&wait=0")
    assert body["events"][0]["note"] == "n"
    assert body["last_id"] == 2


@pytest.mark.parametrize("query", ["since=abc", "since=-1", "wait=x", "wait=-2", "since=1.5"])
def test_bad_event_query_is_400(api, query):
    assert api.get(f"/events?{query}")[0] == 400


def test_events_wait_is_capped_at_60_not_rejected(api, app, monkeypatch):
    waits = []
    real = app.store.wait_for_events

    def spy(since, timeout):
        waits.append(timeout)
        return real(since, timeout)

    monkeypatch.setattr(app.store, "wait_for_events", spy)
    app.store.add_event("checkout", "music", "x", "alex", "2027-01-15T08:00:00Z")
    assert api.get("/events?since=0&wait=999")[0] == 200
    assert api.get("/events?since=0&wait=7")[0] == 200
    assert waits == [60, 7]


def test_long_poll_wakes_promptly(api, app, monkeypatch):
    blocked = threading.Event()
    real = app.store.wait_for_events

    def spy(since, timeout):
        blocked.set()
        return real(since, timeout)

    monkeypatch.setattr(app.store, "wait_for_events", spy)
    got = {}

    def poll():
        t0 = time.monotonic()
        got["res"] = api.get("/events?wait=30", user="sam")
        got["took"] = time.monotonic() - t0

    t = threading.Thread(target=poll)
    t.start()
    assert blocked.wait(5)
    api.post(f"{BASE}/checkout")
    t.join(10)
    assert not t.is_alive()
    assert got["took"] < 5
    status, body = got["res"]
    assert (status, body["events"][0]["type"]) == (200, "checkout")


def test_long_poll_times_out_with_empty_list(api):
    t0 = time.monotonic()
    assert api.get("/events?wait=1") == (200, {"events": [], "last_id": 0})
    assert 0.9 < time.monotonic() - t0 < 5


def test_take_over_event_has_previous_holder(api, clock):
    api.post(f"{BASE}/checkout")
    clock.now += 1000
    api.post(f"{BASE}/checkout", {"take_over": True}, user="sam")
    _, body = api.get("/events?since=1&wait=0")
    assert body["events"][0]["type"] == "take_over"
    assert body["events"][0]["previous_holder"] == "alex"
    assert body["events"][0]["user"] == "sam"


def test_conflict_events_via_scanner(api, app, music, wait_until):
    from syncbaton.conflicts import ConflictScanner

    rel = "Song A.logicx/Alternatives/x.sync-conflict-20260102-030405-AAA.wav"
    (music / rel).write_text("x")
    scanner = ConflictScanner(app.config.folders, app.store, lambda: app.clock())
    scanner.run_once()
    scanner.run_once()
    _, body = api.get("/events?since=0&wait=0")
    assert body["events"] == [
        {
            "id": 1,
            "at": "2027-01-15T08:00:00Z",
            "type": "conflict",
            "folder": "music",
            "project": "Song A.logicx",
            "user": None,
            "path": rel,
        }
    ]
    other = ConflictScanner(app.config.folders, app.store, lambda: app.clock())
    other.run_once()
    assert app.store.latest_event_id() == 1


def test_log_lines_have_user_and_never_the_token(api, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="syncbaton.server"):
        api.get("/info")
        api.get("/info", user="bad-token-value")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "GET /info 200 user=alex" in text
    assert "GET /info 401 user=-" in text
    assert "token-alex" not in text
    assert "bad-token-value" not in text


def test_int_params_beyond_64_bits_are_400(api):
    assert api.get(f"/events?since={2**63}&wait=0")[0] == 400
    assert api.get(f"/events?wait={2**63}")[0] == 400
    assert api.get(f"/events?since={2**63 - 1}&wait=0") == (200, {"events": [], "last_id": 0})

import threading
import time

import pytest

from syncbaton.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def add(store, kind="checkout", **kw):
    return store.add_event(
        kind, "music", "A.logicx", kw.pop("user", "alex"), "2026-01-01T00:00:00Z", **kw
    )


def test_event_ids_increase_and_only_relevant_extras_appear(store):
    s = store
    a = add(s)
    b = add(s, "checkin", note="")
    c = add(s, "take_over", previous_holder="sam")
    d = s.add_event(
        "conflict", "music", "A.logicx", None, "2026-01-01T00:00:00Z", path="A.logicx/x"
    )
    assert [e["id"] for e in (a, b, c, d)] == [1, 2, 3, 4]
    assert set(a) == {"id", "at", "type", "folder", "project", "user"}
    assert b["note"] == ""
    assert c["previous_holder"] == "sam"
    assert d["user"] is None
    assert d["path"] == "A.logicx/x"
    assert s.events_after(2) == [c, d]
    assert s.latest_event_id() == 4


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "state.db"
    s = Store(path)
    add(s)
    assert s.mark_conflict_reported("music", "A.logicx/x") is True
    s.close()
    s2 = Store(path)
    assert s2.latest_event_id() == 1
    assert s2.mark_conflict_reported("music", "A.logicx/x") is False
    assert s2.mark_conflict_reported("music", "A.logicx/y") is True
    s2.close()


def test_long_poll_returns_immediately_when_events_exist(store):
    s = store
    add(s)
    t0 = time.monotonic()
    assert len(s.wait_for_events(0, timeout=30)) == 1
    assert time.monotonic() - t0 < 1


def test_long_poll_times_out_empty(store):
    s = store
    t0 = time.monotonic()
    assert s.wait_for_events(0, timeout=0.2) == []
    assert 0.15 < time.monotonic() - t0 < 2


def test_long_poll_wakes_when_event_added(store):
    s = store
    result = []
    t = threading.Thread(target=lambda: result.append(s.wait_for_events(0, timeout=30)))
    t.start()
    time.sleep(0.2)
    t0 = time.monotonic()
    add(s)
    t.join(5)
    assert not t.is_alive()
    assert time.monotonic() - t0 < 1
    assert [e["id"] for e in result[0]] == [1]


def test_rolled_back_transaction_leaves_no_event(store):
    s = store
    try:
        with s.transaction():
            add(s)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert s.latest_event_id() == 0
    assert s.events_after(0) == []


def test_listeners_see_each_event(store):
    s = store
    seen = []
    s.add_listener(seen.append)
    e = add(s)
    assert seen == [e]


def test_listeners_run_only_after_the_outermost_commit(store):
    seen = []
    store.add_listener(seen.append)
    with store.transaction():
        add(store)
        with store.transaction():
            add(store)
        assert seen == []
    assert [e["id"] for e in seen] == [1, 2]


def test_rolled_back_event_never_reaches_a_listener(store):
    seen = []
    store.add_listener(seen.append)

    def failing_transaction():
        with store.transaction():
            add(store)
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        failing_transaction()
    add(store)
    assert [e["id"] for e in seen] == [1]
    assert store.latest_event_id() == 1


def test_failed_commit_is_rolled_back_and_the_store_stays_usable(store):
    import sqlite3

    class FailsToCommit:
        def __init__(self, real):
            self.real = real

        def execute(self, sql, *args):
            if sql == "COMMIT":
                msg = "disk full"
                raise sqlite3.OperationalError(msg)
            return self.real.execute(sql, *args)

    seen = []
    store.add_listener(seen.append)
    real = store._conn
    store._conn = FailsToCommit(real)
    with pytest.raises(sqlite3.OperationalError):
        add(store)
    store._conn = real
    assert seen == []
    assert store.latest_event_id() == 0
    add(store)
    assert [e["id"] for e in seen] == [1]

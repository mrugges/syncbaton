import pytest
from hypothesis import given
from hypothesis import strategies as st
from tenacity import wait_none

from syncbaton.syncthing import SyncthingClient, SyncthingError, need_paths

from .conftest import API_KEY, DEVICE_ID


def client(stub, key=API_KEY, attempts=3):
    return SyncthingClient(stub.url, key, attempts=attempts, wait=wait_none())


def test_my_id_sends_api_key(stub):
    assert client(stub).my_id() == DEVICE_ID
    assert stub.requests == [("/rest/system/status", API_KEY)]


def test_need_collects_progress_queued_and_rest(stub):
    stub.set_need(progress=["a"], queued=["b", "c"], rest=["d"])
    assert client(stub).need("music") == ["a", "b", "c", "d"]
    assert stub.requests[-1][0] == "/rest/db/need?folder=music"


def test_need_tolerates_nulls_and_odd_entries():
    assert need_paths({"progress": None, "queued": [{"name": "x"}, {"size": 1}, "str"]}) == ["x"]
    assert need_paths({}) == []


NAMES = st.lists(st.text(min_size=1, max_size=8), max_size=5)


@given(NAMES, NAMES, NAMES)
def test_property_need_paths_keeps_every_named_entry_in_order(progress, queued, rest):
    def entries(names):
        return [{"name": n} for n in names]

    body = {"progress": entries(progress), "queued": entries(queued), "rest": entries(rest)}
    assert need_paths(body) == progress + queued + rest


def test_retries_server_errors_then_succeeds(stub):
    stub.fail_next = 2
    assert client(stub).my_id() == DEVICE_ID
    assert len(stub.requests) == 3


def test_gives_up_after_attempts(stub):
    stub.always_status = 503
    with pytest.raises(SyncthingError):
        client(stub, attempts=3).need("music")
    assert len(stub.requests) == 3


def test_client_errors_are_not_retried_and_do_not_leak_the_key(stub):
    with pytest.raises(SyncthingError, match="403") as err:
        client(stub, key="wrong-key").my_id()
    assert "wrong-key" not in str(err.value)
    assert len(stub.requests) == 1


def test_unreachable_is_retried_then_error():
    c = SyncthingClient("http://127.0.0.1:1", "k", attempts=2, wait=wait_none(), timeout=1)
    with pytest.raises(SyncthingError, match="reach"):
        c.my_id()


def test_invalid_json_is_an_error(stub):
    stub.need = None  # served as JSON null
    with pytest.raises(SyncthingError, match="unexpected"):
        client(stub).need("music")


def test_need_pages_through_every_entry(stub):
    stub.set_need(progress=["p1"], queued=["q1", "q2"], rest=[f"r{i}" for i in range(22)])
    names = SyncthingClient(stub.url, API_KEY, wait=wait_none(), page_size=10).need("music")
    assert names == ["p1", "q1", "q2"] + [f"r{i}" for i in range(22)]
    assert stub.pages == [(1, 10), (2, 10), (3, 10)]


def test_need_stops_after_an_exact_multiple_with_one_empty_page(stub):
    stub.set_need(rest=[f"r{i}" for i in range(20)])
    names = SyncthingClient(stub.url, API_KEY, wait=wait_none(), page_size=10).need("music")
    assert len(names) == 20
    assert stub.pages == [(1, 10), (2, 10), (3, 10)]


def test_need_gives_up_instead_of_looping_forever(stub):
    stub.set_need(rest=[f"r{i}" for i in range(50)])
    c = SyncthingClient(stub.url, API_KEY, wait=wait_none(), page_size=10, max_pages=3)
    with pytest.raises(SyncthingError, match="more than 30"):
        c.need("music")


def test_retries_stop_when_the_time_budget_is_spent(stub):
    import time

    from tenacity import wait_fixed

    stub.always_status = 503
    c = SyncthingClient(stub.url, API_KEY, attempts=1000, wait=wait_fixed(0.05), budget=0.5)
    t0 = time.monotonic()
    with pytest.raises(SyncthingError):
        c.need("music")
    assert time.monotonic() - t0 < 3
    assert 3 < len(stub.requests) < 1000

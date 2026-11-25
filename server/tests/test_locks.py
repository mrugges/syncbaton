import threading

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from syncbaton.locks import Held, LockManager, NotHolder, NotStale, StillSyncing
from syncbaton.store import Store

STALE = 900
USERS = ["alex", "sam", "kim"]


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def mgr(clock):
    manager = LockManager(Store(":memory:"), STALE, clock)
    yield manager
    manager.store.close()


def test_free_project_state(mgr):
    s = mgr.state("music", "A.logicx")
    assert (s.holder, s.since, s.heartbeat, s.last_checkin) == (None, None, None, None)


def test_checkout_then_held_by_other(mgr, clock):
    mgr.checkout("music", "A.logicx", "alex")
    clock.now += 5
    with pytest.raises(Held) as err:
        mgr.checkout("music", "A.logicx", "sam")
    assert err.value.holder == "alex"
    assert err.value.since == 1_800_000_000.0


def test_recheckout_refreshes_heartbeat_only(mgr, clock):
    first = mgr.checkout("music", "A.logicx", "alex")
    clock.now += 100
    again = mgr.checkout("music", "A.logicx", "alex")
    assert again.since == first.since
    assert again.heartbeat == first.heartbeat + 100
    assert [e["type"] for e in mgr.store.events_after(0)] == ["checkout"]


def test_take_over_requires_stale(mgr, clock):
    mgr.checkout("music", "A.logicx", "alex")
    clock.now += STALE
    with pytest.raises(NotStale):
        mgr.checkout("music", "A.logicx", "sam", take_over=True)
    clock.now += 1
    state = mgr.checkout("music", "A.logicx", "sam", take_over=True)
    assert state.holder == "sam"
    event = mgr.store.events_after(0)[-1]
    assert (event["type"], event["user"], event["previous_holder"]) == ("take_over", "sam", "alex")


def test_take_over_flag_on_free_project_is_plain_checkout(mgr):
    mgr.checkout("music", "A.logicx", "sam", take_over=True)
    assert mgr.store.events_after(0)[-1]["type"] == "checkout"


def test_heartbeat(mgr, clock):
    mgr.checkout("music", "A.logicx", "alex")
    clock.now += 60
    assert mgr.heartbeat("music", "A.logicx", "alex").heartbeat == clock.now
    with pytest.raises(NotHolder):
        mgr.heartbeat("music", "A.logicx", "sam")
    with pytest.raises(NotHolder):
        mgr.heartbeat("music", "free.logicx", "sam")


def test_checkin_releases_records_and_emits(mgr):
    mgr.checkout("music", "A.logicx", "alex")
    state = mgr.checkin("music", "A.logicx", "alex", "new bass", gate=lambda: None)
    assert state.holder is None
    assert state.last_checkin is not None
    assert (state.last_checkin.user, state.last_checkin.note) == ("alex", "new bass")
    event = mgr.store.events_after(0)[-1]
    assert (event["type"], event["note"]) == ("checkin", "new bass")
    mgr.checkout("music", "A.logicx", "sam")
    assert mgr.state("music", "A.logicx").last_checkin.user == "alex"


def test_checkin_gate_failure_keeps_lock_and_emits_nothing(mgr):
    mgr.checkout("music", "A.logicx", "alex")

    def gate():
        raise StillSyncing(["A.logicx/x"])

    with pytest.raises(StillSyncing):
        mgr.checkin("music", "A.logicx", "alex", "", gate=gate)
    assert mgr.state("music", "A.logicx").holder == "alex"
    assert len(mgr.store.events_after(0)) == 1


def test_gate_not_consulted_for_non_holder(mgr):
    called = []
    with pytest.raises(NotHolder):
        mgr.checkin("music", "A.logicx", "sam", "", gate=lambda: called.append(1))
    assert called == []


def test_checkin_aborts_if_lock_taken_over_while_gate_ran(mgr, clock):
    mgr.checkout("music", "A.logicx", "alex")

    def gate():
        clock.now += STALE + 1
        mgr.checkout("music", "A.logicx", "sam", take_over=True)

    with pytest.raises(NotHolder):
        mgr.checkin("music", "A.logicx", "alex", "late", gate=gate)
    assert mgr.state("music", "A.logicx").holder == "sam"


def test_folders_and_projects_are_independent(mgr):
    mgr.checkout("music", "A.logicx", "alex")
    mgr.checkout("music", "B.logicx", "sam")
    mgr.checkout("other", "A.logicx", "kim")
    snap = mgr.snapshot("music", ["A.logicx", "B.logicx", "C.logicx"])
    assert [snap[n].holder for n in ("A.logicx", "B.logicx", "C.logicx")] == ["alex", "sam", None]


def test_concurrent_checkouts_have_one_winner(tmp_path):
    mgr = LockManager(Store(tmp_path / "s.db"), STALE, Clock())
    results = []
    barrier = threading.Barrier(8)

    def worker(user):
        barrier.wait()
        try:
            mgr.checkout("music", "A.logicx", user)
            results.append(("ok", user))
        except Held:
            results.append(("held", user))

    threads = [threading.Thread(target=worker, args=(f"u{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [u for r, u in results if r == "ok"]
    assert len(winners) == 1
    assert mgr.state("music", "A.logicx").holder == winners[0]
    assert len(mgr.store.events_after(0)) == 1
    mgr.store.close()


class LockMachine(RuleBasedStateMachine):
    """Random interleavings of the four operations by up to three users on two projects."""

    PROJECTS = ["A.logicx", "B.logicx"]

    def __init__(self):
        super().__init__()
        self.clock = Clock()
        self.mgr = LockManager(Store(":memory:"), STALE, self.clock)
        self.sync_ok = True
        self.prev = {p: self.mgr.state("music", p) for p in self.PROJECTS}

    def teardown(self):
        self.mgr.store.close()

    def age(self, state):
        assert state.heartbeat is not None
        return self.clock.now - state.heartbeat

    def gate(self):
        if not self.sync_ok:
            raise StillSyncing(["x"])

    @rule(seconds=st.integers(0, 3 * STALE))
    def advance(self, seconds):
        self.clock.now += seconds

    @rule(ok=st.booleans())
    def set_sync(self, ok):
        self.sync_ok = ok

    @rule(project=st.sampled_from(PROJECTS), user=st.sampled_from(USERS), take_over=st.booleans())
    def checkout(self, project, user, take_over):
        before = self.mgr.state("music", project)
        events = len(self.mgr.store.events_after(0))
        try:
            after = self.mgr.checkout("music", project, user, take_over=take_over)
        except NotStale:
            assert take_over
            assert before.holder not in (None, user)
            assert self.age(before) <= STALE
            assert self.mgr.state("music", project) == before
            return
        except Held:
            assert not take_over
            assert before.holder not in (None, user)
            assert self.mgr.state("music", project) == before
            return
        assert after.holder == user
        new_events = self.mgr.store.events_after(events)
        if before.holder == user:
            # re-checkout by the holder is idempotent apart from the heartbeat
            assert after.since == before.since
            assert after.heartbeat == self.clock.now
            assert new_events == []
        elif before.holder is None:
            assert [e["type"] for e in new_events] == ["checkout"]
        else:
            # a take-over only ever succeeds against a stale heartbeat
            assert take_over
            assert self.age(before) > STALE
            assert [e["type"] for e in new_events] == ["take_over"]
            assert new_events[0]["previous_holder"] == before.holder

    @rule(project=st.sampled_from(PROJECTS), user=st.sampled_from(USERS))
    def heartbeat(self, project, user):
        before = self.mgr.state("music", project)
        try:
            after = self.mgr.heartbeat("music", project, user)
        except NotHolder:
            assert before.holder != user
            assert self.mgr.state("music", project) == before
            return
        assert before.holder == user
        assert (after.holder, after.since, after.heartbeat) == (user, before.since, self.clock.now)

    @rule(project=st.sampled_from(PROJECTS), user=st.sampled_from(USERS))
    def checkin(self, project, user):
        before = self.mgr.state("music", project)
        events = len(self.mgr.store.events_after(0))
        try:
            after = self.mgr.checkin("music", project, user, "n", gate=self.gate)
        except NotHolder:
            assert before.holder != user
            assert self.mgr.state("music", project) == before
            assert len(self.mgr.store.events_after(0)) == events
            return
        except StillSyncing:
            assert not self.sync_ok
            assert self.mgr.state("music", project) == before
            return
        assert before.holder == user
        assert self.sync_ok
        assert after.holder is None
        assert after.last_checkin is not None
        assert after.last_checkin.user == user

    @invariant()
    def holder_changes_only_by_legal_transitions(self):
        for p in self.PROJECTS:
            old, new = self.prev[p], self.mgr.state("music", p)
            if old.holder != new.holder:
                freed = new.holder is None
                took_free = old.holder is None
                stale_takeover = (
                    old.holder is not None and new.holder is not None and self.age(old) > STALE
                )
                assert freed or took_free or stale_takeover
            self.prev[p] = new


LockMachine.TestCase.settings = settings(max_examples=200, stateful_step_count=40, deadline=None)
TestLockMachine = LockMachine.TestCase

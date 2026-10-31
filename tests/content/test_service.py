"""ContentSync: when syncs run, what they report, and what they ignore."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lightfall.content.service import CONTENT_SUBJECT, ContentSync
from lightfall.content.store import ContentStore
from lightfall.content.sync import ScopeSyncResult, SyncResult
from tests.ui.preferences.test_scoped_backend import Deferred, sync_run

BR = "branch:7.0.1.1"
CHAIN = ("facility", "beamline:7.0.1", BR, "user:u1")


class FakeIpc(QObject):
    sigConnectionChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.handlers = {}

    def subscribe(self, subject, cb, main_thread=False):
        self.handlers[subject] = cb

    def unsubscribe(self, subject):
        self.handlers.pop(subject, None)

    def fire(self, data):
        self.handlers[CONTENT_SUBJECT](CONTENT_SUBJECT, data, None)


class FakeSync:
    def __init__(self):
        self.calls = []
        self.prunes = []
        self.result = None  # None: nothing changed

    def __call__(self, client, store, bind, chain, *, prune=False):
        self.calls.append((bind, tuple(chain)))
        self.prunes.append(prune)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or SyncResult(bind, (ScopeSyncResult(BR, unchanged=True),), False)


def make(tmp_path, qapp, run=sync_run, poll_ms=0):
    ipc, fake = FakeIpc(), FakeSync()
    cs = ContentSync(client=object(), store=ContentStore(tmp_path / "root"), bind=lambda: BR,
                     run=run, ipc=lambda: ipc, debounce_ms=0, sync=fake,
                     poll_ms=poll_ms)
    got = []
    cs.synced.connect(got.append)
    return cs, ipc, fake, got


def test_start_syncs_and_subscribes(tmp_path, qapp):
    cs, ipc, fake, _ = make(tmp_path, qapp)
    cs.start(CHAIN)
    assert fake.calls == [(BR, CHAIN)]
    assert CONTENT_SUBJECT in ipc.handlers and cs.chain == CHAIN
    cs.stop()
    assert CONTENT_SUBJECT not in ipc.handlers


def test_synced_emitted_only_on_change(tmp_path, qapp):
    cs, ipc, fake, got = make(tmp_path, qapp)
    cs.start(CHAIN)
    assert got == []
    fake.result = SyncResult(BR, (ScopeSyncResult(BR, changed=(("agent", "a"),)),), False)
    cs.refresh()
    assert got == [fake.result]


def test_event_for_other_scope_ignored(tmp_path, qapp):
    cs, ipc, fake, _ = make(tmp_path, qapp)
    cs.start(CHAIN)
    ipc.fire({"scope": "branch:12.0.2.1", "type": "agent", "name": "a", "revision": 1})
    ipc.fire({"scope": "user:u1"})
    ipc.fire("not a dict")
    assert len(fake.calls) == 1
    ipc.fire({"scope": BR, "type": "agent", "name": "a", "revision": 1})
    assert len(fake.calls) == 2


def test_event_during_sync_runs_once_more(tmp_path, qapp):
    deferred = Deferred()
    cs, ipc, fake, _ = make(tmp_path, qapp, run=deferred)
    cs.start(CHAIN)
    for _ in range(3):
        ipc.fire({"scope": BR})
    assert len(deferred.jobs) == 1
    fn, ok, _err = deferred.jobs.pop(0)
    ok(fn())
    assert len(deferred.jobs) == 1  # exactly one more
    fn, ok, _err = deferred.jobs.pop(0)
    ok(fn())
    assert deferred.jobs == [] and len(fake.calls) == 2


def test_ipc_reconnect_refreshes(tmp_path, qapp):
    cs, ipc, fake, _ = make(tmp_path, qapp)
    cs.start(CHAIN)
    ipc.sigConnectionChanged.emit(True)
    assert len(fake.calls) == 2


def test_stop_drops_inflight_result(tmp_path, qapp):
    deferred = Deferred()
    cs, ipc, fake, got = make(tmp_path, qapp, run=deferred)
    fake.result = SyncResult(BR, (ScopeSyncResult(BR, changed=(("agent", "a"),)),), False)
    cs.start(CHAIN)
    cs.stop()
    fn, ok, _err = deferred.jobs.pop(0)
    ok(fn())
    assert got == [] and cs.chain == ()
    cs.refresh()  # stopped: nothing runs
    assert deferred.jobs == []


def test_transport_failure_marks_offline_and_keeps_copy(tmp_path, qapp):
    cs, ipc, fake, got = make(tmp_path, qapp)
    fake.result = SyncResult(BR, (), offline=True)
    cs.start(CHAIN)
    assert cs.offline and got == []
    fake.result = RuntimeError("worker blew up")
    cs.refresh()  # an unexpected error: logged, offline, no exception
    assert cs.offline
    fake.result = None
    cs.refresh()
    assert not cs.offline


def test_follow_scope_info(tmp_path, qapp):
    from lightfall.ui.preferences.manager import ScopeInfo

    cs, ipc, fake, _ = make(tmp_path, qapp)
    cs.follow(ScopeInfo("server", CHAIN))
    assert cs.chain == CHAIN and len(fake.calls) == 1
    cs.follow(ScopeInfo("server", CHAIN))  # same chain again (roles changed): one more sync
    assert len(fake.calls) == 2
    cs.follow(ScopeInfo("local", ("facility",)))
    assert cs.chain == () and CONTENT_SUBJECT not in ipc.handlers
    cs.follow(ScopeInfo("off"))
    assert len(fake.calls) == 2


def test_stop_then_start_never_runs_two_syncs_at_once(tmp_path, qapp):
    deferred = Deferred()
    cs, ipc, fake, got = make(tmp_path, qapp, run=deferred)
    cs.start(CHAIN)
    cs.stop()
    cs.start(CHAIN)  # logout and login again while the first sync is still running
    assert len(deferred.jobs) == 1
    fn, ok, _err = deferred.jobs.pop(0)
    ok(fn())  # the old session's result: dropped, then the new session syncs
    assert len(deferred.jobs) == 1
    fn, ok, _err = deferred.jobs.pop(0)
    ok(fn())
    assert deferred.jobs == []


def test_first_sync_of_the_process_prunes(tmp_path, qapp):
    cs, ipc, fake, _ = make(tmp_path, qapp)
    cs.start(CHAIN)
    cs.refresh()
    assert fake.prunes == [True, False]


def test_polls_while_started(tmp_path, qapp):
    cs, ipc, fake, _ = make(tmp_path, qapp, poll_ms=60_000)
    cs.start(CHAIN)
    assert cs._poll.isActive()
    cs._poll.timeout.emit()
    assert len(fake.calls) == 2
    cs.stop()
    assert not cs._poll.isActive()

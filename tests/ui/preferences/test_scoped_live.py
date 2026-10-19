"""ScopedBackend refreshes on server change events (notify-and-pull)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.journal import WriteJournal
from lightfall.ui.preferences.scoped_backend import SETTINGS_SUBJECT, ScopedBackend
from tests.ui.preferences.test_scoped_backend import FakeLocal, Server, sync_run


class FakeIPC(QObject):
    sigConnectionChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.subs = {}

    def subscribe(self, subject, callback, *, main_thread=True):
        self.subs[subject] = callback

    def unsubscribe(self, subject):
        self.subs.pop(subject, None)

    def send(self, data):
        self.subs[SETTINGS_SUBJECT](SETTINGS_SUBJECT, data, None)


class CountingServer(Server):
    def __init__(self):
        super().__init__()
        self.chains = 0

    def chain(self, bind):
        self.chains += 1
        return super().chain(bind)


def _backend(tmp_path, server, ipc, debounce_ms=0):
    return ScopedBackend(FakeLocal(), client=server, cache=SnapshotCache(tmp_path / "c"),
                         journal=WriteJournal(tmp_path / "j.json"), bind="beamline:7.0.1",
                         identity="u1", run=sync_run, notify=lambda *a: None, poll_ms=0,
                         ipc=lambda: ipc, debounce_ms=debounce_ms)


def test_event_for_chain_scope_pulls_new_value(qapp, tmp_path):
    server, ipc = CountingServer(), FakeIPC()
    backend = _backend(tmp_path, server, ipc)
    seen = []
    backend.changed.connect(lambda k, v: seen.append((k, v)))
    backend.activate()
    assert SETTINGS_SUBJECT in ipc.subs
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://new", 1)
    ipc.send({"scope": "beamline:7.0.1", "key": "tiled_url", "version": 1})
    assert backend.get("tiled_url") == "https://new"
    assert ("tiled_url", "https://new") in seen


def test_events_outside_the_chain_are_ignored(qapp, tmp_path):
    server, ipc = CountingServer(), FakeIPC()
    backend = _backend(tmp_path, server, ipc)
    backend.activate()
    before = server.chains
    ipc.send({"scope": "user:someone-else", "key": "theme", "version": 3})
    ipc.send({"scope": "branch:9.9.9.9", "key": "x", "version": 1})
    ipc.send("not a dict")
    assert server.chains == before


def test_burst_of_events_refreshes_once(qapp, qtbot, tmp_path):
    server, ipc = CountingServer(), FakeIPC()
    backend = _backend(tmp_path, server, ipc, debounce_ms=50)
    backend.activate()
    before = server.chains
    for v in range(20):
        ipc.send({"scope": "user:u1", "key": f"k{v}", "version": 1})
    qtbot.waitUntil(lambda: server.chains == before + 1, timeout=2000)
    qtbot.wait(150)
    assert server.chains == before + 1


def test_reconnect_refreshes_and_deactivate_unsubscribes(qapp, tmp_path):
    server, ipc = CountingServer(), FakeIPC()
    backend = _backend(tmp_path, server, ipc)
    backend.activate()
    before = server.chains
    ipc.sigConnectionChanged.emit(True)
    assert server.chains == before + 1
    backend.deactivate()
    assert SETTINGS_SUBJECT not in ipc.subs
    ipc.sigConnectionChanged.emit(True)
    assert server.chains == before + 1


def test_no_ipc_service_is_fine(qapp, tmp_path):
    backend = _backend(tmp_path, Server(), None)
    backend.activate()
    assert backend.active


def test_default_poll_is_one_minute(qapp, tmp_path):
    backend = ScopedBackend(FakeLocal(), client=Server(), cache=SnapshotCache(tmp_path / "c"),
                            journal=WriteJournal(tmp_path / "j.json"), bind="facility",
                            identity="u1", run=sync_run, notify=lambda *a: None,
                            ipc=lambda: None)
    assert backend._poll_ms == 60_000

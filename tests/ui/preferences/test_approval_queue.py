"""Scoped writes refused with key_pending wait in a per-identity queue and are sent
automatically once a facility admin approves the key."""
from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import QObject, Signal

from lightfall.scopes.approval_queue import ApprovalQueue
from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import ScopesError
from lightfall.scopes.journal import WriteJournal
from lightfall.scopes.store import MISSING
from lightfall.ui.preferences.scoped_backend import KEYS_SUBJECT, ScopedBackend
from tests.ui.preferences import test_dialog_scopes as tds
from tests.ui.preferences.test_dialog_scopes import env  # noqa: F401 - fixture
from tests.ui.preferences.test_scoped_backend import CHAIN, Deferred, FakeLocal, Server, sync_run

BL = "beamline:7.0.1"
KEY = "tiled_url"
APPROVED_SPEC = {"key": KEY, "type": "str", "scopes": ["user", "branch", "beamline", "facility"],
                 "operator_writable": True}


class PendingServer(Server):
    """Refuses writes of keys in ``pending`` above user scope until approved."""

    def __init__(self, pending=(), **kw):
        super().__init__(**kw)
        self.pending = set(pending)
        self.specs: tuple[dict, ...] = ()
        self.fail_with: ScopesError | None = None
        self.key_status = "pending"
        self.on_put = None  # called before answering a put (to interleave user actions)

    def chain(self, bind):
        base = super().chain(bind)
        return dataclasses.replace(base, specs=self.specs)

    def put_setting(self, scope, key, value, *, version=None, proposed_spec=None):
        if self.on_put is not None:
            self.on_put()
        if self.fail_with is not None:
            self.calls.append(("put", scope, key, value, version))
            raise self.fail_with
        if key in self.pending and not scope.startswith("user:"):
            self.calls.append(("put", scope, key, value, version))
            raise ScopesError("not approved", status=409, code="key_pending",
                              key_status=self.key_status)
        return super().put_setting(scope, key, value, version=version)

    def approve(self, key):
        self.pending.discard(key)
        self.specs = (*self.specs, APPROVED_SPEC)


class FakeIPC(QObject):
    sigConnectionChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.subs = {}

    def subscribe(self, subject, callback, *, main_thread=True):
        self.subs[subject] = callback

    def unsubscribe(self, subject):
        self.subs.pop(subject, None)

    def publish_key(self, key, status):
        self.subs[KEYS_SUBJECT](KEYS_SUBJECT, {"key": key, "status": status}, None)


@pytest.fixture
def make(tmp_path, qapp):
    def _make(server=None, ipc=None, run=sync_run):
        server = server or PendingServer(pending={KEY})
        ipc = ipc or FakeIPC()
        notes = []
        backend = ScopedBackend(
            FakeLocal(), client=server, cache=SnapshotCache(tmp_path / "cache"),
            journal=WriteJournal(tmp_path / "journal.json"), bind=BL, identity="u1",
            approvals=ApprovalQueue(tmp_path / "approvals.json"), run=run,
            notify=lambda t, m: notes.append((t, m)), poll_ms=0, ipc=lambda: ipc,
            debounce_ms=0)
        backend.activate()
        return backend, server, ipc, notes
    return _make


def test_pending_write_is_queued_and_notified(make, tmp_path):
    backend, server, _, notes = make()
    backend.set(KEY, "https://x", scope=BL)
    assert backend.awaiting_approval(KEY, BL) == "https://x"
    assert ApprovalQueue(tmp_path / "approvals.json").entries() == {(BL, KEY): "https://x"}
    assert notes[-1][1] == f"Saved once a facility admin approves {KEY}"
    assert (BL, KEY) not in server.rows


def test_approved_event_resends_and_clears(make):
    backend, server, ipc, _ = make()
    backend.set(KEY, "https://x", scope=BL)
    server.approve(KEY)
    ipc.publish_key(KEY, "approved")
    assert server.rows[(BL, KEY)] == ("https://x", 1)
    assert backend.awaiting_approval(KEY, BL) is MISSING
    assert backend.get(KEY) == "https://x"


def test_approval_seen_on_refresh_resends(make):
    backend, server, _, _ = make()
    backend.set(KEY, "https://x", scope=BL)
    server.approve(KEY)        # no event reaches this console
    backend.refresh()
    assert server.rows[(BL, KEY)] == ("https://x", 1)
    assert backend.awaiting_approval(KEY, BL) is MISSING


def test_rejected_drops_and_notifies(make, tmp_path):
    backend, server, ipc, notes = make()
    backend.set(KEY, "https://x", scope=BL)
    ipc.publish_key(KEY, "rejected")
    assert backend.awaiting_approval(KEY, BL) is MISSING
    assert ApprovalQueue(tmp_path / "approvals.json").entries() == {}
    assert notes[-1][1] == f"{KEY} was rejected by a facility admin; not saved"


def test_queue_survives_restart(make):
    backend, server, ipc, _ = make()
    backend.set(KEY, "https://x", scope=BL)
    backend.deactivate()
    assert KEYS_SUBJECT not in ipc.subs  # stopped listening on logout
    fresh_ipc = FakeIPC()
    again, _, _, _ = make(server=server, ipc=fresh_ipc)
    assert again.awaiting_approval(KEY, BL) == "https://x"
    server.approve(KEY)
    fresh_ipc.publish_key(KEY, "approved")
    assert server.rows[(BL, KEY)] == ("https://x", 1)


def test_latest_value_wins(make):
    backend, server, ipc, _ = make()
    backend.set(KEY, "https://old", scope=BL)
    backend.set(KEY, "https://new", scope=BL)
    assert backend.awaiting_approval(KEY, BL) == "https://new"
    server.approve(KEY)
    ipc.publish_key(KEY, "approved")
    assert server.rows[(BL, KEY)] == ("https://new", 1)
    assert [c[3] for c in server.calls if c[0] == "put"][-1] == "https://new"


def test_queued_remove_is_resent_as_delete(make, tmp_path):
    server = PendingServer()
    server.rows[(BL, KEY)] = ("https://x", 1)
    queue = ApprovalQueue(tmp_path / "approvals.json")
    queue.add(BL, KEY, None)
    backend, _, ipc, _ = make(server=server)
    assert backend.awaiting_approval(KEY, BL) is None
    ipc.publish_key(KEY, "approved")
    assert (BL, KEY) not in server.rows
    assert backend.awaiting_approval(KEY, BL) is MISSING


def test_resend_failures(make):
    backend, server, ipc, notes = make()
    backend.set(KEY, "https://x", scope=BL)
    # Still pending (the event raced the approval): stays queued.
    ipc.publish_key(KEY, "approved")
    assert backend.awaiting_approval(KEY, BL) == "https://x"
    # Transient: stays queued.
    server.fail_with = ScopesError("unreachable")
    ipc.publish_key(KEY, "approved")
    assert backend.awaiting_approval(KEY, BL) == "https://x"
    # Anything else: dropped and notified.
    server.fail_with = ScopesError("bad value", status=422)
    ipc.publish_key(KEY, "approved")
    assert backend.awaiting_approval(KEY, BL) is MISSING
    assert notes[-1][0] == "Setting not saved" and KEY in notes[-1][1]


def test_scope_outside_chain_waits(make, tmp_path):
    queue = ApprovalQueue(tmp_path / "approvals.json")
    queue.add("beamline:9.9.9", KEY, "https://far")
    server = PendingServer()
    backend, _, ipc, _ = make(server=server)
    ipc.publish_key(KEY, "approved")
    assert not [c for c in server.calls if c[1] == "beamline:9.9.9"]
    assert backend.awaiting_approval(KEY, "beamline:9.9.9") == "https://far"


def _puts(server, value):
    return [c for c in server.calls if c[0] == "put" and c[3] == value]


def test_event_and_refresh_send_once(make):
    run = Deferred()
    backend, server, ipc, _ = make(run=run)
    run.run_all()
    backend.set(KEY, "https://x", scope=BL)
    run.run_all()
    assert backend.awaiting_approval(KEY, BL) == "https://x"
    server.approve(KEY)
    ipc.publish_key(KEY, "approved")  # resend queued, not yet run
    backend.refresh()                 # this refresh also sees the approved spec
    run.run_all()
    assert len(_puts(server, "https://x")) == 2  # the refused write + one resend
    assert server.rows[(BL, KEY)] == ("https://x", 1)
    assert backend.awaiting_approval(KEY, BL) is MISSING


def test_direct_write_discards_queued_entry(make):
    backend, server, ipc, _ = make()
    backend.set(KEY, "https://old", scope=BL)
    server.pending.discard(KEY)       # approved, but this console saw nothing yet
    backend.set(KEY, "https://new", scope=BL)
    assert backend.awaiting_approval(KEY, BL) is MISSING
    server.specs = (APPROVED_SPEC,)
    ipc.publish_key(KEY, "approved")
    backend.refresh()
    assert len(_puts(server, "https://old")) == 1  # the refused write; never resent
    assert server.rows[(BL, KEY)][0] == "https://new"


@pytest.mark.parametrize("status, text", [
    ("rejected", "a facility admin rejected this setting"),
    ("unproposed", "no proposal was filed"),
])
def test_refused_without_proposal_keeps_old_notice(make, status, text):
    server = PendingServer(pending={KEY})
    server.key_status = status
    backend, _, _, notes = make(server=server)
    backend.set(KEY, "https://x", scope=BL)
    assert backend.awaiting_approval(KEY, BL) is MISSING
    assert notes[-1][0] == "Setting not saved" and text in notes[-1][1]


def test_revoked_drops_and_notifies(make):
    backend, server, ipc, notes = make()
    backend.set(KEY, "https://x", scope=BL)
    ipc.publish_key(KEY, "revoked")
    assert backend.awaiting_approval(KEY, BL) is MISSING
    assert notes[-1] == ("Setting not saved",
                         f"{KEY} was withdrawn by a facility admin; not saved")


def test_superseded_resend_failure_is_silent(make):
    backend, server, ipc, notes = make()
    backend.set(KEY, "https://old", scope=BL)
    before = len(notes)
    server.fail_with = ScopesError("bad value", status=422)
    # The user saves again while the resend is in flight.
    server.on_put = lambda: backend._approvals.add(BL, KEY, "https://new")
    ipc.publish_key(KEY, "approved")
    assert len(notes) == before
    assert backend.awaiting_approval(KEY, BL) == "https://new"


def test_queue_file_roundtrip(tmp_path):
    path = tmp_path / "q.json"
    q = ApprovalQueue(path)
    q.add(BL, KEY, {"a": 1})
    q.add("facility", "other", None)
    again = ApprovalQueue(path)
    assert again.entries() == {(BL, KEY): {"a": 1}, ("facility", "other"): None}
    assert again.keys() == {KEY, "other"}
    again.remove(BL, KEY)
    assert ApprovalQueue(path).entries() == {("facility", "other"): None}


def test_unreadable_queue_file_is_ignored(tmp_path):
    path = tmp_path / "q.json"
    path.write_text("{not json", encoding="utf-8")
    assert ApprovalQueue(path).entries() == {}


# Row note ------------------------------------------------------------------------


def test_row_shows_waiting_note(qtbot):
    from lightfall.ui.preferences.form.row import WAITING_TEXT
    from tests.ui.preferences.form.test_view import (
        BEAMLINE,
        COUNT,
        LOCAL,
        USER,
        FakePrefs,
        make_form,
    )

    prefs = FakePrefs(writable={USER, LOCAL, BEAMLINE})
    queued = {(COUNT, BEAMLINE): 12}
    prefs.awaiting_approval = lambda key, scope: queued.get((key, scope), MISSING)
    form, _, _ = make_form(qtbot, prefs, scope=BEAMLINE)
    row = form._rows[COUNT]
    assert row.waiting_note_shown()
    assert row._waiting.text() == WAITING_TEXT
    assert WAITING_TEXT == "Waiting for a facility admin; saves automatically once approved"
    assert row.editor.value() is None  # the layer value: not set until approval
    queued.clear()
    form.refresh_from_store()
    assert not row.waiting_note_shown()
    form.show_scope(USER)
    assert not form._rows[COUNT].waiting_note_shown()


def test_chain_constant_is_unchanged():
    # The fake server wraps the shared CHAIN; keep this module from mutating it.
    assert CHAIN.specs == ()


# Manager accessor and dialog refresh ----------------------------------------------


def test_manager_reports_awaiting_and_forwards_changes(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    import lightfall.ui.preferences.scoped_backend as sb
    from lightfall.ui.preferences.manager import PreferencesManager

    server = PendingServer(pending={KEY})

    def fake_build(local, *, identity, bind, user_preferences):
        return ScopedBackend(local, client=server, cache=SnapshotCache(tmp_path / "c"),
                             journal=WriteJournal(tmp_path / "j.json"), bind=bind,
                             identity=identity, user_preferences=user_preferences,
                             run=sync_run, notify=lambda *a: None, poll_ms=0,
                             ipc=lambda: None)

    monkeypatch.setattr(sb, "build_scoped_backend", fake_build)
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    try:
        assert manager.awaiting_approval(KEY, BL) is MISSING  # not logged in
        assert manager.activate_scopes("u1", BL)
        seen = []
        manager.awaiting_approval_changed.connect(seen.append)
        manager.set(KEY, "https://x", scope=BL)
        assert manager.awaiting_approval(KEY, BL) == "https://x"
        assert seen == [KEY]
        manager.deactivate_scopes()
        assert manager.awaiting_approval(KEY, BL) is MISSING
    finally:
        manager.deactivate_scopes()
        PreferencesManager.reset()


def test_dialog_row_follows_queue_changes(qtbot, env, monkeypatch):  # noqa: F811
    queued = {}

    class Prefs(type(env.prefs)):
        awaiting_approval_changed = Signal(str)

        def awaiting_approval(self, key, scope):
            return queued.get((key, scope), MISSING)

    prefs = Prefs()
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance",
        classmethod(lambda cls: prefs),
    )
    dialog = tds.make_dialog(qtbot, env)
    r = tds.row(dialog, "main", tds.COUNT)
    assert not r.waiting_note_shown()
    queued[(tds.COUNT, tds.USER)] = 5
    prefs.awaiting_approval_changed.emit(tds.COUNT)
    assert r.waiting_note_shown()
    dialog.reject()
    prefs.awaiting_approval_changed.emit(tds.COUNT)  # disconnected on close: no error

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_token_and_subject_match_server_encoding():
    from lightfall.logbook.live_updates import logbook_user_token, subject_for_user
    assert logbook_user_token("alice") == b"alice".hex()
    assert subject_for_user("alice") == "_lightfall.logbook.changed." + b"alice".hex()


def test_fetch_server_user_id_reads_logbook_endpoint(tmp_path, httpx_mock):
    from lightfall.logbook.client import fetch_server_user_id
    httpx_mock.add_response(
        url="http://lb.test/logbook",
        json={"id": "lb-1", "user_id": "kc-sub-123", "created_at": "2026-01-01T00:00:00+00:00"},
    )
    assert fetch_server_user_id("http://lb.test", user_id="alice") == "kc-sub-123"


def test_fetch_server_user_id_returns_none_on_error(httpx_mock):
    from lightfall.logbook.client import fetch_server_user_id
    httpx_mock.add_response(url="http://lb.test/logbook", status_code=500)
    assert fetch_server_user_id("http://lb.test", user_id="alice") is None


class _FakeIPC:
    def __init__(self):
        self.subscribed = []      # list of subjects
        self.unsubscribed = []    # list of subjects
        self.sigConnectionChanged = MagicMock()

    def subscribe(self, subject, callback, main_thread=True):
        self.subscribed.append(subject)

    def unsubscribe(self, subject):
        self.unsubscribed.append(subject)


@pytest.fixture
def live(qapp, monkeypatch):
    from lightfall.logbook import live_updates as lu
    client = MagicMock()
    fake_ipc = _FakeIPC()
    monkeypatch.setattr(lu, "get_ipc_service", lambda: fake_ipc)
    obj = lu.LogbookLiveUpdates(client)
    obj._fake_ipc = fake_ipc   # for assertions
    obj._client = client
    return obj


def test_event_triggers_sync(live):
    live._on_event("subj", {"op": "create"}, None)
    live._client.schedule_sync.assert_called_once()


def test_reconnect_triggers_sync_disconnect_does_not(live):
    live._on_connection_changed(True)
    live._client.schedule_sync.assert_called_once()
    live._client.schedule_sync.reset_mock()
    live._on_connection_changed(False)
    live._client.schedule_sync.assert_not_called()


def test_poll_tick_triggers_sync(live):
    live._on_poll_tick()
    live._client.schedule_sync.assert_called_once()


def test_subscribe_for_uses_server_user_subject(live):
    from lightfall.logbook.live_updates import subject_for_user
    live._subscribe_for("kc-sub-123")
    assert subject_for_user("kc-sub-123") in live._fake_ipc.subscribed


def test_resubscribe_swaps_subject(live):
    from lightfall.logbook.live_updates import subject_for_user
    live._subscribe_for("alice")
    live._subscribe_for("bob")
    assert subject_for_user("alice") in live._fake_ipc.unsubscribed
    assert subject_for_user("bob") in live._fake_ipc.subscribed

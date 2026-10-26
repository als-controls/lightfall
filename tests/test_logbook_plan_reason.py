"""The submitter's reason in a run's start document shows in its logbook fragment
and survives the stop-document update."""

from __future__ import annotations

import pytest


@pytest.fixture
def listener(tmp_path, monkeypatch):
    from lightfall.logbook.client import LogbookClient
    from lightfall.logbook.event_listener import EventListener

    LogbookClient.reset()
    client = LogbookClient.get_instance()
    monkeypatch.setattr(client, "_db_path", tmp_path / "logbook.db")
    monkeypatch.setattr(client, "_load_preferences", lambda: None)
    client._server_url = None
    client.init()
    lst = EventListener()
    lst.current_entry_id = client.create_entry("lb-test", title="reason")
    yield lst, client
    client.close()
    LogbookClient.reset()


def _plan_fragment(client, entry_id):
    (frag,) = [f for f in client.list_fragments(entry_id) if f["subtype"] == "bluesky_plan"]
    return frag


def test_reason_in_fragment_and_kept_after_stop(listener):
    lst, client = listener
    uid = "abcd1234-0000"
    lst._on_run_document("start", {"uid": uid, "plan_name": "scan", "reason": "Find the edge"})
    frag = _plan_fragment(client, lst.current_entry_id)
    assert frag["content"] == "Plan: scan (abcd1234)\nReason: Find the edge"
    assert frag["data"]["reason"] == "Find the edge"
    assert "reason" not in frag["data"]["params"]

    lst._on_run_document("stop", {"run_start": uid, "exit_status": "success"})
    frag = _plan_fragment(client, lst.current_entry_id)
    assert frag["content"] == "Plan: scan (abcd1234) — success\nReason: Find the edge"


def test_no_reason_keeps_the_old_one_line_format(listener):
    lst, client = listener
    lst._on_run_document("start", {"uid": "ffff0000-1", "plan_name": "count"})
    frag = _plan_fragment(client, lst.current_entry_id)
    assert frag["content"] == "Plan: count (ffff0000)"
    assert "reason" not in frag["data"]

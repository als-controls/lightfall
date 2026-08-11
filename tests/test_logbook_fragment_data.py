"""list_fragments must deserialize the JSON 'data' column into a dict.

Regression: the 'data' column is persisted with json.dumps, but list_fragments
returned the raw row, so callers received a JSON *string*.
_update_start_fragment_with_stop then did frag["data"].get(...) and raised
"'str' object has no attribute 'get'".
"""

from __future__ import annotations

import pytest


@pytest.fixture
def offline_client(tmp_path, monkeypatch):
    """A LogbookClient on a temp DB with sync disabled (no Qt timer needed)."""
    from lightfall.logbook.client import LogbookClient

    LogbookClient.reset()
    client = LogbookClient.get_instance()
    monkeypatch.setattr(client, "_db_path", tmp_path / "logbook.db")
    monkeypatch.setattr(client, "_load_preferences", lambda: None)
    client._server_url = None  # schedule_sync() early-returns -> no QTimer
    client.init()
    yield client
    client.close()
    LogbookClient.reset()


def test_list_fragments_deserializes_data_to_dict(offline_client):
    c = offline_client
    eid = c.create_entry("lb-test", title="frag-data")
    c.add_fragment(
        eid,
        kind="text",
        subtype="bluesky_plan",
        content="Plan: count",
        data={"uid": "abcd1234", "plan_name": "count"},
    )

    frags = c.list_fragments(eid)

    assert len(frags) == 1
    data = frags[0]["data"]
    assert isinstance(data, dict)
    assert data["uid"] == "abcd1234"
    # The exact call-site pattern that used to raise now works:
    assert data.get("plan_name") == "count"


def test_list_fragments_bad_json_falls_back_to_empty_dict(offline_client):
    c = offline_client
    eid = c.create_entry("lb-test", title="bad-json")
    fid = c.add_fragment(eid, content="x", data={"ok": 1})

    # Corrupt the stored data to a non-JSON string.
    db = c._ensure_db()
    db.execute("UPDATE fragment SET data = ? WHERE id = ?", ("not json", fid))
    db.commit()

    frags = c.list_fragments(eid)

    assert frags[0]["data"] == {}

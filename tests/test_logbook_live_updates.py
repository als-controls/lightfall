from __future__ import annotations


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

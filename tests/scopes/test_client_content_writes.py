"""ScopesClient content writes (publish, retract, restore), history and content policy."""
from __future__ import annotations

import json

import httpx

from lightfall.scopes.client import (
    ChainInfo,
    ContentPolicy,
    ScopesClient,
    ScopesError,
    describe_error,
)

BASE = "https://lb.test"
BR = "branch:7.0.1.1"
URL = f"{BASE}/logbook/scopes/{BR}/content/plan/scan"
BUNDLE_BYTES = b"PK\x03\x04fake-zip-bytes"
MANIFEST = {"type": "plan", "name": "scan", "file_count": 1, "sha256": "ab" * 32}


def _client():
    return ScopesClient(base_url=BASE, auth=httpx.Auth())


def _bundle(tmp_path):
    p = tmp_path / "scan.zip"
    p.write_bytes(BUNDLE_BYTES)
    return p


def test_publish_sends_multipart_and_agent_header(httpx_mock, tmp_path):
    record = {"scope": BR, "type": "plan", "name": "scan", "revision": 3,
              "sha256": "ab" * 32, "size": len(BUNDLE_BYTES)}
    httpx_mock.add_response(method="POST", url=URL, status_code=201, json=record)
    out = _client().publish_content(BR, "plan", "scan", MANIFEST, _bundle(tmp_path), agent=True)
    assert out == record
    req = httpx_mock.get_request()
    assert req.headers["X-Lightfall-Agent"] == "1"
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.read()
    assert b'name="manifest"' in body and json.dumps(MANIFEST).encode() in body
    assert b'name="bundle"' in body and BUNDLE_BYTES in body


def test_publish_without_agent_has_no_header(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=URL, json={"revision": 1})
    _client().publish_content(BR, "plan", "scan", MANIFEST, _bundle(tmp_path))
    assert "X-Lightfall-Agent" not in httpx_mock.get_request().headers


def test_retract_restore_history(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{URL}/retract", json={"revision": 4})
    httpx_mock.add_response(method="POST", url=f"{URL}/restore", json={"revision": 2})
    hist = [{"revision": 2, "current": True}]
    httpx_mock.add_response(method="GET", url=f"{URL}/history", json=hist)
    c = _client()
    assert c.retract_content(BR, "plan", "scan", agent=True) == {"revision": 4}
    assert c.restore_content(BR, "plan", "scan", 2) == {"revision": 2}
    assert c.content_history(BR, "plan", "scan") == hist
    reqs = httpx_mock.get_requests()
    assert reqs[0].headers["X-Lightfall-Agent"] == "1"
    assert "X-Lightfall-Agent" not in reqs[1].headers
    assert json.loads(reqs[1].read()) == {"revision": 2}


def test_write_errors_raise_scopes_error(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=URL, status_code=413,
                            json={"detail": "max_files is 2000; this bundle has 2500"})
    try:
        _client().publish_content(BR, "plan", "scan", MANIFEST, _bundle(tmp_path))
    except ScopesError as e:
        assert e.status == 413 and "max_files" in str(e)
    else:
        raise AssertionError("expected ScopesError")


def test_chain_parses_content_policy_and_defaults_when_absent(httpx_mock):
    base = {"chain": ["facility"], "roles": {"facility": "user"}, "entitled": [],
            "facility_label": "ALS"}
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/chain?bind=x",
                            json={**base, "content_policy": {
                                "max_bundle_bytes": 10, "max_uncompressed_bytes": 20,
                                "max_files": 3}})
    info = _client().chain("x")
    assert info.content_policy == ContentPolicy(10, 20, 3)
    assert ChainInfo.from_dict(info.to_dict()) == info
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/chain?bind=y", json=base)
    assert _client().chain("y").content_policy == ContentPolicy(52428800, 209715200, 2000)
    assert ContentPolicy.from_dict(None) == ContentPolicy()
    assert ContentPolicy.from_dict({"max_files": "x"}) == ContentPolicy()


def test_describe_error_messages():
    d = describe_error
    assert d(ScopesError("x", status=403), scope="branch:7.0.1.1") == \
        "You're not a maintainer of Branch 7.0.1.1."
    assert d(ScopesError("x", status=403), scope="beamline:7.0.1") == \
        "You're not a maintainer of Beamline 7.0.1."
    assert d(ScopesError("x", status=403), scope="facility") == \
        "You're not a maintainer of Facility."
    assert d(ScopesError("x", status=409)) == "Someone changed it at the same time — try again."
    assert d(ScopesError("x", status=412)) == \
        "Someone changed it while you were deciding — look again."
    assert d(ScopesError("Publish: max_files is 2000; this bundle has 2500", status=413)) == \
        "Publish: max_files is 2000; this bundle has 2500"
    assert d(ScopesError("Publish: bad name", status=422)) == \
        "The server refused it: Publish: bad name"
    assert d(ScopesError("Logbook server unreachable: x")) == "The logbook server is unreachable."
    assert d(ScopesError("boom", status=500)) == "boom"


def test_malformed_write_reply_is_not_called_unreachable(httpx_mock, tmp_path):
    # The server may have accepted the write: say so, don't blame the network.
    unreadable = "The server's reply couldn't be read; check History before retrying."
    httpx_mock.add_response(method="POST", url=URL, status_code=200, text="<html>")
    httpx_mock.add_response(method="POST", url=f"{URL}/retract", status_code=200, json=[1])
    httpx_mock.add_response(method="GET", url=f"{URL}/history", status_code=200, text="?")
    c = _client()
    calls = (lambda: c.publish_content(BR, "plan", "scan", MANIFEST, _bundle(tmp_path)),
             lambda: c.retract_content(BR, "plan", "scan"),
             lambda: c.content_history(BR, "plan", "scan"))
    for call in calls:
        try:
            call()
        except ScopesError as e:
            assert e.status is None
            assert describe_error(e, scope=BR) == unreadable
        else:
            raise AssertionError("expected ScopesError")


LISTING = f"{BASE}/logbook/scopes/{BR}/content/?include_retracted=1"


def _rec(name, revision, **extra):
    return {"type": "plan", "name": name, "revision": revision, "sha256": "ab" * 32,
            "size": 10, "published_by": "ann", "published_at": "2026-11-01T10:00:00Z",
            "author": None, "file_count": 1, "uncompressed_size": 20, **extra}


def test_retracted_content_parses_and_old_server_is_none(httpx_mock):
    httpx_mock.add_response(method="GET", url=LISTING, json={
        "scope": BR, "etag": "e1", "include_retracted": True,
        "items": [_rec("live", 4, retracted=False), _rec("gone", 2, retracted=True)]})
    items = _client().retracted_content(BR)
    assert [(i.name, i.revision, i.file_count) for i in items] == [("gone", 2, 1)]
    assert items[0].published_by == "ann"

    # A server that honours the flag says so, even with nothing at the scope.
    httpx_mock.add_response(method="GET", url=LISTING,
                            json={"scope": BR, "etag": "e2", "include_retracted": True,
                                  "items": []})
    assert _client().retracted_content(BR) == []

    # An old server ignores include_retracted: no marker, with or without items
    # (everything retracted there looks like an empty listing).
    httpx_mock.add_response(method="GET", url=LISTING,
                            json={"scope": BR, "etag": "e0", "items": [_rec("live", 4)]})
    assert _client().retracted_content(BR) is None
    httpx_mock.add_response(method="GET", url=LISTING,
                            json={"scope": BR, "etag": "e0", "items": []})
    assert _client().retracted_content(BR) is None

    httpx_mock.add_response(method="GET", url=LISTING, status_code=403,
                            json={"detail": "forbidden"})
    try:
        _client().retracted_content(BR)
    except ScopesError as e:
        assert e.status == 403
    else:
        raise AssertionError("expected ScopesError")


def test_expected_revision_sends_if_match(httpx_mock):
    for _ in range(2):
        httpx_mock.add_response(method="POST", url=f"{URL}/retract", json={"revision": 4})
    for _ in range(3):
        httpx_mock.add_response(method="POST", url=f"{URL}/restore", json={"revision": 2})
    c = _client()
    c.retract_content(BR, "plan", "scan", expected_revision=4)
    c.restore_content(BR, "plan", "scan", 2, expected_revision=4, agent=True)
    c.restore_content(BR, "plan", "scan", 2, expected_revision="none")
    c.retract_content(BR, "plan", "scan")
    c.restore_content(BR, "plan", "scan", 2)
    reqs = httpx_mock.get_requests()
    assert [r.headers.get("If-Match") for r in reqs] == ["4", "4", "none", None, None]
    assert reqs[1].headers["X-Lightfall-Agent"] == "1"
    assert json.loads(reqs[2].read()) == {"revision": 2}


def test_412_message(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{URL}/retract", status_code=412,
                            json={"detail": "changed since you looked: current revision is 5"})
    try:
        _client().retract_content(BR, "plan", "scan", expected_revision=4)
    except ScopesError as e:
        assert e.status == 412
        assert describe_error(e, scope=BR) == \
            "Someone changed it while you were deciding — look again."
    else:
        raise AssertionError("expected ScopesError")


def test_transport_failure_is_unreachable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), method="POST", url=f"{URL}/retract")
    try:
        _client().retract_content(BR, "plan", "scan")
    except ScopesError as e:
        assert describe_error(e, scope=BR) == "The logbook server is unreachable."
    else:
        raise AssertionError("expected ScopesError")

"""ScopesClient against the phase-3 logbook endpoints (pytest-httpx)."""
from __future__ import annotations

import httpx
import pytest

from lightfall.scopes.client import ChainInfo, ScopesClient, ScopesError, Snapshot

BASE = "https://lb.test"
CHAIN = {
    "chain": ["facility", "beamline:7.0.1", "branch:7.0.1.1", "user:u1"],
    "roles": {"facility": "admin", "beamline:7.0.1": "admin", "branch:7.0.1.1": "admin",
              "user:u1": "owner"},
    "entitled": ["beamline:7.0.1", "branch:7.0.1.1", "branch:7.0.1.2"],
    "facility_label": "ALS",
}


def _client():
    return ScopesClient(base_url=BASE, auth=httpx.Auth())


def test_chain_parses(httpx_mock):
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/chain?bind=branch%3A7.0.1.1", json=CHAIN)
    info = _client().chain("branch:7.0.1.1")
    assert info.chain[-1] == "user:u1"
    assert info.roles["facility"] == "admin"
    assert info.entitled == ("beamline:7.0.1", "branch:7.0.1.1", "branch:7.0.1.2")
    assert ChainInfo.from_dict(info.to_dict()) == info


def test_snapshot_sends_etag_and_handles_304(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/logbook/scopes/facility/settings",
        match_headers={"If-None-Match": "e1"}, status_code=304,
    )
    assert _client().snapshot("facility", etag="e1") is None


def test_snapshot_parses_200(httpx_mock):
    body = {"scope": "branch:7.0.1.1", "settings": {"voice.wake_word": "hey"},
            "versions": {"voice.wake_word": 2},
            "locks": {"voice.wake_word": {"locked_by": "rp", "reason": "", "locked_at": "t"}},
            "etag": "e2"}
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/branch:7.0.1.1/settings", json=body)
    snap = _client().snapshot("branch:7.0.1.1")
    assert snap.settings == {"voice.wake_word": "hey"}
    assert snap.etag == "e2"
    assert Snapshot.from_dict(snap.to_dict()) == snap


def test_setting_keys_403_returns_none(httpx_mock):
    httpx_mock.add_response(url=f"{BASE}/logbook/setting-keys/", status_code=403,
                            json={"detail": "maintainer role required"})
    assert _client().setting_keys() is None


def test_401_raises_with_status(httpx_mock):
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/chain?bind=facility", status_code=401,
                            json={"detail": "Not authenticated"})
    with pytest.raises(ScopesError) as ei:
        _client().chain("facility")
    assert ei.value.status == 401


def test_transport_error_has_no_status(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(ScopesError) as ei:
        _client().chain("facility")
    assert ei.value.status is None


def test_malformed_json_is_a_scopes_error(httpx_mock):
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/chain?bind=facility", json={"oops": 1})
    with pytest.raises(ScopesError):
        _client().chain("facility")


def test_put_setting_sends_if_match_and_returns_version(httpx_mock):
    httpx_mock.add_response(
        method="PUT", url=f"{BASE}/logbook/scopes/user:u1/settings/theme",
        match_headers={"If-Match": "3"}, match_json={"value": "dark"},
        json={"scope": "user:u1", "key": "theme", "value": "dark", "version": 4},
    )
    assert _client().put_setting("user:u1", "theme", "dark", version=3) == 4


def test_put_new_row_has_no_if_match(httpx_mock):
    httpx_mock.add_response(method="PUT", url=f"{BASE}/logbook/scopes/user:u1/settings/theme",
                            json={"version": 1})
    assert _client().put_setting("user:u1", "theme", "dark") == 1
    assert "If-Match" not in httpx_mock.get_request().headers


def test_put_key_pending_carries_code_and_sends_proposal(httpx_mock):
    httpx_mock.add_response(
        method="PUT", url=f"{BASE}/logbook/scopes/branch:7.0.1.1/settings/tiled_url",
        status_code=409,
        json={"status_code": 409, "detail": "tiled_url is not an approved setting yet",
              "extra": {"code": "key_pending", "status": "pending"}},
    )
    with pytest.raises(ScopesError) as ei:
        _client().put_setting("branch:7.0.1.1", "tiled_url", "x",
                              proposed_spec={"key": "tiled_url", "type": "any"})
    assert (ei.value.status, ei.value.code, ei.value.key_status) == (409, "key_pending", "pending")
    assert '"tiled_url"' in httpx_mock.get_request().headers["X-Proposed-Spec"]


def test_delete_setting_treats_404_as_done(httpx_mock):
    httpx_mock.add_response(method="DELETE",
                            url=f"{BASE}/logbook/scopes/user:u1/settings/theme", status_code=404)
    _client().delete_setting("user:u1", "theme")


def test_delete_transport_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(ScopesError) as ei:
        _client().delete_setting("user:u1", "theme")
    assert ei.value.status is None


def test_chain_info_parses_and_round_trips_the_scope_tree():
    from lightfall.scopes.client import ChainInfo, TreeEntry

    d = {"chain": ["facility", "user:u"], "roles": {"facility": None, "user:u": "owner"},
         "entitled": ["branch:7.0.1.1"], "facility_label": "ALS",
         "tree": [{"scope": "beamline:7.0.1", "parent": None, "role": None},
                  {"scope": "branch:7.0.1.1", "parent": "beamline:7.0.1", "role": "maintainer"}]}
    info = ChainInfo.from_dict(d)
    assert info.tree[1] == TreeEntry("branch:7.0.1.1", "beamline:7.0.1", "maintainer")
    assert ChainInfo.from_dict(info.to_dict()) == info
    old_server = {k: v for k, v in d.items() if k != "tree"}
    assert ChainInfo.from_dict(old_server).tree == ()


def test_approve_sends_edits_and_note(httpx_mock):
    import json as _json

    rec = {"key": "tiled_enabled", "status": "approved", "spec": {"key": "tiled_enabled"}}
    httpx_mock.add_response(method="POST",
                            url=f"{BASE}/logbook/setting-keys/tiled_enabled/approve", json=rec)
    got = _client().approve_key("tiled_enabled", edits={"scopes": ["facility"]}, note="ok")
    assert got == rec
    assert _json.loads(httpx_mock.get_request().content) == {
        "spec": {"scopes": ["facility"]}, "note": "ok"}


def test_approve_without_edits_sends_null_spec(httpx_mock):
    import json as _json

    httpx_mock.add_response(method="POST", url=f"{BASE}/logbook/setting-keys/k/approve",
                            json={"key": "k", "status": "approved"})
    _client().approve_key("k")
    assert _json.loads(httpx_mock.get_request().content)["spec"] is None


def test_reject_and_revoke(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/logbook/setting-keys/k/reject",
                            json={"key": "k", "status": "rejected"})
    httpx_mock.add_response(method="DELETE", url=f"{BASE}/logbook/setting-keys/k",
                            status_code=204)
    assert _client().reject_key("k", note="no")["status"] == "rejected"
    _client().revoke_key("k")


def test_review_refused_for_non_admin_raises_403(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/logbook/setting-keys/k/approve",
                            status_code=403, json={"detail": "facility admin role required"})
    with pytest.raises(ScopesError) as ei:
        _client().approve_key("k")
    assert ei.value.status == 403


def test_chain_info_carries_approved_specs():
    from lightfall.scopes.client import ChainInfo

    d = {"chain": ["facility"], "roles": {}, "facility_label": "",
         "specs": [{"key": "voice.wake_word", "scopes": ["branch"]}, {"no": "key"}]}
    info = ChainInfo.from_dict(d)
    assert [s["key"] for s in info.specs] == ["voice.wake_word"]
    assert ChainInfo.from_dict(info.to_dict()) == info

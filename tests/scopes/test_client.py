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

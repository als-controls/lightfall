"""SessionManager mints a CSM key per CSM base URL at login (real mint path, mocked HTTP)."""

from __future__ import annotations

import json

import pytest

from lightfall.auth.session import Session, SessionManager, User
from lightfall.devices.backend_types.csm import csm_service_id


def httpx_response(body, status=200):
    import httpx

    return httpx.Response(status, json=body)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    SessionManager.reset()
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url", lambda: "https://tiled.test"
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url", lambda: "https://logbook.test"
    )
    yield
    SessionManager.reset()


def _mint_response(secret):
    return {
        "secret": secret,
        "first_eight": secret[:8],
        "expiration_time": None,
        "scopes": [],
        "note": "n",
    }


def test_service_id_normalises():
    assert csm_service_id("https://CSM.als.lbl.gov/") == "csm:https://csm.als.lbl.gov"
    assert csm_service_id(" https://csm.als.lbl.gov ") == csm_service_id("https://csm.als.lbl.gov")


def test_attach_session_mints_csm_key_and_drops_bearer(httpx_mock, monkeypatch):
    monkeypatch.setattr(
        "lightfall.devices.backend_types.csm.csm_mint_targets", lambda: ["https://csm.test"]
    )
    seen = {}

    def _cb(request):
        seen[str(request.url)] = (request.headers.get("Authorization"), json.loads(request.content))
        host = request.url.host
        return httpx_response(_mint_response(f"{host}-secret-xyz"))

    httpx_mock.add_callback(_cb, is_reusable=True)
    sm = SessionManager.get_instance()
    sm.attach_session(
        Session(user=User(username="rp", attributes={"sub": "kc-1"}), token="kc-bearer")
    )

    assert sm.get_api_key(csm_service_id("https://csm.test")) == "csm.test-secret-xyz"
    auth, body = seen["https://csm.test/api/v1/auth/apikey"]
    assert auth == "Bearer kc-bearer"
    assert body["scopes"] == ["read", "display:write", "instantiables:create"]
    assert sm.session.token is None


def test_no_csm_rows_no_csm_mint(httpx_mock, monkeypatch):
    monkeypatch.setattr("lightfall.devices.backend_types.csm.csm_mint_targets", lambda: [])
    httpx_mock.add_callback(lambda r: httpx_response(_mint_response("k" * 12)), is_reusable=True)
    sm = SessionManager.get_instance()
    sm.attach_session(Session(user=User(username="rp", attributes={"sub": "kc-1"}), token="t"))
    assert not any(k.startswith("csm:") for k in sm._service_keys)


def test_csm_mint_failure_leaves_no_key(httpx_mock, monkeypatch):
    monkeypatch.setattr(
        "lightfall.devices.backend_types.csm.csm_mint_targets", lambda: ["https://csm.test"]
    )

    def _cb(request):
        if request.url.host == "csm.test":
            return httpx_response({"detail": "nope"}, status=401)
        return httpx_response(_mint_response("k" * 12))

    httpx_mock.add_callback(_cb, is_reusable=True)
    sm = SessionManager.get_instance()
    sm.attach_session(Session(user=User(username="rp", attributes={"sub": "kc-1"}), token="t"))
    assert sm.get_api_key(csm_service_id("https://csm.test")) is None


def test_csm_mint_targets_reads_enabled_csm_rows(monkeypatch):
    from lightfall.devices.backend_types import csm as csm_type

    rows = [
        {
            "id": "a",
            "type": "csm",
            "name": "csm",
            "enabled": True,
            "config": {"url": "https://csm.test/"},
        },
        {
            "id": "b",
            "type": "csm",
            "name": "csm-2",
            "enabled": True,
            "config": {"url": "https://CSM.test"},
        },
        {
            "id": "c",
            "type": "csm",
            "name": "off",
            "enabled": False,
            "config": {"url": "https://other"},
        },
        {"id": "d", "type": "mock", "name": "mock", "enabled": True, "config": {}},
    ]

    class _Prefs:
        def get(self, key, default=None):
            return rows if key == "device_backends" else default

    monkeypatch.setattr(
        "lightfall.ui.preferences.PreferencesManager.get_instance", lambda: _Prefs()
    )
    # Disabled rows are minted too: a new row is saved disabled until its key exists.
    assert csm_type.csm_mint_targets() == ["https://csm.test", "https://other"]

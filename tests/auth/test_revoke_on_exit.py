"""Tests for shutdown-time revocation of unleased service keys.

Covers SessionManager.revoke_unleased_service_keys(): unleased keys are
revoked via self-auth (the key deletes itself), keys handed out through
get_minted_key() (leased to detached executors) are exempt, and logout
runs the revoke round before clearing the cache.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lightfall.auth.service_key import MintedKey
from lightfall.auth.session import Session, SessionManager, User


@pytest.fixture(autouse=True)
def reset_singleton():
    SessionManager.reset()
    yield
    SessionManager.reset()


def _minted(secret: str = "abc123", expires_in_s: int = 3600) -> MintedKey:
    return MintedKey(
        secret=secret,
        first_eight=secret[:8].ljust(8, "x"),
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_s),
        scopes=("inherit",),
        note="test",
    )


def _install_key(sm: SessionManager, service: str, minted: MintedKey, url: str) -> None:
    sm._service_keys[service] = minted
    sm._service_key_urls[service] = url


def test_revoke_unleased_revokes_and_clears(monkeypatch):
    sm = SessionManager.get_instance()
    minted = _minted(secret="tiled-secret")
    _install_key(sm, "tiled", minted, "https://tiled.test/api/v1")

    revoked: list = []
    monkeypatch.setattr(
        "lightfall.auth.session.revoke_service_key",
        lambda url, **kw: revoked.append((url, kw)),
    )

    sm.revoke_unleased_service_keys()

    assert len(revoked) == 1
    url, kw = revoked[0]
    assert url == "https://tiled.test/api/v1"
    assert kw["first_eight"] == minted.first_eight
    # Self-auth: the key is its own credential; no bearer survives the mint round.
    assert kw["api_key"] == "tiled-secret"
    assert sm.get_api_key("tiled") is None


def test_leased_key_is_exempt(monkeypatch):
    sm = SessionManager.get_instance()
    _install_key(sm, "tiled", _minted(secret="leased"), "https://tiled.test/api/v1")

    # Simulate a pipeline dispatch embedding the key in a job payload.
    assert sm.get_minted_key("tiled") is not None

    revoked: list = []
    monkeypatch.setattr(
        "lightfall.auth.session.revoke_service_key",
        lambda url, **kw: revoked.append(url),
    )

    sm.revoke_unleased_service_keys()

    assert revoked == []
    assert sm.get_api_key("tiled") == "leased"


def test_expired_key_is_not_revoked(monkeypatch):
    sm = SessionManager.get_instance()
    _install_key(sm, "tiled", _minted(expires_in_s=-60), "https://tiled.test/api/v1")

    revoked: list = []
    monkeypatch.setattr(
        "lightfall.auth.session.revoke_service_key",
        lambda url, **kw: revoked.append(url),
    )

    sm.revoke_unleased_service_keys()

    assert revoked == []


def test_key_without_recorded_url_is_skipped(monkeypatch):
    sm = SessionManager.get_instance()
    sm._service_keys["tiled"] = _minted()  # no url recorded

    revoked: list = []
    monkeypatch.setattr(
        "lightfall.auth.session.revoke_service_key",
        lambda url, **kw: revoked.append(url),
    )

    sm.revoke_unleased_service_keys()

    assert revoked == []


def test_logout_revokes_unleased_keys(monkeypatch):
    import asyncio

    sm = SessionManager.get_instance()
    sm._session = Session(user=User(username="tester"))
    _install_key(sm, "tiled", _minted(secret="logout-me"), "https://tiled.test/api/v1")

    revoked: list = []
    monkeypatch.setattr(
        "lightfall.auth.session.revoke_service_key",
        lambda url, **kw: revoked.append(kw["first_eight"]),
    )

    asyncio.run(sm.logout())

    assert len(revoked) == 1
    assert sm.get_api_key("tiled") is None
    assert sm._leased_services == set()


def test_session_key_lifetime_reads_preference(monkeypatch):
    class FakePrefs:
        def get(self, key, default=None):
            assert key == "tiled_session_key_lifetime"
            return 3600

    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance",
        staticmethod(lambda: FakePrefs()),
    )
    assert SessionManager._session_key_lifetime() == 3600


def test_session_key_lifetime_falls_back_to_default(monkeypatch):
    def boom():
        raise RuntimeError("headless: no preferences subsystem")

    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance",
        staticmethod(boom),
    )
    assert SessionManager._session_key_lifetime() == 604800

"""Tests for SessionManager's service-key cache + login mint round.

Covers Task 3 (cache surface, this file initially) and Task 4 (login mint).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lucid.auth.service_key import MintedKey
from lucid.auth.session import Session, SessionManager, User


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
        scopes=("read:metadata",),
        note="test",
    )


# The five tests below exercise the cache via direct attribute access
# (`sm._service_keys[...] = ...`) because they are unit tests of the cache
# read API. The login-path integration test at the bottom of this file
# covers the production write path.


def test_get_api_key_returns_none_when_no_cache():
    sm = SessionManager.get_instance()
    assert sm.get_api_key("tiled") is None


def test_get_api_key_returns_cached_secret():
    sm = SessionManager.get_instance()
    sm._service_keys["tiled"] = _minted(secret="tiled-key")
    assert sm.get_api_key("tiled") == "tiled-key"


def test_get_api_key_returns_none_when_expired():
    sm = SessionManager.get_instance()
    sm._service_keys["tiled"] = _minted(secret="old", expires_in_s=-60)
    assert sm.get_api_key("tiled") is None


def test_get_minted_key_returns_full_record():
    sm = SessionManager.get_instance()
    key = _minted(secret="full")
    sm._service_keys["tiled"] = key
    assert sm.get_minted_key("tiled") is key


def test_get_minted_key_returns_none_when_expired():
    sm = SessionManager.get_instance()
    sm._service_keys["tiled"] = _minted(secret="stale", expires_in_s=-60)
    assert sm.get_minted_key("tiled") is None


def test_cache_cleared_on_logout(monkeypatch):
    import asyncio

    sm = SessionManager.get_instance()
    # logout() early-returns when there's no session, so install a minimal one.
    sm._session = Session(user=User(username="tester"))
    sm._service_keys["tiled"] = _minted()
    assert sm.get_api_key("tiled") is not None

    # logout() is async; run it
    asyncio.run(sm.logout())

    assert sm.get_api_key("tiled") is None


def test_mint_all_service_keys_populates_cache(monkeypatch):
    """A successful mint populates the cache slot for tiled."""
    sm = SessionManager.get_instance()
    called: list = []

    def fake_mint(service_url, bearer, *, expires_in, scopes, note, timeout=10.0):
        called.append((service_url, bearer, expires_in, tuple(scopes), note))
        return _minted(secret=f"key-for-{service_url}")

    monkeypatch.setattr("lucid.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lucid.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lucid.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    sm._mint_all_service_keys("bearer-xyz")

    assert "tiled" in sm._service_keys
    assert sm.get_api_key("tiled") == "key-for-https://tiled.test/api/v1"
    # Both services are minted in a single round.
    assert len(called) == 2
    tiled_call = next(c for c in called if c[0] == "https://tiled.test/api/v1")
    url, bearer, expires_in, scopes, note = tiled_call
    assert url == "https://tiled.test/api/v1"
    assert bearer == "bearer-xyz"
    assert expires_in == 604800
    assert "read:metadata" in scopes and "create:apikeys" not in scopes
    assert "lucid" in note


def test_mint_all_service_keys_tolerates_failure(monkeypatch):
    """A failed mint logs but leaves the slot empty; other services unaffected."""
    import httpx

    sm = SessionManager.get_instance()

    def boom(service_url, bearer, **kwargs):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr("lucid.auth.session.mint_service_key", boom)
    monkeypatch.setattr(
        "lucid.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lucid.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    # MUST NOT raise
    sm._mint_all_service_keys("bearer-xyz")

    assert sm.get_api_key("tiled") is None


def test_login_runs_mint_round_through_asyncio_to_thread(monkeypatch):
    """Integration test: full login() path mints service keys via asyncio.to_thread.

    Bypassed by the direct _mint_all_service_keys tests above; this one exercises
    the actual production code path so a regression in the asyncio.to_thread
    wrapper would be caught.
    """
    import asyncio

    from lucid.auth.providers.base import AuthProvider
    from lucid.auth.session import Session

    sm = SessionManager.get_instance()

    # Stub mint helper + URL resolvers
    monkeypatch.setattr("lucid.auth.session.mint_service_key", lambda *a, **kw: _minted(secret="login-tiled-key"))
    monkeypatch.setattr(
        "lucid.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lucid.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    # Minimal AuthProvider stub that returns a session with a token
    class _StubProvider(AuthProvider):
        @property
        def name(self) -> str:
            return "stub"

        @property
        def supports_password_auth(self) -> bool:
            return True

        @property
        def supports_browser_auth(self) -> bool:
            return False

        async def authenticate(self, **kwargs):
            from lucid.auth.session import User
            user = User(username="tester", attributes={"sub": "kc-sub-1"})
            return Session(user=user, token="stub-bearer")

        async def logout(self, session):
            pass

        async def refresh(self, session):
            return None

        async def check_connectivity(self):
            return True

    sm.set_provider(_StubProvider())

    ok = asyncio.run(sm.login())

    assert ok is True
    assert sm.get_api_key("tiled") == "login-tiled-key"


def test_mint_all_service_keys_mints_both_services(monkeypatch):
    """Both tiled and logbook are minted at login."""
    sm = SessionManager.get_instance()
    called: dict[str, str] = {}
    scopes_by_url: dict[str, tuple] = {}

    def fake_mint(service_url, bearer, *, expires_in, scopes, note, timeout=10.0):
        called[service_url] = bearer
        scopes_by_url[service_url] = tuple(scopes)
        return _minted(secret=f"key-for-{service_url}")

    monkeypatch.setattr("lucid.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lucid.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lucid.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    sm._mint_all_service_keys("bearer-xyz")

    assert sm.get_api_key("tiled") == "key-for-https://tiled.test/api/v1"
    assert sm.get_api_key("logbook") == "key-for-https://logbook.test/api/v1"
    assert called["https://tiled.test/api/v1"] == "bearer-xyz"
    assert called["https://logbook.test/api/v1"] == "bearer-xyz"
    # Logbook must be minted with empty scopes; tiled needs read:metadata.
    assert scopes_by_url["https://logbook.test/api/v1"] == ()
    tiled_scopes = scopes_by_url["https://tiled.test/api/v1"]
    assert "read:metadata" in tiled_scopes


def test_mint_logbook_failure_leaves_tiled_intact(monkeypatch):
    """A failed logbook mint does not interfere with the tiled key."""
    sm = SessionManager.get_instance()

    def fake_mint(service_url, bearer, **kwargs):
        if "logbook" in service_url:
            raise httpx.ConnectError("logbook unreachable")
        return _minted(secret="tiled-secret")

    monkeypatch.setattr("lucid.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lucid.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lucid.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    sm._mint_all_service_keys("bearer-xyz")

    assert sm.get_api_key("tiled") == "tiled-secret"
    assert sm.get_api_key("logbook") is None

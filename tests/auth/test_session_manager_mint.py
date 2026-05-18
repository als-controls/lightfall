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


# Tests below poke `sm._service_keys` directly because Task 3 introduces only
# the read API (get_api_key, get_minted_key). Task 4 will add the public
# cache-populate path via `SessionManager.login()` -> `_mint_all_service_keys`.
# When Task 4 lands, the direct-poke pattern in these tests can be replaced
# with black-box login-based setup. TODO(task-4): migrate to login(...).


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
    """A successful mint populates the cache slot."""
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

    sm._mint_all_service_keys("bearer-xyz")

    assert "tiled" in sm._service_keys
    assert sm.get_api_key("tiled") == "key-for-https://tiled.test/api/v1"
    assert len(called) == 1
    url, bearer, expires_in, scopes, note = called[0]
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

    # MUST NOT raise
    sm._mint_all_service_keys("bearer-xyz")

    assert sm.get_api_key("tiled") is None

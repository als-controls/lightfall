"""Tests for SessionManager's service-key cache + login mint round.

Covers Task 3 (cache surface, this file initially) and Task 4 (login mint).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

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

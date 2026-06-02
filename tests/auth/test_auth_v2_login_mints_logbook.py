"""End-to-end auth-v2 test: login → mint → use.

Verifies that the production login path mints a logbook session key and
that a downstream consumer (UserSettingsClient) picks it up and sends
`Authorization: Apikey <secret>` on its requests. Counterpart to
test_session_manager_mint.test_login_runs_mint_round_through_asyncio_to_thread,
which covers the same for tiled.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from lightfall.auth.providers.base import AuthProvider
from lightfall.auth.service_key import MintedKey
from lightfall.auth.session import Session, SessionManager, User
from lightfall.settings.user_settings_client import UserSettingsClient


@pytest.fixture(autouse=True)
def _reset_singletons():
    SessionManager.reset()
    UserSettingsClient.reset()
    yield
    SessionManager.reset()
    UserSettingsClient.reset()


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
        return Session(
            user=User(username="tester", attributes={"sub": "kc-sub-1"}),
            token="stub-bearer",
        )

    async def logout(self, session):
        pass

    async def refresh(self, session):
        return None

    async def check_connectivity(self):
        return True


def _minted(secret: str) -> MintedKey:
    return MintedKey(
        secret=secret,
        first_eight=secret[:8].ljust(8, "x"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=(),
        note="test",
    )


def test_login_mints_logbook_key_and_consumer_uses_it(monkeypatch, httpx_mock):
    """Full production path: login -> SessionManager mints logbook key
    -> UserSettingsClient.get() sends `Authorization: Apikey <secret>`.
    """

    # Capture every mint call so we can assert the logbook one was issued
    # with the right URL.
    mint_calls: list[tuple[str, str]] = []

    def fake_mint(service_url, bearer, *, expires_in, scopes, note, timeout=10.0):
        mint_calls.append((service_url, bearer))
        if "logbook" in service_url:
            return _minted("logbook-key-xyz")
        return _minted("tiled-key-xyz")

    monkeypatch.setattr("lightfall.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    # 1. Login through the production path
    sm = SessionManager.get_instance()
    sm.set_provider(_StubProvider())
    ok = asyncio.run(sm.login())
    assert ok is True

    # Sanity: both keys are cached
    assert sm.get_api_key("logbook") == "logbook-key-xyz"
    assert sm.get_api_key("tiled") == "tiled-key-xyz"

    # Sanity: mint was called for both services
    urls_minted = {url for url, _ in mint_calls}
    assert "https://logbook.test/api/v1" in urls_minted
    assert "https://tiled.test/api/v1" in urls_minted

    # 2. A downstream consumer uses the logbook key.
    # The base_url here ("lb.test") is intentionally distinct from the mint
    # URL ("logbook.test") — ServiceKeyAuth looks up by service name, not URL,
    # so the consumer's base_url is independent of where the key was minted.
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/theme?beamline=",
        json={
            "user_id": "tester",
            "beamline": "",
            "key": "theme",
            "value": "dark",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    UserSettingsClient.init(base_url="https://lb.test")
    client = UserSettingsClient.get_instance()
    client.get("theme")

    captured = httpx_mock.get_requests()
    assert len(captured) == 1
    assert captured[0].headers.get("Authorization") == "Apikey logbook-key-xyz"

"""End-to-end auth-v2: login → mint → use → logout.

Single test that walks the production code path:
- Stubbed Keycloak provider returns a Session with bearer + refresh + id_token.
- SessionManager.login() mints both tiled and logbook keys, clears tokens.
- UserSettingsClient.get() sends Apikey header to lightfall-logbook.
- SessionManager.logout() restores id_token for the provider's RP-initiated
  logout, then clears every credential slot.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from lightfall.auth.providers.base import AuthProvider
from lightfall.auth.service_key import MintedKey
from lightfall.auth.session import AuthState, Session, SessionManager, User
from lightfall.settings.user_settings_client import UserSettingsClient


@pytest.fixture(autouse=True)
def _reset_singletons():
    SessionManager.reset()
    UserSettingsClient.reset()
    yield
    SessionManager.reset()
    UserSettingsClient.reset()


def _minted(secret: str) -> MintedKey:
    return MintedKey(
        secret=secret,
        first_eight=secret[:8].ljust(8, "x"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=(),
        note="test",
    )


class _StubKeycloakProvider(AuthProvider):
    """Synthetic Keycloak-shaped provider for the round-trip test."""

    def __init__(self) -> None:
        self.logout_calls: list[Session] = []

    @property
    def name(self) -> str:
        return "stub-keycloak"

    @property
    def supports_password_auth(self) -> bool:
        return True

    @property
    def supports_browser_auth(self) -> bool:
        return False

    async def authenticate(self, **kwargs):
        return Session(
            user=User(username="tester", attributes={"sub": "kc-sub-1"}),
            token="bearer-original",
            refresh_token="refresh-original",
            id_token="id-original",
        )

    async def logout(self, session):
        # Capture a snapshot of what was passed in for assertion
        self.logout_calls.append(
            Session(
                user=session.user,
                token=session.token,
                refresh_token=session.refresh_token,
                id_token=session.id_token,
            )
        )

    async def refresh(self, session):
        return None

    async def check_connectivity(self):
        return True


def test_full_auth_v2_roundtrip(monkeypatch, httpx_mock):
    """Login -> use Tiled+logbook -> logout (full production wiring)."""

    # Stub mint to return per-service synthetic keys
    mint_calls: list[tuple[str, str]] = []

    def fake_mint(service_url, bearer, *, expires_in, scopes, note, timeout=10.0):
        mint_calls.append((service_url, bearer))
        if "logbook" in service_url:
            return _minted("logbook-secret-v2")
        return _minted("tiled-secret-v2")

    monkeypatch.setattr("lightfall.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    sm = SessionManager.get_instance()
    provider = _StubKeycloakProvider()
    sm.set_provider(provider)

    # 1. Login
    assert asyncio.run(sm.login()) is True
    assert sm.state == AuthState.AUTHENTICATED

    # 2. Post-mint state: tokens cleared, id_token stashed
    assert sm.session.token is None
    assert sm.session.refresh_token is None
    assert sm.session.id_token is None
    assert sm._id_token_for_logout == "id-original"

    # 3. Service-key cache populated for both services
    assert sm.get_api_key("tiled") == "tiled-secret-v2"
    assert sm.get_api_key("logbook") == "logbook-secret-v2"

    # 4. Mint was called for both services with the original bearer
    urls = {url for url, _ in mint_calls}
    assert "https://tiled.test/api/v1" in urls
    assert "https://logbook.test/api/v1" in urls
    bearers = {b for _, b in mint_calls}
    assert bearers == {"bearer-original"}

    # 5. Consumer call carries Apikey header
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

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].headers.get("Authorization") == "Apikey logbook-secret-v2"

    # 6. Logout: provider.logout receives a session with id_token restored
    asyncio.run(sm.logout())

    assert len(provider.logout_calls) == 1
    logout_session = provider.logout_calls[0]
    assert logout_session.id_token == "id-original"  # restored before call

    # 7. After logout: cache empty, slot cleared, state UNAUTHENTICATED
    assert sm.get_api_key("tiled") is None
    assert sm.get_api_key("logbook") is None
    assert sm._id_token_for_logout is None
    assert sm.state == AuthState.UNAUTHENTICATED

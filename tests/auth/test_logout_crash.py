"""Menu logout must not crash the app (2026-10-15).

Root causes, reproduced:
1. ``KeycloakAuthProvider.logout`` runs on the "logout" worker thread
   (``asyncio.run`` inside a QThreadFuture) and called
   ``QWebEngineProfile...cookieStore().deleteAllCookies()`` there. With a
   QWebEngineView alive (the embedded login browser), Chromium aborts the
   process (0x80000003). The clear must run on the GUI thread.
2. ``_ensure_http`` cached one aiohttp ClientSession forever, but login and
   logout each run their own ``asyncio.run`` loop, so logout's POST failed with
   "Event loop is closed" and the Keycloak SSO session was never ended.
3. Service keys were revoked only after the provider logout, so a hard failure
   there leaked them.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from lightfall.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig
from lightfall.auth.session import Session, SessionManager, User


@pytest.fixture
def provider() -> KeycloakAuthProvider:
    config = KeycloakConfig(
        server_url="http://keycloak.example/auth", realm="test", client_id="test-client"
    )
    return KeycloakAuthProvider(config)


class _Resp:
    status = 204

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttp:
    def __init__(self):
        self.posts = []

    def post(self, url, data=None):
        self.posts.append((url, data))
        return _Resp()


def test_cookie_clear_runs_on_gui_thread(qapp, qtbot, provider, monkeypatch):
    ran_on: list[str] = []
    monkeypatch.setattr(
        KeycloakAuthProvider, "_clear_browser_cookies",
        staticmethod(lambda: ran_on.append(threading.current_thread().name)),
    )
    http = _FakeHttp()

    async def fake_http():
        return http

    monkeypatch.setattr(provider, "_ensure_http", fake_http)
    session = Session(user=User(username="tester"), id_token="idt")

    worker = threading.Thread(target=lambda: asyncio.run(provider.logout(session)),
                              name="logout")
    worker.start()
    worker.join(5)
    qtbot.waitUntil(lambda: bool(ran_on), timeout=3000)
    assert ran_on == ["MainThread"]
    assert http.posts and http.posts[0][1]["id_token_hint"] == "idt"


def test_http_session_is_rebuilt_per_event_loop(provider):
    async def get():
        return await provider._ensure_http()

    first = asyncio.run(get())
    second = asyncio.run(get())
    assert second is not first

    async def twice():
        return await provider._ensure_http(), await provider._ensure_http()

    a, b = asyncio.run(twice())
    assert a is b


@pytest.fixture
def fresh_sm():
    SessionManager.reset()
    yield SessionManager.get_instance()
    SessionManager.reset()


def test_service_keys_revoked_before_provider_logout(fresh_sm, monkeypatch):
    order: list[str] = []

    class Provider:
        async def logout(self, session):
            order.append("provider")
            raise RuntimeError("keycloak step died")

    fresh_sm._provider = Provider()
    fresh_sm._session = Session(user=User(username="tester"))
    monkeypatch.setattr(fresh_sm, "revoke_unleased_service_keys",
                        lambda: order.append("revoke"))
    asyncio.run(fresh_sm.logout())
    assert order[:2] == ["revoke", "provider"]
    assert fresh_sm._session is None

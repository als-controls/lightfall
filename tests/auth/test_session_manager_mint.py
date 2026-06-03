"""Tests for SessionManager's service-key cache + login mint round.

Covers Task 3 (cache surface, this file initially) and Task 4 (login mint).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lightfall.auth.service_key import MintedKey
from lightfall.auth.session import AuthState, Session, SessionManager, User


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

    monkeypatch.setattr("lightfall.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
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
    # Tiled apikey must request `inherit` (a Tiled metascope). Explicit
    # scopes are rejected because the user's `principal.roles` is empty
    # for a fresh Keycloak user — see Tiled's generate_apikey validation.
    assert scopes == ("inherit",) or list(scopes) == ["inherit"]
    assert "lightfall" in note


def test_mint_all_service_keys_tolerates_failure(monkeypatch):
    """A failed mint logs but leaves the slot empty; other services unaffected."""
    import httpx

    sm = SessionManager.get_instance()

    def boom(service_url, bearer, **kwargs):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr("lightfall.auth.session.mint_service_key", boom)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
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

    from lightfall.auth.providers.base import AuthProvider
    from lightfall.auth.session import Session

    sm = SessionManager.get_instance()

    # Stub mint helper + URL resolvers
    monkeypatch.setattr("lightfall.auth.session.mint_service_key", lambda *a, **kw: _minted(secret="login-tiled-key"))
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
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
            from lightfall.auth.session import User
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


def test_attach_session_mints_keys_and_transitions_to_authenticated(monkeypatch):
    """Regression: attach_session must run the mint round.

    LoginDialog previously assigned _session directly and bypassed the
    mint, leaving every consumer to send anonymous requests. The fix is
    to route LoginDialog through attach_session, which is responsible
    for mint + state + signal.
    """
    from lightfall.auth.session import AuthState

    sm = SessionManager.get_instance()
    user_changed_seen = []
    sm.user_changed.connect(lambda u: user_changed_seen.append(u.username))

    monkeypatch.setattr(
        "lightfall.auth.session.mint_service_key",
        lambda *a, **kw: _minted(secret="attached-key"),
    )
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    session = Session(
        user=User(username="tester", attributes={"sub": "kc-sub-1"}),
        token="bearer-abc",
        refresh_token="refresh-abc",
        id_token="id-abc",
    )

    sm.attach_session(session)

    # Mint ran for both services
    assert sm.get_api_key("tiled") == "attached-key"
    assert sm.get_api_key("logbook") == "attached-key"

    # Bearer cleared post-mint (auth-v2 cleanup invariant)
    assert sm.session.token is None
    assert sm._id_token_for_logout == "id-abc"

    # State transitioned and signal fired
    assert sm.state == AuthState.AUTHENTICATED
    assert "tester" in user_changed_seen


def test_attach_session_skips_mint_when_no_bearer(monkeypatch):
    """A session without a Keycloak bearer (e.g. local-auth path) still
    transitions to AUTHENTICATED; mint is skipped gracefully.
    """
    from lightfall.auth.session import AuthState

    sm = SessionManager.get_instance()
    mint_calls: list = []
    monkeypatch.setattr(
        "lightfall.auth.session.mint_service_key",
        lambda *a, **kw: mint_calls.append(a) or _minted(),
    )

    session = Session(user=User(username="local-user"), token=None)
    sm.attach_session(session)

    assert mint_calls == []  # no bearer -> no mint attempt
    assert sm.state == AuthState.AUTHENTICATED
    assert sm.get_api_key("tiled") is None


def test_mint_all_service_keys_mints_both_services(monkeypatch):
    """Both tiled and logbook are minted at login."""
    sm = SessionManager.get_instance()
    called: dict[str, str] = {}
    scopes_by_url: dict[str, tuple] = {}

    def fake_mint(service_url, bearer, *, expires_in, scopes, note, timeout=10.0):
        called[service_url] = bearer
        scopes_by_url[service_url] = tuple(scopes)
        return _minted(secret=f"key-for-{service_url}")

    monkeypatch.setattr("lightfall.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    sm._mint_all_service_keys("bearer-xyz")

    assert sm.get_api_key("tiled") == "key-for-https://tiled.test/api/v1"
    assert sm.get_api_key("logbook") == "key-for-https://logbook.test/api/v1"
    assert called["https://tiled.test/api/v1"] == "bearer-xyz"
    assert called["https://logbook.test/api/v1"] == "bearer-xyz"
    # Logbook is minted with empty scopes (no granular model); tiled is
    # minted with the `inherit` metascope (see _SERVICE_SCOPES comment).
    assert scopes_by_url["https://logbook.test/api/v1"] == ()
    tiled_scopes = scopes_by_url["https://tiled.test/api/v1"]
    assert "inherit" in tiled_scopes


def test_mint_logbook_failure_leaves_tiled_intact(monkeypatch):
    """A failed logbook mint does not interfere with the tiled key."""
    sm = SessionManager.get_instance()

    def fake_mint(service_url, bearer, **kwargs):
        if "logbook" in service_url:
            raise httpx.ConnectError("logbook unreachable")
        return _minted(secret="tiled-secret")

    monkeypatch.setattr("lightfall.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    sm._mint_all_service_keys("bearer-xyz")

    assert sm.get_api_key("tiled") == "tiled-secret"
    assert sm.get_api_key("logbook") is None


def test_tokens_cleared_after_mint(monkeypatch):
    """After mint succeeds, the bearer/refresh/id_token are all cleared
    from the session; the id_token is preserved on the manager slot for
    later RP-initiated logout.
    """
    sm = SessionManager.get_instance()
    sm._session = Session(
        user=User(username="tester", attributes={"sub": "kc-sub-1"}),
        token="bearer-abc",
        refresh_token="refresh-abc",
        id_token="id-abc",
    )

    monkeypatch.setattr(
        "lightfall.auth.session.mint_service_key",
        lambda *a, **kw: _minted(),
    )
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test",
    )
    monkeypatch.setattr(
        "lightfall.logbook.url.get_logbook_base_url",
        lambda: "https://logbook.test",
    )

    sm._mint_all_service_keys("bearer-abc")

    assert sm._session.token is None
    assert sm._session.refresh_token is None
    assert sm._session.id_token is None
    assert sm._id_token_for_logout == "id-abc"


def test_logout_restores_id_token_for_provider(monkeypatch):
    """SessionManager.logout puts id_token back on the session it hands
    to provider.logout, so RP-initiated logout works after the post-mint
    clearing.
    """
    import asyncio

    sm = SessionManager.get_instance()
    sm._session = Session(
        user=User(username="tester"),
        token=None,           # already cleared by _mint_all_service_keys
        refresh_token=None,
        id_token=None,
    )
    sm._id_token_for_logout = "id-stashed"

    captured: dict = {}

    class _StubProvider:
        async def logout(self, session):
            captured["id_token"] = session.id_token

    sm._provider = _StubProvider()

    asyncio.run(sm.logout())

    assert captured["id_token"] == "id-stashed"
    # After logout completes, the slot is cleared
    assert sm._id_token_for_logout is None


def test_reconnect_restores_authenticated_state(monkeypatch):
    """After enter_offline_mode + _attempt_reconnect, state goes back to
    AUTHENTICATED when a session is still live.

    Regression for the state-machine bug found in Task 2 of the auth-v2
    cleanup plan: _on_done used to only call _set_state when session was
    None, leaving an authenticated user stuck in OFFLINE.
    """
    sm = SessionManager.get_instance()
    sm._session = Session(user=User(username="tester"))
    sm._set_state(AuthState.AUTHENTICATED)

    sm.enter_offline_mode()
    assert sm.state == AuthState.OFFLINE
    assert sm.is_offline is True

    # Drive _on_done directly to bypass the QThreadFuture machinery.
    # Find the closure by re-invoking _attempt_reconnect with a stubbed provider.
    class _StubProvider:
        async def check_connectivity(self):
            return True
        @property
        def name(self): return "stub"
        @property
        def supports_password_auth(self): return False
        @property
        def supports_browser_auth(self): return False
        async def authenticate(self, **kwargs): return None
        async def logout(self, session): pass
        async def refresh(self, session): return None

    sm._provider = _StubProvider()

    # Call the reconnect callback semantics directly: simulate "connected=True"
    # by patching QThreadFuture to fire the callback synchronously.
    import lightfall.utils.threads as threads_mod

    class _SyncFuture:
        def __init__(self, fn, *args, callback_slot=None, except_slot=None, **kwargs):
            self._fn = fn
            self._cb = callback_slot
        def start(self):
            result = self._fn()
            if self._cb:
                self._cb(result)

    monkeypatch.setattr(threads_mod, "QThreadFuture", _SyncFuture)

    sm._attempt_reconnect()

    assert sm.state == AuthState.AUTHENTICATED
    assert sm.is_offline is False

# Token Refresh Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dual-path token refresh (timer + on-demand) with a single calculated timer that eliminates race conditions.

**Architecture:** SessionManager owns a one-shot QTimer calculated from the JWT `exp` claim, firing 60s before expiry. KeycloakTiledAuth never calls Keycloak — it only reads the current token and retries if SessionManager already refreshed it. QThreadFuture error logging is made safe against repr failures.

**Tech Stack:** PySide6 (QTimer, QObject, Signal), httpx.Auth, pytest, pytest-qt

**Spec:** `docs/superpowers/specs/2026-04-09-token-refresh-redesign.md`

---

### Task 1: Safe repr in QThreadFuture error logging

This is a standalone fix that prevents the cascading 401 crash. Do it first so subsequent testing doesn't hit the secondary failure.

**Files:**
- Modify: `src/lucid/utils/threads.py:553-560`
- Test: `tests/test_threads.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_threads.py`:

```python
class TestQThreadFutureSafeRepr:
    """Tests for safe repr in error logging."""

    def test_error_logging_survives_repr_failure(self, qapp, qtbot) -> None:
        """QThreadFuture should not crash when args have broken repr."""

        class BadRepr:
            def __repr__(self):
                raise RuntimeError("repr exploded")

        def failing_task(arg):
            raise ValueError("task failed")

        errors = []
        future = QThreadFuture(
            failing_task,
            BadRepr(),
            except_slot=lambda ex: errors.append(ex),
            name="test_bad_repr",
        )
        future.start()
        qtbot.waitUntil(lambda: len(errors) == 1, timeout=3000)

        # The original ValueError should be captured, not a RuntimeError from repr
        assert isinstance(errors[0], ValueError)
        assert "task failed" in str(errors[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_threads.py::TestQThreadFutureSafeRepr::test_error_logging_survives_repr_failure -v`

Expected: FAIL — the `repr(BadRepr())` inside the f-string raises RuntimeError, which either replaces the original ValueError or causes a secondary crash.

- [ ] **Step 3: Implement safe repr**

In `src/lucid/utils/threads.py`, replace lines 553-560:

```python
        except Exception as ex:
            self._exception = ex
            logger.error(
                f"Error in thread '{self._name}': {ex}\n"
                f"Method: {getattr(self._method, '__name__', 'UNKNOWN')}\n"
                f"Args: {self._args}\n"
                f"Kwargs: {self._kwargs}"
            )
```

With:

```python
        except Exception as ex:
            self._exception = ex
            try:
                args_repr = repr(self._args)
                kwargs_repr = repr(self._kwargs)
            except Exception:
                args_repr = f"<{len(self._args)} args, repr failed>"
                kwargs_repr = "<repr failed>"
            logger.error(
                f"Error in thread '{self._name}': {ex}\n"
                f"Method: {getattr(self._method, '__name__', 'UNKNOWN')}\n"
                f"Args: {args_repr}\n"
                f"Kwargs: {kwargs_repr}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_threads.py::TestQThreadFutureSafeRepr -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lucid/utils/threads.py tests/test_threads.py
git commit -m "fix(threads): safe repr in QThreadFuture error logging

repr() on tiled client args triggers HTTP requests that cascade into
secondary 401 errors. Catch repr failures gracefully."
```

---

### Task 2: Remove on-demand refresh from KeycloakTiledAuth

Strip out `_refresh_token_sync` and simplify the auth flows to only check whether
SessionManager already has a fresher token.

**Files:**
- Modify: `src/lucid/services/tiled_auth.py`
- Test: `tests/test_tiled_auth.py` (new file)

- [ ] **Step 1: Write the tests**

Create `tests/test_tiled_auth.py`:

```python
"""Tests for KeycloakTiledAuth with on-demand refresh removed."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from lucid.services.tiled_auth import KeycloakTiledAuth


@pytest.fixture
def auth():
    return KeycloakTiledAuth()


@pytest.fixture
def mock_session_manager():
    """Mock SessionManager singleton with a controllable token."""
    sm = MagicMock()
    sm.session = MagicMock()
    sm.session.token = "token-v1"
    with patch(
        "lucid.services.tiled_auth.SessionManager",
        **{"get_instance.return_value": sm},
    ) as mock_cls:
        mock_cls.get_instance.return_value = sm
        yield sm


class TestSyncAuthFlow:
    """Tests for sync_auth_flow."""

    def test_adds_bearer_token(self, auth, mock_session_manager) -> None:
        """Auth flow should set Authorization header from SessionManager."""
        mock_session_manager.session.token = "my-token"
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        outgoing = next(flow)

        assert outgoing.headers["Authorization"] == "Bearer my-token"

    def test_no_retry_on_success(self, auth, mock_session_manager) -> None:
        """Auth flow should not retry when response is 200."""
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        next(flow)  # yields request

        response = httpx.Response(200, request=request)
        with pytest.raises(StopIteration):
            flow.send(response)

    def test_retries_with_refreshed_token(self, auth, mock_session_manager) -> None:
        """On 401, if SessionManager has a new token, retry with it."""
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        next(flow)  # yields request with token-v1

        # Simulate SessionManager timer refreshing the token
        mock_session_manager.session.token = "token-v2"

        response = httpx.Response(401, request=request)
        retry_request = flow.send(response)

        assert retry_request.headers["Authorization"] == "Bearer token-v2"

    def test_gives_up_when_token_unchanged(self, auth, mock_session_manager) -> None:
        """On 401, if token hasn't changed, don't retry."""
        mock_session_manager.session.token = "stale-token"
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        next(flow)  # yields request

        response = httpx.Response(401, request=request)
        with pytest.raises(StopIteration):
            flow.send(response)

    def test_no_token_sends_unauthenticated(self, auth, mock_session_manager) -> None:
        """With no session token, send request without auth header."""
        mock_session_manager.session = None
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.sync_auth_flow(request)
        outgoing = next(flow)

        assert "Authorization" not in outgoing.headers

    def test_does_not_call_keycloak(self, auth, mock_session_manager) -> None:
        """Auth flow must never call Keycloak directly (no refresh_sync)."""
        assert not hasattr(auth, "_refresh_token_sync"), (
            "_refresh_token_sync should be removed"
        )


class TestAsyncAuthFlow:
    """Tests for async_auth_flow."""

    @pytest.mark.asyncio
    async def test_retries_with_refreshed_token(
        self, auth, mock_session_manager
    ) -> None:
        """On 401, if SessionManager has a new token, retry with it."""
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.async_auth_flow(request)
        await flow.__anext__()  # yields request with token-v1

        mock_session_manager.session.token = "token-v2"

        response = httpx.Response(401, request=request)
        retry_request = await flow.asend(response)

        assert retry_request.headers["Authorization"] == "Bearer token-v2"

    @pytest.mark.asyncio
    async def test_gives_up_when_token_unchanged(
        self, auth, mock_session_manager
    ) -> None:
        """On 401, if token hasn't changed, don't retry."""
        mock_session_manager.session.token = "stale-token"
        request = httpx.Request("GET", "http://example.com/api")

        flow = auth.async_auth_flow(request)
        await flow.__anext__()

        response = httpx.Response(401, request=request)
        with pytest.raises(StopAsyncIteration):
            await flow.asend(response)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tiled_auth.py -v`

Expected: Several failures — `_refresh_token_sync` still exists, `sync_auth_flow` still calls Keycloak on 401 instead of checking current token.

- [ ] **Step 3: Rewrite tiled_auth.py**

Replace the entire content of `src/lucid/services/tiled_auth.py` with:

```python
"""Tiled authentication using Keycloak tokens.

Provides httpx.Auth implementations for authenticating Tiled client
requests using tokens from the SessionManager.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import httpx

from lucid.utils.logging import logger


class KeycloakTiledAuth(httpx.Auth):
    """httpx.Auth that uses Keycloak tokens from SessionManager.

    This auth class fetches the current token from the SessionManager for each
    request. It never calls Keycloak directly — token refresh is handled
    exclusively by SessionManager's scheduled timer.

    On a 401 response, it checks whether SessionManager has since refreshed
    the token and retries once if so.

    Example:
        >>> from tiled.client import from_uri
        >>> auth = KeycloakTiledAuth()
        >>> client = from_uri("https://tiled.example.com", auth=auth)
    """

    def _get_token(self) -> str | None:
        """Get the current access token from SessionManager."""
        from lucid.auth.session import SessionManager

        session = SessionManager.get_instance().session
        return session.token if session else None

    @staticmethod
    def _set_auth(request: httpx.Request, token: str) -> None:
        """Set the Authorization header on a request."""
        request.headers["Authorization"] = f"Bearer {token}"

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Synchronous auth flow for httpx.

        Adds Bearer token from SessionManager to the request.
        If the server responds with 401, checks if SessionManager has a
        newer token and retries once if so.

        Args:
            request: The outgoing request.

        Yields:
            The modified request with Authorization header.
        """
        token = self._get_token()
        if not token:
            logger.debug("No auth token available for Tiled request")
            yield request
            return

        self._set_auth(request, token)
        response = yield request

        if response.status_code != 401:
            return

        # Token was rejected. Check if SessionManager already refreshed it.
        current_token = self._get_token()
        if current_token and current_token != token:
            logger.debug("Using refreshed token for Tiled retry")
            self._set_auth(request, current_token)
            yield request
        # Otherwise: give up. The timer will refresh soon.

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Async auth flow for httpx.

        Adds Bearer token from SessionManager to the request.
        If the server responds with 401, checks if SessionManager has a
        newer token and retries once if so.

        Args:
            request: The outgoing request.

        Yields:
            The modified request with Authorization header.
        """
        token = self._get_token()
        if not token:
            logger.debug("No auth token available for Tiled request")
            yield request
            return

        self._set_auth(request, token)
        response = yield request

        if response.status_code != 401:
            return

        # Token was rejected. Check if SessionManager already refreshed it.
        current_token = self._get_token()
        if current_token and current_token != token:
            logger.debug("Using refreshed token for Tiled retry (async)")
            self._set_auth(request, current_token)
            yield request
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tiled_auth.py -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/lucid/services/tiled_auth.py tests/test_tiled_auth.py
git commit -m "fix(tiled-auth): remove on-demand refresh, eliminate race condition

KeycloakTiledAuth no longer calls Keycloak directly on 401.
It checks if SessionManager already has a refreshed token and
retries with it. Token refresh is now solely owned by
SessionManager's scheduled timer."
```

---

### Task 3: Replace SessionManager polling timer with calculated one-shot

This is the core change. Replace the 30s polling QTimer and `_check_session_expiry`
with `_schedule_refresh` / `_do_scheduled_refresh`.

**Files:**
- Modify: `src/lucid/auth/session.py`
- Test: `tests/test_session_refresh.py` (new file)

- [ ] **Step 1: Write the tests**

Create `tests/test_session_refresh.py`:

```python
"""Tests for SessionManager token refresh scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from lucid.auth.session import AuthState, Session, SessionManager, User


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset SessionManager singleton between tests."""
    SessionManager.reset()
    yield
    SessionManager.reset()


@pytest.fixture
def manager(qapp) -> SessionManager:
    """Create a SessionManager with a mock provider."""
    sm = SessionManager.get_instance()
    provider = MagicMock()
    provider.refresh_sync = MagicMock(return_value=None)
    sm.set_provider(provider)
    return sm


def _make_session(
    expires_in: float = 300.0,
    token: str = "access-tok",
    refresh_token: str = "refresh-tok",
) -> Session:
    """Create a Session that expires `expires_in` seconds from now."""
    now = datetime.now(UTC)
    user = User(
        username="testuser",
        authenticated_at=now,
        expires_at=now + timedelta(seconds=expires_in),
    )
    return Session(user=user, token=token, refresh_token=refresh_token)


class TestScheduleRefresh:
    """Tests for _schedule_refresh timing calculation."""

    def test_schedules_at_60s_before_expiry(self, manager, qapp) -> None:
        """Timer should fire 60s before the token expires."""
        session = _make_session(expires_in=300.0)  # 5 min
        manager._session = session
        manager._set_state(AuthState.AUTHENTICATED)

        with patch.object(manager, "_start_single_shot") as mock_timer:
            manager._schedule_refresh()

        mock_timer.assert_called_once()
        delay_ms = mock_timer.call_args[0][0]
        # Should be ~240s (300 - 60), allow 2s tolerance
        assert abs(delay_ms - 240_000) < 2000

    def test_fires_immediately_when_near_expiry(self, manager, qapp) -> None:
        """If token expires in <60s, fire immediately (delay=0)."""
        session = _make_session(expires_in=30.0)
        manager._session = session
        manager._set_state(AuthState.AUTHENTICATED)

        with patch.object(manager, "_start_single_shot") as mock_timer:
            manager._schedule_refresh()

        delay_ms = mock_timer.call_args[0][0]
        assert delay_ms == 0

    def test_no_schedule_without_session(self, manager, qapp) -> None:
        """No timer if there's no session."""
        manager._session = None

        with patch.object(manager, "_start_single_shot") as mock_timer:
            manager._schedule_refresh()

        mock_timer.assert_not_called()

    def test_no_schedule_without_expires_at(self, manager, qapp) -> None:
        """No timer if session has no expires_at."""
        session = _make_session(expires_in=300.0)
        session.user.expires_at = None
        manager._session = session

        with patch.object(manager, "_start_single_shot") as mock_timer:
            manager._schedule_refresh()

        mock_timer.assert_not_called()


class TestDoScheduledRefresh:
    """Tests for _do_scheduled_refresh execution."""

    def test_successful_refresh_updates_session(self, manager, qapp, qtbot) -> None:
        """After successful refresh, session should be updated."""
        old_session = _make_session(expires_in=10.0, token="old-tok")
        manager._session = old_session
        manager._set_state(AuthState.AUTHENTICATED)

        new_session = _make_session(expires_in=300.0, token="new-tok")
        manager._provider.refresh_sync.return_value = new_session

        with patch.object(manager, "_schedule_refresh") as mock_schedule:
            manager._on_refresh_success(new_session)

        assert manager._session.token == "new-tok"
        assert manager._refresh_in_progress is False
        assert manager._fast_retry_count == 0
        mock_schedule.assert_called_once()

    def test_refresh_in_progress_guard(self, manager, qapp) -> None:
        """Should skip if a refresh is already in progress."""
        manager._session = _make_session(expires_in=10.0)
        manager._set_state(AuthState.AUTHENTICATED)
        manager._refresh_in_progress = True

        with patch.object(manager, "_sync_refresh_session") as mock_refresh:
            manager._do_scheduled_refresh()

        mock_refresh.assert_not_called()

    def test_fast_retry_on_failure(self, manager, qapp) -> None:
        """Failed refresh should retry quickly (up to 3 times)."""
        manager._session = _make_session(expires_in=10.0)
        manager._fast_retry_count = 0

        with patch.object(manager, "_start_single_shot") as mock_timer:
            manager._on_refresh_failure(Exception("network error"))

        assert manager._fast_retry_count == 1
        assert manager._refresh_in_progress is False
        mock_timer.assert_called_once()
        delay_ms = mock_timer.call_args[0][0]
        assert delay_ms == 3000

    def test_fallback_to_slow_retry(self, manager, qapp) -> None:
        """After 3 fast retries, fall back to 30s interval."""
        manager._session = _make_session(expires_in=10.0)
        manager._fast_retry_count = 3  # Already exhausted fast retries

        with patch.object(manager, "_start_single_shot") as mock_timer:
            manager._on_refresh_failure(Exception("still failing"))

        assert manager._fast_retry_count == 4
        delay_ms = mock_timer.call_args[0][0]
        assert delay_ms == 30_000

    def test_verification_rejects_stale_expiry(self, manager, qapp) -> None:
        """Refresh that doesn't advance expires_at should be treated as failure."""
        old_session = _make_session(expires_in=10.0)
        manager._session = old_session

        # New session has same or earlier expiry — suspicious
        stale_session = _make_session(expires_in=5.0, token="new-but-stale")

        with patch.object(manager, "_on_refresh_failure") as mock_fail:
            manager._on_refresh_success(stale_session)

        mock_fail.assert_called_once()


class TestLogoutCleanup:
    """Tests for refresh cleanup on logout."""

    @pytest.mark.asyncio
    async def test_logout_cancels_refresh_timer(self, manager, qapp) -> None:
        """Logout should cancel any pending refresh timer."""
        manager._session = _make_session(expires_in=300.0)
        manager._set_state(AuthState.AUTHENTICATED)
        manager._refresh_in_progress = True
        manager._fast_retry_count = 2
        manager._schedule_refresh()

        await manager.logout()

        assert manager._refresh_in_progress is False
        assert manager._fast_retry_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_refresh.py -v`

Expected: FAIL — `_schedule_refresh`, `_do_scheduled_refresh`, `_on_refresh_success`, `_on_refresh_failure`, `_start_single_shot` don't exist yet. `_refresh_in_progress` and `_fast_retry_count` not defined.

- [ ] **Step 3: Implement the new refresh mechanism in SessionManager**

In `src/lucid/auth/session.py`, make these changes:

**3a. Update `__init__`** — replace the polling timer with new state fields.

Replace lines 175-178:

```python
        # Session expiry timer
        self._expiry_timer = QTimer(self)
        self._expiry_timer.timeout.connect(self._check_session_expiry)
        self._expiry_timer.start(30000)  # Check every 30s (access tokens may be short-lived)
```

With:

```python
        # Token refresh state
        self._refresh_in_progress = False
        self._fast_retry_count = 0
        self._refresh_timer_id: int | None = None
```

**3b. Update `reset()`** — stop the old timer reference is gone, clean up new state.

Replace lines 197-199:

```python
            if cls._instance is not None:
                cls._instance._expiry_timer.stop()
                cls._instance._reconnect_timer.stop()
```

With:

```python
            if cls._instance is not None:
                cls._instance._cancel_refresh_timer()
                cls._instance._reconnect_timer.stop()
```

**3c. Update `login()`** — schedule refresh after successful auth.

In the `login` method, after line 286 (`logger.info("User '{}' authenticated", session.user.username)`), add:

```python
                self._schedule_refresh()
```

**3d. Update `logout()`** — clean up refresh state.

After line 304 (`if self._session is None: return`), before the provider logout call, add:

```python
        self._cancel_refresh_timer()
        self._refresh_in_progress = False
        self._fast_retry_count = 0
```

**3e. Replace `_check_session_expiry` and `_sync_refresh_session`** — delete both methods (lines 348-427) and replace with:

```python
    def _schedule_refresh(self) -> None:
        """Schedule a one-shot timer to refresh the token before it expires.

        Calculates the delay from the current session's expires_at, targeting
        60 seconds before expiry. Called after login and after each successful
        refresh.
        """
        self._cancel_refresh_timer()

        if self._session is None or self._session.user.expires_at is None:
            return

        now = datetime.now(UTC)
        expires_at = self._session.user.expires_at
        delay_s = max(0, (expires_at - now).total_seconds() - 60)
        delay_ms = int(delay_s * 1000)

        logger.debug(
            "Token refresh scheduled in {}s (expires_at={})",
            int(delay_s),
            expires_at.isoformat(),
        )
        self._start_single_shot(delay_ms, self._do_scheduled_refresh)

    def _start_single_shot(self, delay_ms: int, slot: object) -> None:
        """Start a single-shot timer. Separated for testability."""
        self._refresh_timer_id = self.startTimer(delay_ms)

    def _cancel_refresh_timer(self) -> None:
        """Cancel the pending refresh timer if any."""
        if self._refresh_timer_id is not None:
            self.killTimer(self._refresh_timer_id)
            self._refresh_timer_id = None

    def timerEvent(self, event) -> None:  # noqa: N802
        """Handle QObject timer events (used for single-shot refresh)."""
        if event.timerId() == self._refresh_timer_id:
            self.killTimer(self._refresh_timer_id)
            self._refresh_timer_id = None
            self._do_scheduled_refresh()
        else:
            super().timerEvent(event)

    def _do_scheduled_refresh(self) -> None:
        """Execute the scheduled token refresh.

        Runs the actual Keycloak call in a background thread via QThreadFuture.
        Guarded by _refresh_in_progress to prevent concurrent refresh attempts.
        """
        if self._refresh_in_progress:
            logger.debug("Token refresh already in progress, skipping")
            return

        if self._session is None or self._provider is None:
            return

        if not self._session.refresh_token:
            logger.warning("No refresh token available, cannot refresh")
            return

        self._refresh_in_progress = True

        from lucid.utils.threads import QThreadFuture

        # Capture session reference for the background thread
        session = self._session

        def _refresh():
            if hasattr(self._provider, "refresh_sync"):
                return self._provider.refresh_sync(session)
            else:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(
                        self._provider.refresh(session)
                    )
                finally:
                    loop.close()

        QThreadFuture(
            _refresh,
            callback_slot=self._on_refresh_success,
            except_slot=self._on_refresh_failure,
            key="session-token-refresh",
            name="session-token-refresh",
        ).start()

    def _on_refresh_success(self, new_session: Session | None = None) -> None:
        """Handle successful token refresh (called on main thread).

        Verifies the new session has a later expiry, updates state, and
        schedules the next refresh.
        """
        self._refresh_in_progress = False

        if new_session is None:
            self._on_refresh_failure(
                RuntimeError("Provider returned None from refresh")
            )
            return

        # Verify the refresh actually advanced the expiry
        old_expires = (
            self._session.user.expires_at if self._session else None
        )
        new_expires = new_session.user.expires_at
        if old_expires and new_expires and new_expires <= old_expires:
            self._on_refresh_failure(
                RuntimeError(
                    f"Refresh did not advance expiry: "
                    f"{old_expires.isoformat()} -> {new_expires.isoformat()}"
                )
            )
            return

        self._session = new_session
        self._fast_retry_count = 0

        logger.info("Token refresh OK for '{}'", new_session.user.username)
        self._schedule_refresh()

    def _on_refresh_failure(self, exc: Exception) -> None:
        """Handle failed token refresh (called on main thread).

        Retries quickly up to 3 times, then falls back to 30s intervals.
        """
        self._refresh_in_progress = False
        self._fast_retry_count += 1

        if self._fast_retry_count <= 3:
            delay_ms = 3000
            logger.warning(
                "Token refresh failed (attempt {}): {}, retrying in {}ms",
                self._fast_retry_count,
                exc,
                delay_ms,
            )
        else:
            delay_ms = 30_000
            logger.warning(
                "Token refresh failed (attempt {}): {}, backing off to {}s",
                self._fast_retry_count,
                exc,
                delay_ms // 1000,
            )

        self._start_single_shot(delay_ms, self._do_scheduled_refresh)
```

**3f. Remove `session_expiring` signal** — it has no consumers.

Delete from the class body (line 160):

```python
    session_expiring = Signal(int)  # seconds remaining
```

And remove it from the docstring at line 147:

```python
        session_expiring: Emitted when session is about to expire (seconds_remaining).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_refresh.py -v`

Expected: All PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `python -m pytest tests/ -v --timeout=30`

Expected: No regressions. Nothing connects to `session_expiring`. The old `_expiry_timer` and `_check_session_expiry` are fully replaced.

- [ ] **Step 6: Commit**

```bash
git add src/lucid/auth/session.py tests/test_session_refresh.py
git commit -m "fix(auth): replace polling timer with calculated one-shot refresh

- Timer fires exactly 60s before token expiry instead of polling every 30s
- On failure: 3 fast retries (3s), then 30s fallback
- Refresh-in-progress guard prevents concurrent attempts
- Verification: new expiry must advance past old expiry
- Remove unused session_expiring signal"
```

---

### Task 4: Integration smoke test

Verify the full chain works together: SessionManager refresh updates the token
that KeycloakTiledAuth reads.

**Files:**
- Test: `tests/test_tiled_auth.py` (append)

- [ ] **Step 1: Write the integration test**

Append to `tests/test_tiled_auth.py`:

```python
class TestIntegrationWithSessionManager:
    """Verify KeycloakTiledAuth reads tokens refreshed by SessionManager."""

    def test_auth_picks_up_timer_refreshed_token(self, qapp) -> None:
        """After SessionManager refreshes, the next tiled request uses the new token."""
        from datetime import UTC, datetime, timedelta

        from lucid.auth.session import Session, SessionManager, User

        SessionManager.reset()
        sm = SessionManager.get_instance()

        # Set up an initial session
        now = datetime.now(UTC)
        user = User(
            username="test",
            authenticated_at=now,
            expires_at=now + timedelta(seconds=300),
        )
        sm._session = Session(
            user=user, token="original-token", refresh_token="rt"
        )

        auth = KeycloakTiledAuth()

        # Verify auth reads the current token
        request = httpx.Request("GET", "http://example.com/api")
        flow = auth.sync_auth_flow(request)
        outgoing = next(flow)
        assert outgoing.headers["Authorization"] == "Bearer original-token"

        # Simulate what SessionManager._on_refresh_success does
        new_user = User(
            username="test",
            authenticated_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        sm._session = Session(
            user=new_user, token="refreshed-token", refresh_token="rt2"
        )

        # On 401, auth should pick up the refreshed token
        response = httpx.Response(401, request=request)
        retry_request = flow.send(response)
        assert retry_request.headers["Authorization"] == "Bearer refreshed-token"

        SessionManager.reset()
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_tiled_auth.py::TestIntegrationWithSessionManager -v`

Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v --timeout=30`

Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_tiled_auth.py
git commit -m "test(auth): add integration test for timer-refreshed token flow"
```

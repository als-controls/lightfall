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

        # _do_scheduled_refresh should return early without starting a QThreadFuture
        with patch("lucid.utils.threads.QThreadFuture") as mock_qtf:
            manager._do_scheduled_refresh()

        mock_qtf.assert_not_called()

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

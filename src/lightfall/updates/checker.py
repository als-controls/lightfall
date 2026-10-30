"""The shared background update checker behind the update and git-state indicators.

Active only while someone holding ``Permission.ADMIN_SYSTEM`` is logged in and
the session is online; otherwise no checks run and the result is empty, so both
indicators stay hidden. Checks run on a ``QThreadFuture`` worker.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.auth.policy import Permission
from lightfall.auth.session import AuthState, SessionManager
from lightfall.updates.model import CheckResult, run_check
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

INITIAL_DELAY_MS = 30_000
FETCH_INTERVAL_MS = 3_600_000
LOCAL_INTERVAL_MS = 300_000


class UpdateChecker(QObject):
    """Runs update checks on a timer while the session allows it; emits each result."""

    result_changed = Signal(object)  # CheckResult
    restart_pending_changed = Signal(bool)

    def __init__(
        self,
        session: Any | None = None,
        check: Callable[[bool], CheckResult] = run_check,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session if session is not None else SessionManager.get_instance()
        self._check = check
        self._result = CheckResult.EMPTY
        self._active = False
        self._running = False
        self._generation = 0
        self._restart_pending = False
        self._future: QThreadFuture | None = None

        self._initial_timer = QTimer(self)
        self._initial_timer.setSingleShot(True)
        self._initial_timer.setInterval(INITIAL_DELAY_MS)
        self._initial_timer.timeout.connect(lambda: self.check_now(fetch=True))
        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(FETCH_INTERVAL_MS)
        self._fetch_timer.timeout.connect(lambda: self.check_now(fetch=True))
        self._local_timer = QTimer(self)
        self._local_timer.setInterval(LOCAL_INTERVAL_MS)
        self._local_timer.timeout.connect(lambda: self.check_now(fetch=False))

        self._session.state_changed.connect(self._on_session_changed)
        self._session.offline_mode_changed.connect(self._on_session_changed)
        self._session.user_changed.connect(self._on_session_changed)

    # -- state ----------------------------------------------------------------

    @property
    def result(self) -> CheckResult:
        return self._result

    @property
    def active(self) -> bool:
        return self._active

    @property
    def restart_pending(self) -> bool:
        return self._restart_pending

    def is_allowed(self) -> bool:
        """Logged in, online, and holding ``ADMIN_SYSTEM``."""
        session = self._session
        return (
            not session.is_offline
            and session.state == AuthState.AUTHENTICATED
            and session.check_permission(Permission.ADMIN_SYSTEM)
        )

    # -- gate -----------------------------------------------------------------

    def _on_session_changed(self, *args: Any) -> None:
        self.refresh_gate()

    def refresh_gate(self) -> None:
        """Start or stop checking to match the session's current permission."""
        allowed = self.is_allowed()
        if allowed and not self._active:
            self._active = True
            self._initial_timer.start()
            self._fetch_timer.start()
            self._local_timer.start()
        elif not allowed and self._active:
            self._active = False
            self._generation += 1
            for timer in (self._initial_timer, self._fetch_timer, self._local_timer):
                timer.stop()
            self._set_result(CheckResult.EMPTY)

    # -- checks ---------------------------------------------------------------

    def check_now(self, fetch: bool = True) -> None:
        """Start a check in the background (no-op if inactive or one is running)."""
        if not self._active or self._running:
            return
        self._running = True
        generation = self._generation
        check = self._check

        def work() -> tuple[int, bool, CheckResult]:
            return generation, fetch, check(fetch)

        self._future = QThreadFuture(
            work, callback_slot=self._on_result, except_slot=self._on_error,
            name="update_check",
        )
        self._future.start()

    def _on_result(self, payload: tuple[int, bool, CheckResult]) -> None:
        self._running = False
        generation, fetched, result = payload
        if generation != self._generation or not self._active:
            return
        if not fetched:
            # A local refresh doesn't ask the index; keep the last PyPI answers.
            result = CheckResult(result.repos, self._result.pypi)
        self._set_result(result)

    def _on_error(self, exc: Exception) -> None:
        self._running = False
        logger.debug("Update check failed: {}", exc)

    def _set_result(self, result: CheckResult) -> None:
        self._result = result
        self.result_changed.emit(result)

    def mark_restart_pending(self) -> None:
        if not self._restart_pending:
            self._restart_pending = True
            self.restart_pending_changed.emit(True)


_checker: UpdateChecker | None = None


def get_update_checker() -> UpdateChecker:
    """The process-wide checker shared by the update and git-state indicators."""
    global _checker
    if _checker is None:
        _checker = UpdateChecker()
    return _checker

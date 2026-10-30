"""Tests for the shared, session-gated background update checker."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from lightfall.auth.policy import Permission
from lightfall.auth.session import AuthState, User
from lightfall.updates.checker import (
    FETCH_INTERVAL_MS,
    INITIAL_DELAY_MS,
    LOCAL_INTERVAL_MS,
    UpdateChecker,
)
from lightfall.updates.git import RepoState
from lightfall.updates.model import CheckResult
from lightfall.updates.pypi import PypiState


class FakeSession(QObject):
    state_changed = Signal(AuthState, AuthState)
    user_changed = Signal(User)
    offline_mode_changed = Signal(bool)

    def __init__(self, state=AuthState.UNAUTHENTICATED, offline=False, allowed=True):
        super().__init__()
        self.state = state
        self.is_offline = offline
        self.allowed = allowed
        self.asked: list[Permission] = []

    def check_permission(self, permission, context=None):
        self.asked.append(permission)
        return self.allowed

    def login(self):
        old, self.state = self.state, AuthState.AUTHENTICATED
        self.state_changed.emit(self.state, old)

    def logout(self):
        old, self.state = self.state, AuthState.UNAUTHENTICATED
        self.state_changed.emit(self.state, old)


REPO = RepoState(Path("lightfall"), ("lightfall",), "master", "master", "origin", False,
                 "a", 0, 2, "b")
RESULT = CheckResult(repos=(REPO,))


class FakeCheck:
    def __init__(self, result=RESULT, gate: threading.Event | None = None):
        self.calls: list[bool] = []
        self.result = result
        self.gate = gate

    def __call__(self, fetch: bool) -> CheckResult:
        self.calls.append(fetch)
        if self.gate is not None:
            self.gate.wait(5)
        return self.result


def make(qtbot, session, check=None):
    checker = UpdateChecker(session=session, check=check or FakeCheck())
    checker.refresh_gate()
    return checker


def test_inactive_when_anonymous(qtbot):
    check = FakeCheck()
    checker = make(qtbot, FakeSession(), check)
    assert checker.active is False
    checker.check_now()
    qtbot.wait(50)
    assert check.calls == []


def test_inactive_when_offline_or_no_permission(qtbot):
    assert make(qtbot, FakeSession(AuthState.AUTHENTICATED, offline=True)).active is False
    session = FakeSession(AuthState.AUTHENTICATED, allowed=False)
    assert make(qtbot, session).active is False
    assert Permission.ADMIN_SYSTEM in session.asked


def test_activates_on_login_and_emits_result(qtbot):
    session = FakeSession()
    checker = make(qtbot, session)
    session.login()
    assert checker.active
    with qtbot.waitSignal(checker.result_changed, timeout=5000) as blocker:
        checker.check_now()
    assert blocker.args == [RESULT]
    assert checker.result.update_count == 1


def test_logout_clears_result(qtbot):
    session = FakeSession(AuthState.AUTHENTICATED)
    checker = make(qtbot, session)
    with qtbot.waitSignal(checker.result_changed, timeout=5000):
        checker.check_now()
    with qtbot.waitSignal(checker.result_changed, timeout=1000) as blocker:
        session.logout()
    assert blocker.args == [CheckResult.EMPTY]
    assert checker.active is False


def test_result_after_deactivation_is_dropped(qtbot):
    gate = threading.Event()
    session = FakeSession(AuthState.AUTHENTICATED)
    checker = make(qtbot, session, FakeCheck(gate=gate))
    checker.check_now()
    session.logout()
    gate.set()
    qtbot.wait(500)
    assert checker.result == CheckResult.EMPTY


def test_local_check_keeps_last_pypi_results(qtbot):
    pypi = (PypiState("lightfall-deck", "1.0", "1.1"),)
    check = FakeCheck(CheckResult(repos=(REPO,), pypi=pypi))
    checker = make(qtbot, FakeSession(AuthState.AUTHENTICATED), check)
    with qtbot.waitSignal(checker.result_changed, timeout=5000):
        checker.check_now(fetch=True)
    check.result = CheckResult(repos=(REPO,))
    with qtbot.waitSignal(checker.result_changed, timeout=5000):
        checker.check_now(fetch=False)
    assert checker.result.pypi == pypi


def test_timer_intervals(qtbot):
    checker = make(qtbot, FakeSession(AuthState.AUTHENTICATED))
    assert INITIAL_DELAY_MS == 30_000
    assert FETCH_INTERVAL_MS == 3_600_000
    assert LOCAL_INTERVAL_MS == 300_000
    assert checker._initial_timer.interval() == INITIAL_DELAY_MS
    assert checker._fetch_timer.interval() == FETCH_INTERVAL_MS
    assert checker._local_timer.interval() == LOCAL_INTERVAL_MS
    assert checker._fetch_timer.isActive() and checker._local_timer.isActive()


def test_restart_pending(qtbot):
    checker = make(qtbot, FakeSession(AuthState.AUTHENTICATED))
    with qtbot.waitSignal(checker.restart_pending_changed, timeout=1000) as blocker:
        checker.mark_restart_pending()
    assert blocker.args == [True]
    assert checker.restart_pending

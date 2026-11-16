"""Push column of the git-state dialog (all modals and pushes are faked)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from lightfall.github.push import PushResult, PushTarget
from lightfall.github.session import GitHubCredential, GitHubSession
from lightfall.ui.dialogs import git_state_dialog as gsd
from lightfall.ui.dialogs.git_state_dialog import PUSH_COL, STATE_COL, GitStateDialog
from tests.updates.fakes import FakeChecker, repo


class FakeAuth(QObject):
    state_changed = Signal(object, object)
    user_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.current_user = SimpleNamespace(username="u1")


def good_target(root: Path) -> PushTarget:
    return PushTarget(root=root, branch="feat/x", remote="origin",
                      url="https://github.com/als-controls/lightfall.git",
                      has_upstream=True, ahead=1)


@pytest.fixture
def session(qapp):
    return GitHubSession(auth_session=FakeAuth())


@pytest.fixture
def signed_in(session):
    session.set_credential(GitHubCredential(
        token="github_pat_SECRET", kind="pat", login="octo", expires_at=None,
        lightfall_user="u1"))
    return session


def make(qtbot, session, *, target=good_target, push=None, checker=None):
    dlg = GitStateDialog([repo("lightfall", branch="feat/x")], checker=checker,
                         github_session=session, push=push, target=target)
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.table.cellWidget(0, PUSH_COL).text() != "Checking…")
    return dlg


def button(dlg):
    return dlg.table.cellWidget(0, PUSH_COL)


def test_button_disabled_with_reason(qtbot, signed_in):
    dlg = make(qtbot, signed_in, target=lambda root: "detached HEAD")
    assert not button(dlg).isEnabled()
    assert button(dlg).toolTip() == "detached HEAD"


def test_checking_state_until_targets_ready(qtbot, signed_in):
    dlg = GitStateDialog([repo("lightfall")], github_session=signed_in, target=good_target)
    qtbot.addWidget(dlg)
    assert button(dlg).text() == "Checking…"
    assert not button(dlg).isEnabled()


def test_sign_in_button_when_signed_out(qtbot, session, monkeypatch):
    calls = []
    monkeypatch.setattr(gsd, "start_sign_in", lambda parent, gh=None: calls.append(parent))
    dlg = make(qtbot, session)
    assert button(dlg).text() == "Sign in to push"
    button(dlg).click()
    assert calls == [dlg]


def test_push_confirm_yes_runs_and_rechecks(qtbot, signed_in, monkeypatch):
    asked = []

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    pushed = []

    def fake_push(target, cred, **kw):
        pushed.append((target.branch, cred.login, kw))
        return PushResult(True, "pushed feat/x", target, None, "bbb")

    checker = FakeChecker()
    dlg = make(qtbot, signed_in, push=fake_push, checker=checker)
    assert button(dlg).text() == "Push"
    button(dlg).click()
    qtbot.waitUntil(lambda: dlg.table.item(0, STATE_COL).text().startswith("✓"))
    assert asked == ["Push feat/x of lightfall to github.com/als-controls/lightfall as octo?"]
    assert pushed == [("feat/x", "octo", {})]
    assert checker.checks == [True]


def test_push_failure_shows_cross(qtbot, signed_in, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    checker = FakeChecker()
    dlg = make(qtbot, signed_in, checker=checker,
               push=lambda t, c, **kw: PushResult(False, "rejected", t, None, None))
    button(dlg).click()
    qtbot.waitUntil(lambda: dlg.table.item(0, STATE_COL).text().startswith("✗"))
    assert checker.checks == []


def test_push_confirm_no_does_nothing(qtbot, signed_in, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    pushed = []
    checker = FakeChecker()
    dlg = make(qtbot, signed_in, checker=checker, push=lambda *a, **k: pushed.append(1))
    before = dlg.table.item(0, STATE_COL).text()
    button(dlg).click()
    assert pushed == []
    assert checker.checks == []
    assert dlg.table.item(0, STATE_COL).text() == before
    assert button(dlg).isEnabled()


def test_dialog_rebuilds_on_credential_change(qtbot, signed_in):
    dlg = make(qtbot, signed_in)
    assert button(dlg).text() == "Push"
    signed_in.sign_out()
    assert button(dlg).text() == "Sign in to push"


def test_disabled_when_checker_not_allowed(qtbot, signed_in):
    dlg = make(qtbot, signed_in, checker=FakeChecker(allowed=False))
    assert not button(dlg).isEnabled()

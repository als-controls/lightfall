"""Tests for the GitHub sign-in dialogs and the link-account confirmation."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from lightfall.github.auth import DeviceCode, GitHubAuthError, TokenGrant
from lightfall.github.session import GitHubCredential, GitHubSession
from lightfall.ui.dialogs import github_sign_in as gsi
from lightfall.ui.dialogs.github_sign_in import DeviceSignInDialog, PatSignInDialog, confirm_login
from lightfall.ui.preferences import PreferencesManager


class FakePrefs:
    def __init__(self, **values):
        self.values = dict(values)
        self.sets: list[tuple[str, object]] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, **kwargs):
        self.sets.append((key, value))
        self.values[key] = value


def make_cred(login="octo", user="u1"):
    return GitHubCredential(
        token="github_pat_SECRET", kind="app", login=login, expires_at=None, lightfall_user=user
    )


CODE = DeviceCode("dev", "ABCD-1234", "https://github.com/login/device", 5, 900)


@pytest.fixture(autouse=True)
def _no_modals(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("unpatched modal")

    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(refuse))
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: True))


@pytest.fixture
def prefs(monkeypatch):
    fake = FakePrefs()
    monkeypatch.setattr(PreferencesManager, "get_instance", staticmethod(lambda: fake))
    return fake


@pytest.fixture
def session(qtbot):
    return GitHubSession()


def test_device_dialog_success_sets_credential(qtbot, session, prefs, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dialog = DeviceSignInDialog(
        session, "u1",
        request=lambda cid: CODE,
        poll=lambda cid, code, cancelled=None, **k: TokenGrant("tok", None),
        make_credential=lambda grant, user: make_cred(),
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(dialog.result, timeout=5000)
    assert session.credential().login == "octo"
    assert ("github_login", "octo") in prefs.sets
    assert "ABCD-1234" in dialog.code_edit.text()


def test_device_dialog_expired_shows_reason(qtbot, session, prefs):
    def poll(cid, code, cancelled=None, **k):
        raise GitHubAuthError("expired", "Device code expired")

    dialog = DeviceSignInDialog(
        session, "u1", request=lambda cid: CODE, poll=poll,
        make_credential=lambda grant, user: make_cred(),
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "expired" in dialog.status_label.text(), timeout=5000)
    assert session.credential() is None


def test_device_dialog_cancel_stops_polling(qtbot, session, prefs):
    seen = {}

    def poll(cid, code, cancelled=None, **k):
        seen["cancelled"] = cancelled
        for _ in range(100):
            if cancelled():
                raise GitHubAuthError("cancelled")
            time.sleep(0.02)
        return TokenGrant("tok", None)

    dialog = DeviceSignInDialog(
        session, "u1", request=lambda cid: CODE, poll=poll,
        make_credential=lambda grant, user: make_cred(),
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "cancelled" in seen, timeout=5000)
    dialog.cancel_button.click()
    assert seen["cancelled"]() is True
    assert session.credential() is None


def test_device_dialog_late_result_after_cancel_ignored(qtbot, session, prefs):
    dialog = DeviceSignInDialog(
        session, "u1", request=lambda cid: CODE,
        poll=lambda cid, code, cancelled=None, **k: TokenGrant("tok", None),
        make_credential=lambda grant, user: make_cred(),
    )
    qtbot.addWidget(dialog)
    dialog.cancel_button.click()
    dialog._on_result(make_cred())
    assert session.credential() is None


def test_pat_dialog_rejects_classic_without_network(qtbot, session, prefs):
    calls = []
    dialog = PatSignInDialog(
        session, "u1", make_credential=lambda token, user: calls.append(token),
    )
    qtbot.addWidget(dialog)
    dialog.token_edit.setText("ghp_x")
    dialog.sign_in_button.click()
    assert "Only fine-grained tokens (github_pat_" in dialog.error_label.text()
    assert calls == []


def test_pat_dialog_success(qtbot, session, prefs, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    dialog = PatSignInDialog(session, "u1", make_credential=lambda token, user: make_cred())
    qtbot.addWidget(dialog)
    dialog.token_edit.setText("github_pat_abc")
    dialog.sign_in_button.click()
    qtbot.waitUntil(dialog.result, timeout=5000)
    assert session.credential().login == "octo"
    assert dialog.token_edit.text() == ""


def test_confirm_login_saves_when_unset(qtbot, prefs, monkeypatch):
    asked = []

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    assert confirm_login(make_cred(), None) is True
    assert "Link GitHub account octo to this Lightfall user?" in asked[0]
    assert prefs.sets == [("github_login", "octo")]


def test_confirm_login_matching_is_silent(qtbot, prefs):
    prefs.values["github_login"] = "octo"
    assert confirm_login(make_cred(), None) is True
    assert prefs.sets == []


def test_confirm_login_mismatch_declined_signs_out(qtbot, prefs, monkeypatch):
    session = GitHubSession()
    cred = make_cred(login="other")
    session.set_credential(cred)
    prefs.values["github_login"] = "octo"
    seen = []

    def warning(parent, title, text, *a, **k):
        seen.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))
    monkeypatch.setattr(gsi, "get_github_session", lambda: session)
    assert confirm_login(cred, None) is False
    assert "You signed in as other, but this Lightfall user is linked to octo" in seen[0]
    assert session.credential() is None


def test_start_sign_in_opens_device_dialog(qtbot, monkeypatch):
    opened = []

    class Fake:
        def __init__(self, *a, **k):
            opened.append("device")

        def exec(self):
            return 0

    monkeypatch.setattr(gsi, "DeviceSignInDialog", Fake)
    monkeypatch.setattr(gsi, "client_id", lambda: "abc")
    monkeypatch.setattr(gsi, "_current_username", lambda: "u1")
    monkeypatch.setattr(gsi, "get_github_session", lambda: SimpleNamespace())
    gsi.start_sign_in(None)
    assert opened == ["device"]

"""Tests for the GitHub Preferences page and its status widget."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from lightfall.github.session import GitHubCredential, GitHubSession
from lightfall.scopes.keys import is_secret_key
from lightfall.ui.preferences import github_settings as gs
from lightfall.ui.preferences.github_settings import GitHubSettingsPlugin, GitHubStatusWidget


class FakeChecker(QObject):
    result_changed = Signal(object)

    def __init__(self, allowed=True):
        super().__init__()
        self.allowed = allowed

    def is_allowed(self):
        return self.allowed


def cred():
    return GitHubCredential("github_pat_x", "pat", "octo", None, "u1")


def test_settings_specs_scopes():
    specs = {s.key: s for s in GitHubSettingsPlugin().specs()}
    assert specs["github_login"].scopes == frozenset({"user"})
    assert specs["github_app_client_id"].scopes == frozenset({"facility", "beamline", "branch"})
    assert not is_secret_key("github_login")
    assert not is_secret_key("github_app_client_id")
    assert all(s.page == "github" for s in specs.values())


def test_page_metadata_and_sections():
    plugin = GitHubSettingsPlugin()
    assert (plugin.name, plugin.display_name, plugin.category) == ("github", "GitHub", "general")
    assert [s.title for s in plugin.sections()] == ["GitHub account", "GitHub App"]


def test_status_widget_reflects_session(qtbot):
    session = GitHubSession()
    w = GitHubStatusWidget(github_session=session, checker=FakeChecker())
    qtbot.addWidget(w)
    assert "Not signed in" in w.status_label.text()
    session.set_credential(cred())
    assert "signed in as octo" in w.status_label.text()
    assert "fine-grained token" in w.status_label.text()
    session.sign_out()
    assert "Not signed in" in w.status_label.text()


def test_status_widget_gated(qtbot):
    w = GitHubStatusWidget(github_session=GitHubSession(), checker=FakeChecker(allowed=False))
    qtbot.addWidget(w)
    assert not w.sign_in_button.isEnabled()
    assert not w.token_button.isEnabled()
    assert "Pushing requires the system-admin permission." in w.status_label.text()


def test_status_widget_buttons(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(gs, "start_sign_in", lambda parent: calls.append("device"))
    monkeypatch.setattr(gs, "start_pat_sign_in", lambda parent: calls.append("pat"))
    session = GitHubSession()
    session.set_credential(cred())
    w = GitHubStatusWidget(github_session=session, checker=FakeChecker())
    qtbot.addWidget(w)
    w.sign_in_button.click()
    w.token_button.click()
    assert calls == ["device", "pat"]
    w.sign_out_button.click()
    assert session.credential() is None
    assert not w.sign_out_button.isEnabled()


def test_manifest_registers_github_after_login():
    from lightfall.plugins.builtin_manifest import builtin_manifest

    names = [e.name for e in builtin_manifest.plugins if e.type_name == "settings"]
    assert names.index("github") == names.index("login") + 1

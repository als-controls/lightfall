"""Tests for the registry-driven login dialog (Task B5)."""
from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtWidgets import QPushButton

from lightfall.auth.provider_registry import AuthProviderRegistry
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin
from lightfall.ui.dialogs.login_dialog import LoginDialog


class _FakeSession:
    def __init__(self):
        self.user = MagicMock()


class _OkProvider:
    async def authenticate(self, username=None, password=None, **kwargs):
        return _FakeSession()


class _NoFormPlugin(AuthProviderPlugin):
    @property
    def name(self): return "nsls2_tiled"
    @property
    def display_name(self): return "NSLS-II (CMS)"
    @property
    def requires_username(self): return False
    @property
    def requires_password(self): return False
    def create_provider(self): return _OkProvider()


def test_dialog_renders_button_per_registered_provider(qapp):
    AuthProviderRegistry.reset()
    AuthProviderRegistry.get_instance().register(_NoFormPlugin())

    dialog = LoginDialog(allow_guest=True)
    labels = [b.text() for b in dialog.findChildren(QPushButton)]
    assert "Login with NSLS-II (CMS)" in labels
    AuthProviderRegistry.reset()


def test_do_provider_login_success(qapp):
    AuthProviderRegistry.reset()
    dialog = LoginDialog(allow_guest=True)
    dialog._session_manager = MagicMock()

    assert dialog._do_provider_login(_NoFormPlugin(), "", "") is True
    dialog._session_manager.set_provider.assert_called_once()
    dialog._session_manager.attach_session.assert_called_once()
    AuthProviderRegistry.reset()


class _ResumablePlugin(_NoFormPlugin):
    def resume_name(self):
        return "Ron Pandolfi"


def test_resume_offers_continue_and_someone_else(qapp):
    AuthProviderRegistry.reset()
    AuthProviderRegistry.get_instance().register(_ResumablePlugin())

    dialog = LoginDialog(allow_guest=True)
    labels = [b.text() for b in dialog.findChildren(QPushButton)]
    assert "Continue as Ron Pandolfi" in labels
    assert "Sign in as someone else" in labels
    assert "Login with NSLS-II (CMS)" not in labels
    AuthProviderRegistry.reset()


def test_continue_and_someone_else_pass_auth_kwargs(qapp, monkeypatch):
    AuthProviderRegistry.reset()
    AuthProviderRegistry.get_instance().register(_ResumablePlugin())
    dialog = LoginDialog(allow_guest=True)
    calls = []
    monkeypatch.setattr(
        dialog, "_start_provider_login", lambda p, u, pw, kw=None: calls.append(kw)
    )
    buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
    buttons["Continue as Ron Pandolfi"].click()
    buttons["Sign in as someone else"].click()
    assert calls == [{"resume": True}, {"fresh_login": True}]
    AuthProviderRegistry.reset()


def test_do_provider_login_forwards_auth_kwargs(qapp):
    seen = {}

    class _Provider:
        async def authenticate(self, username=None, password=None, **kwargs):
            seen.update(kwargs)
            return _FakeSession()

    class _Plugin(_NoFormPlugin):
        def create_provider(self):
            return _Provider()

    AuthProviderRegistry.reset()
    dialog = LoginDialog(allow_guest=True)
    dialog._session_manager = MagicMock()
    assert dialog._do_provider_login(_Plugin(), "", "", {"resume": True}) is True
    assert seen == {"resume": True}
    AuthProviderRegistry.reset()

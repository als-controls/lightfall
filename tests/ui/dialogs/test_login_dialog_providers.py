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
    assert "NSLS-II (CMS)" in labels
    AuthProviderRegistry.reset()


def test_do_provider_login_success(qapp):
    AuthProviderRegistry.reset()
    dialog = LoginDialog(allow_guest=True)
    dialog._session_manager = MagicMock()

    assert dialog._do_provider_login(_NoFormPlugin(), "", "") is True
    dialog._session_manager.set_provider.assert_called_once()
    dialog._session_manager.attach_session.assert_called_once()
    AuthProviderRegistry.reset()

# tests/ui/test_user_profile_plugin.py
"""UI-side tests for UserProfileSettingsPlugin.

Uses pytest-qt's qtbot fixture and a stubbed Session so the widget can be
constructed without a real auth backend. UserSettingsClient calls are
intercepted via the singleton's reset/init pattern + httpx_mock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@dataclass
class _StubUser:
    username: str = "rpandolfi"
    display_name: str = "Ron Pandolfi"
    email: str = "rp@lbl.gov"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class _StubSession:
    user: _StubUser = field(default_factory=_StubUser)


@pytest.fixture(autouse=True)
def _reset_settings_client():
    from lucid.settings.user_settings_client import UserSettingsClient
    UserSettingsClient.reset()
    UserSettingsClient.init(base_url="https://lb.test")
    yield
    UserSettingsClient.reset()


@pytest.fixture
def stub_session(monkeypatch):
    """Patch SessionManager.get_instance() to return a stub session."""
    from lucid.auth import session as session_mod

    sm = MagicMock()
    sm.session = _StubSession()
    monkeypatch.setattr(
        session_mod.SessionManager, "get_instance", classmethod(lambda cls: sm)
    )
    return sm


def test_plugin_metadata():
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    assert p.name == "user_profile"
    assert p.display_name == "User Profile"
    assert p.category == "general"
    assert p.priority == 1


def test_create_widget_shows_identity_labels(qtbot, stub_session):
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    text = w.findChildren(type(w))  # silence unused-import
    # Walk all QLabels and assert username/email/display name appear
    from PySide6.QtWidgets import QLabel
    label_text = " ".join(
        lbl.text() for lbl in w.findChildren(QLabel)
    )
    assert "rpandolfi" in label_text
    assert "rp@lbl.gov" in label_text
    assert "Ron Pandolfi" in label_text


def test_orcid_row_hidden_when_absent(qtbot, stub_session):
    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    from PySide6.QtWidgets import QLabel
    label_text = " ".join(lbl.text() for lbl in w.findChildren(QLabel))
    assert "ORCID" not in label_text


def test_orcid_row_shown_when_present(qtbot, monkeypatch):
    from lucid.auth import session as session_mod
    user = _StubUser(attributes={"orcid": "0000-0001-2345-6789"})
    sm = MagicMock()
    sm.session = _StubSession(user=user)
    monkeypatch.setattr(
        session_mod.SessionManager, "get_instance", classmethod(lambda cls: sm)
    )

    from lucid.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)

    from PySide6.QtWidgets import QLabel
    label_text = " ".join(lbl.text() for lbl in w.findChildren(QLabel))
    assert "0000-0001-2345-6789" in label_text

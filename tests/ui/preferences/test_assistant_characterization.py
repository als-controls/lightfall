"""Pins the old Assistant page's save/validate behaviour (removed in Task 4)."""
from __future__ import annotations

import pytest

from lightfall.agents.backends.info import AuthStatus, ModelOption
from lightfall.ui.preferences import agent_backend_section as abs_mod
from lightfall.ui.preferences import claude_settings as cs


class _RecordingPrefs:
    def __init__(self):
        self.writes: list[tuple] = []

    def get(self, key, default=None):
        return default

    def set(self, key, value, scope=None):
        self.writes.append((key, value, scope))


class _Info:
    kind = "claude-code"

    def auth_status(self, refresh=False):
        return AuthStatus(True, "ok")

    def list_models(self, refresh=False):
        return [ModelOption("", "Default")]

    def sign_in_argv(self):
        return None

    def sign_in_env(self):
        return {}


@pytest.fixture
def page(qtbot, monkeypatch):
    prefs = _RecordingPrefs()
    monkeypatch.setattr(cs.PreferencesManager, "get_instance", classmethod(lambda cls: prefs))
    monkeypatch.setattr(cs, "check_oauth_status", lambda: (False, "Not logged in"))
    monkeypatch.setattr(cs, "get_cached_models", lambda *a, **k: None)
    monkeypatch.setattr(cs, "cached_model_ids", lambda *a, **k: None)
    monkeypatch.setattr(abs_mod, "backend_info", lambda *a, **k: _Info())
    plugin = cs.ClaudeSettingsPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.load_settings()
    yield plugin, prefs
    plugin._backend.stop()


def test_old_page_saves_every_key_unscoped(page):
    plugin, prefs = page
    plugin.save_settings()
    assert prefs.writes == [
        ("claude_endpoint", "anthropic", None),
        ("claude_custom_url", "", None),
        ("claude_disable_betas", False, None),
        ("claude_api_key", "", None),
        ("claude_model", "", None),
        ("claude_effort", "", None),
        ("claude_max_turns", 20, None),
        ("claude_permission_mode", "default", None),
        ("claude_auto_restore", False, None),
        ("agent_backend", "claude-code", None),
        ("agy_path", "", None),
        ("agy_gcp_project", "lbl-gemini-enterprise-access", None),
        ("agy_gcp_location", "global", None),
        ("agy_model", "", None),
        ("agy_effort", "", None),
    ]


def test_old_page_custom_endpoint_needs_url(page):
    plugin, _ = page
    plugin._endpoint_combo.setCurrentIndex(plugin._endpoint_combo.findData("custom"))
    plugin._custom_url_edit.setText("")
    assert plugin.validate() == ["Custom URL is required when using custom endpoint"]

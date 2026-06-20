from __future__ import annotations

import pytest

from lightfall.ui.preferences import claude_settings as cs


class _FakePrefs:
    def __init__(self, data: dict):
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture
def fake_prefs(monkeypatch):
    store = _FakePrefs({})
    monkeypatch.setattr(cs.PreferencesManager, "get_instance",
                        classmethod(lambda cls: store))
    return store


def test_resolve_model_alias_maps_presets():
    assert cs.resolve_model_alias("claude-opus") == "opus"
    assert cs.resolve_model_alias("claude-sonnet") == "sonnet"
    assert cs.resolve_model_alias("claude-haiku") == "haiku"


def test_resolve_model_alias_passthrough_and_default():
    assert cs.resolve_model_alias("") == ""
    assert cs.resolve_model_alias("claude-opus-4-8") == "claude-opus-4-8"


def test_effort_default_empty(fake_prefs):
    assert cs.ClaudeSettingsProvider.get_effort() == ""


def test_effort_roundtrip(fake_prefs):
    fake_prefs.set("claude_effort", "xhigh")
    assert cs.ClaudeSettingsProvider.get_effort() == "xhigh"


def test_auto_restore_and_last_session(fake_prefs):
    assert cs.ClaudeSettingsProvider.get_auto_restore() is False
    cs.ClaudeSettingsProvider.set_last_session_id("sess-9")
    assert cs.ClaudeSettingsProvider.get_last_session_id() == "sess-9"

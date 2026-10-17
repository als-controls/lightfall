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


def test_get_model_default_forces_no_model(fake_prefs):
    # Default MUST be "" so lightfall passes NO --model and the configured
    # backend/CLI uses its own working default. Forcing a hardcoded Anthropic
    # model string breaks non-Anthropic backends (Azure/cborg) and the CLI
    # rejects half-form ids like "claude-haiku-4-5" (model_not_found).
    assert cs.ClaudeSettingsProvider.get_model() == ""
    # "" resolves to "" -> the agent omits the model option entirely.
    assert cs.resolve_model_alias(cs.ClaudeSettingsProvider.get_model()) == ""


def test_get_model_explicit_choice_roundtrips(fake_prefs):
    fake_prefs.set("claude_model", "claude-sonnet")
    assert cs.ClaudeSettingsProvider.get_model() == "claude-sonnet"
    assert cs.resolve_model_alias(cs.ClaudeSettingsProvider.get_model()) == "sonnet"


def test_last_session_key_honours_explicit_kind(fake_prefs):
    # Live pref says agy, but the caller pins the build-time kind.
    fake_prefs.set("agent_backend", "agy")
    cs.ClaudeSettingsProvider.set_last_session_id("c-1", kind="claude-code")
    assert fake_prefs.get("claude_last_session_id") == "c-1"
    assert fake_prefs.get("agy_last_session_id") is None
    assert cs.ClaudeSettingsProvider.get_last_session_id(kind="claude-code") == "c-1"
    # kind=None keeps following the live pref.
    assert cs.ClaudeSettingsProvider.get_last_session_id() == ""


def test_agent_stores_session_under_build_time_kind(fake_prefs):
    from lightfall.claude.agent import QtClaudeAgent

    agent = QtClaudeAgent.__new__(QtClaudeAgent)  # skip heavy __init__
    agent._agy = False  # built as a Claude agent
    agent._spec = None
    agent._worker = None
    fake_prefs.set("agent_backend", "agy")  # user switched backend mid-turn
    agent._store_session_id("claude-sess")
    assert fake_prefs.get("claude_last_session_id") == "claude-sess"
    assert fake_prefs.get("agy_last_session_id") is None

    agent._agy = True
    fake_prefs.set("agent_backend", "claude-code")
    agent._store_session_id("agy-sess")
    assert fake_prefs.get("agy_last_session_id") == "agy-sess"
    assert fake_prefs.get("claude_last_session_id") == "claude-sess"

"""Tests for AgentRegistry."""
from __future__ import annotations

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.ui.panels.claude.agent_registry import AgentRegistry


class _Pure_Prompt_Agent(AgentPlugin):
    @property
    def name(self): return "alpha"
    @property
    def description(self): return "alpha plugin"
    @property
    def priority(self): return 10
    def get_system_prompt(self): return "alpha prompt"


class _Pure_Tools_Agent(AgentPlugin):
    @property
    def name(self): return "beta"
    @property
    def description(self): return "beta plugin"
    @property
    def category(self): return "devices"
    @property
    def priority(self): return 50
    def create_tools(self): return [object()]


class _Disabled_By_Default_Agent(AgentPlugin):
    @property
    def name(self): return "gamma"
    @property
    def description(self): return "gamma plugin"
    @property
    def enabled_by_default(self): return False


@pytest.fixture(autouse=True)
def reset_registry():
    AgentRegistry.reset_instance()
    yield
    AgentRegistry.reset_instance()


def _stub_prefs(monkeypatch, *, disabled=None, forced=None):
    """Stub the registry's pref reads so tests don't touch QSettings."""
    def fake(self, key):
        from lightfall.ui.panels.claude.agent_registry import (
            DISABLED_PLUGINS_PREF, FORCED_ENABLED_PLUGINS_PREF,
        )
        if key == DISABLED_PLUGINS_PREF:
            return disabled
        if key == FORCED_ENABLED_PLUGINS_PREF:
            return forced
        return None
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._read_list_pref",
        fake,
    )
    # Skip migration: tests assert on the new prefs directly.
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._migrate_legacy_pref_if_needed",
        lambda self: None,
    )


def test_register_adds_plugin():
    reg = AgentRegistry.get_instance()
    a = _Pure_Prompt_Agent()
    reg.register(a)
    assert reg.get_plugins() == [a]


def test_duplicate_name_replaces():
    reg = AgentRegistry.get_instance()
    a1 = _Pure_Prompt_Agent()
    a2 = _Pure_Prompt_Agent()
    reg.register(a1)
    reg.register(a2)  # same name "alpha"
    plugins = reg.get_plugins()
    assert len(plugins) == 1
    assert plugins[0] is a2


def test_unregister_removes():
    reg = AgentRegistry.get_instance()
    a = _Pure_Prompt_Agent()
    reg.register(a)
    assert reg.unregister("alpha") is True
    assert reg.get_plugins() == []


def test_unregister_unknown_returns_false():
    reg = AgentRegistry.get_instance()
    assert reg.unregister("never_registered") is False


def test_enabled_plugins_no_overrides_uses_defaults(monkeypatch):
    """With no overrides set, enabled = those with enabled_by_default=True."""
    _stub_prefs(monkeypatch)
    reg = AgentRegistry.get_instance()
    a, b, g = _Pure_Prompt_Agent(), _Pure_Tools_Agent(), _Disabled_By_Default_Agent()
    reg.register(a); reg.register(b); reg.register(g)
    names = {p.name for p in reg.enabled_plugins()}
    assert names == {"alpha", "beta"}


def test_disabled_pref_excludes_default_enabled(monkeypatch):
    _stub_prefs(monkeypatch, disabled=["alpha"])
    reg = AgentRegistry.get_instance()
    a, b, g = _Pure_Prompt_Agent(), _Pure_Tools_Agent(), _Disabled_By_Default_Agent()
    reg.register(a); reg.register(b); reg.register(g)
    names = {p.name for p in reg.enabled_plugins()}
    assert names == {"beta"}


def test_forced_enabled_pref_includes_default_disabled(monkeypatch):
    _stub_prefs(monkeypatch, forced=["gamma"])
    reg = AgentRegistry.get_instance()
    a, b, g = _Pure_Prompt_Agent(), _Pure_Tools_Agent(), _Disabled_By_Default_Agent()
    reg.register(a); reg.register(b); reg.register(g)
    names = {p.name for p in reg.enabled_plugins()}
    assert names == {"alpha", "beta", "gamma"}


def test_disabled_overrides_forced_enabled(monkeypatch):
    """A name in both lists is disabled — opt-out is the safer policy."""
    _stub_prefs(monkeypatch, disabled=["gamma"], forced=["gamma"])
    reg = AgentRegistry.get_instance()
    g = _Disabled_By_Default_Agent()
    reg.register(g)
    assert reg.enabled_plugins() == []


def test_newly_registered_plugin_is_enabled_by_default(monkeypatch):
    """Regression: a plugin registered after the user saved settings should
    still be enabled, not silently excluded."""
    _stub_prefs(monkeypatch, disabled=["alpha"], forced=[])
    reg = AgentRegistry.get_instance()
    reg.register(_Pure_Prompt_Agent())  # disabled
    reg.register(_Pure_Tools_Agent())   # newly added — should appear
    names = {p.name for p in reg.enabled_plugins()}
    assert names == {"beta"}


def test_enabled_plugins_sorted_by_priority(monkeypatch):
    _stub_prefs(monkeypatch)
    reg = AgentRegistry.get_instance()
    a, b = _Pure_Prompt_Agent(), _Pure_Tools_Agent()
    reg.register(b); reg.register(a)  # registered out of order
    names = [p.name for p in reg.enabled_plugins()]
    assert names == ["alpha", "beta"]  # alpha has priority 10, beta has 50


def test_introspection_data():
    reg = AgentRegistry.get_instance()
    reg.register(_Pure_Prompt_Agent())
    data = reg.get_introspection_data()
    assert data["plugin_count"] == 1
    assert len(data["plugins"]) == 1
    assert data["plugins"][0]["name"] == "alpha"

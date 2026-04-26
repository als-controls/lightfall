"""Tests for AgentRegistry."""
from __future__ import annotations

from typing import Any

import pytest

from lucid.plugins.agent_plugin import AgentPlugin
from lucid.ui.panels.claude.agent_registry import AgentRegistry


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


def test_enabled_plugins_no_pref_uses_defaults(monkeypatch):
    """When enabled_tool_plugins pref is None, enabled = those with enabled_by_default=True."""
    monkeypatch.setattr(
        "lucid.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: None,
    )
    reg = AgentRegistry.get_instance()
    a, b, g = _Pure_Prompt_Agent(), _Pure_Tools_Agent(), _Disabled_By_Default_Agent()
    reg.register(a); reg.register(b); reg.register(g)
    enabled = reg.enabled_plugins()
    names = {p.name for p in enabled}
    assert names == {"alpha", "beta"}
    assert "gamma" not in names


def test_enabled_plugins_respects_pref(monkeypatch):
    monkeypatch.setattr(
        "lucid.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: ["beta", "gamma"],
    )
    reg = AgentRegistry.get_instance()
    a, b, g = _Pure_Prompt_Agent(), _Pure_Tools_Agent(), _Disabled_By_Default_Agent()
    reg.register(a); reg.register(b); reg.register(g)
    enabled = reg.enabled_plugins()
    names = {p.name for p in enabled}
    assert names == {"beta", "gamma"}


def test_enabled_plugins_sorted_by_priority(monkeypatch):
    monkeypatch.setattr(
        "lucid.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: ["alpha", "beta"],
    )
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

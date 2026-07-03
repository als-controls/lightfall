"""Tests for AgentPlugin base class."""
from __future__ import annotations

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin


class _StubAgent(AgentPlugin):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def description(self) -> str:
        return "Stub agent for tests"


def test_default_get_system_prompt_returns_empty():
    plugin = _StubAgent()
    assert plugin.get_system_prompt() == ""


def test_default_create_tools_returns_empty_list():
    plugin = _StubAgent()
    assert plugin.create_tools() == []


def test_default_get_references_dir_returns_none():
    plugin = _StubAgent()
    assert plugin.get_references_dir() is None


def test_default_display_name_titlecases_name():
    plugin = _StubAgent()
    assert plugin.display_name == "Stub"


def test_default_category_is_general():
    plugin = _StubAgent()
    assert plugin.category == "general"


def test_default_enabled_by_default_is_true():
    plugin = _StubAgent()
    assert plugin.enabled_by_default is True


def test_default_priority_is_100():
    plugin = _StubAgent()
    assert plugin.priority == 100


def test_type_name_is_agent():
    assert AgentPlugin.type_name == "agent"


def test_is_singleton():
    assert AgentPlugin.is_singleton is True


def test_name_is_abstract():
    """Cannot instantiate without overriding name + description."""
    class Incomplete(AgentPlugin):
        pass

    with pytest.raises(TypeError):
        Incomplete()


def test_default_create_external_servers_returns_empty():
    assert _StubAgent().create_external_servers() == {}


def test_introspection_includes_has_external_servers():
    data = _StubAgent().get_introspection_data()
    assert data["has_external_servers"] is False


def test_introspection_does_not_call_create_external_servers():
    calls = []

    class _Exploding(AgentPlugin):
        @property
        def name(self): return "boom"
        @property
        def description(self): return "raises if specs are built"
        def create_external_servers(self):
            calls.append(1)
            raise AssertionError("create_external_servers must not run during introspection")
        def has_external_servers(self): return True

    data = _Exploding().get_introspection_data()
    assert data["has_external_servers"] is True
    assert calls == []

"""Lightweight test that QtClaudeAgent constructs ClaudeAgentOptions correctly.

We don't actually connect to the SDK — we patch ClaudeSDKClient and inspect
the options dict that QtClaudeAgent built.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lucid.plugins.agent_plugin import AgentPlugin
from lucid.ui.panels.claude.agent_registry import AgentRegistry


class _PromptAgent(AgentPlugin):
    @property
    def name(self): return "prompt_agent"
    @property
    def description(self): return "prompt agent for tests"
    def get_system_prompt(self): return "## Prompt body"


class _ToolAgent(AgentPlugin):
    @property
    def name(self): return "tool_agent"
    @property
    def description(self): return "tool agent for tests"
    def create_tools(self):
        from claude_agent_sdk import tool

        @tool(name="my_tool", description="x", input_schema={"type": "object", "properties": {}})
        async def t(args): return {"content": [{"type": "text", "text": "ok"}]}
        return [t]


@pytest.fixture(autouse=True)
def reset_registry():
    AgentRegistry.reset_instance()
    yield
    AgentRegistry.reset_instance()


@pytest.fixture
def mock_sdk(monkeypatch):
    """Replace ClaudeSDKClient with a MagicMock so __init__ doesn't connect."""
    monkeypatch.setattr("lucid.claude.agent.ClaudeSDKClient", MagicMock())


def test_qtclaudeagent_uses_per_plugin_servers_and_plugins_param(mock_sdk, qtbot, monkeypatch):
    AgentRegistry.get_instance().register(_PromptAgent())
    AgentRegistry.get_instance().register(_ToolAgent())
    monkeypatch.setattr(
        "lucid.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: ["prompt_agent", "tool_agent"],
    )

    from PySide6.QtWidgets import QWidget

    from lucid.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)

    options = agent.options
    # qt server is always present
    assert "qt" in options.mcp_servers
    # tool_agent gets its own server (per-plugin split)
    assert "tool_agent" in options.mcp_servers
    # prompt_agent has no tools so no server
    assert "prompt_agent" not in options.mcp_servers
    # No "additional" mega-bag anymore
    assert "additional" not in options.mcp_servers
    # plugins= is set with the synthesized session plugin dir
    assert isinstance(options.plugins, list)
    assert len(options.plugins) == 1
    plugin_path = options.plugins[0]["path"]
    assert (Path(plugin_path) / "skills" / "prompt_agent" / "SKILL.md").exists()
    # No skill content baked into system_prompt
    assert "## Prompt body" not in options.system_prompt
    # allowed_tools includes per-plugin namespace
    assert any(t.startswith("mcp__tool_agent__") for t in options.allowed_tools)

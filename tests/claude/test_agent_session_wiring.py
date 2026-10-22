"""Lightweight test that QtClaudeAgent constructs ClaudeAgentOptions correctly.

We don't actually connect to the SDK — we patch ClaudeSDKClient and inspect
the options dict that QtClaudeAgent built.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lightfall.plugins.tool_plugin import ToolPlugin
from lightfall.ui.panels.claude.tool_registry import ToolRegistry


class _PromptAgent(ToolPlugin):
    # Named after a real shipped skill (src/lightfall/skills/builtin/scan_planning/)
    # so materialize_skills() has something to resolve for it.
    @property
    def name(self): return "scan_planning"
    @property
    def description(self): return "prompt agent for tests"


class _ToolAgent(ToolPlugin):
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
    ToolRegistry.reset_instance()
    yield
    ToolRegistry.reset_instance()


@pytest.fixture
def mock_sdk(monkeypatch):
    """Replace ClaudeSDKClient with a MagicMock so __init__ doesn't connect."""
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())


def test_qtclaudeagent_uses_per_plugin_servers_and_plugins_param(mock_sdk, qtbot, monkeypatch):
    ToolRegistry.get_instance().register(_PromptAgent())
    ToolRegistry.get_instance().register(_ToolAgent())
    # Both agents default to enabled_by_default=True; stub pref reads to no overrides.
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._read_list_pref",
        lambda self, key: None,
    )
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._migrate_legacy_pref_if_needed",
        lambda self: None,
    )

    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)

    options = agent.options
    # qt server is always present
    assert "qt" in options.mcp_servers
    # bus server + tools are always present, on every session
    assert "bus" in options.mcp_servers
    assert "mcp__bus__send_message" in options.allowed_tools
    assert "mcp__bus__list_agents" in options.allowed_tools
    # skills server + tool are always present, on every session
    assert "skills" in options.mcp_servers
    assert "mcp__skills__draft_skill" in options.allowed_tools
    for name in ("list_skills", "get_skill", "get_skill_reference"):
        assert f"mcp__skills__{name}" in options.allowed_tools
    # tool_agent gets its own server (per-plugin split)
    assert "tool_agent" in options.mcp_servers
    # scan_planning has no tools so no server
    assert "scan_planning" not in options.mcp_servers
    # No "additional" mega-bag anymore
    assert "additional" not in options.mcp_servers
    # plugins= is set with the synthesized session plugin dir
    assert isinstance(options.plugins, list)
    assert len(options.plugins) == 1
    plugin_path = options.plugins[0]["path"]
    # scan_planning's shipped skill is materialized by name, file-based now
    assert (Path(plugin_path) / "skills" / "scan_planning" / "SKILL.md").exists()
    # No skill content baked into system_prompt
    assert "## Scan Planning Expertise" not in options.system_prompt
    # allowed_tools includes per-plugin namespace
    assert any(t.startswith("mcp__tool_agent__") for t in options.allowed_tools)
    # Must not inherit the developer's user-scope Claude settings (global
    # CLAUDE.md, personal plugins/hooks/skills) into an embedded session.
    assert options.setting_sources == []


def test_always_allowed_skill_is_materialized_into_new_session(mock_sdk, qtbot, monkeypatch,
                                                                tmp_path):
    """A skill the user chose "always allow" for this agent is copied into the
    agent's next session, without being in any skills list."""
    from lightfall.agents import skill_approvals, skills_store

    extra = tmp_path / "beamline"
    (extra / "approved-one").mkdir(parents=True)
    (extra / "approved-one" / "SKILL.md").write_text(
        "---\nname: approved-one\ndescription: d\n---\nbody", encoding="utf-8"
    )
    real_catalog = skills_store.skill_catalog
    monkeypatch.setattr(skills_store, "skill_catalog",
                        lambda extra_dirs=None: real_catalog([extra]))
    monkeypatch.setattr(skill_approvals, "approvals_path", lambda: tmp_path / "approvals.json")
    skill_approvals.approve_skill("lightfall", "approved-one")
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._read_list_pref",
        lambda self, key: None,
    )
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.tool_registry.ToolRegistry._migrate_legacy_pref_if_needed",
        lambda self: None,
    )

    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)

    plugin_path = Path(agent.options.plugins[0]["path"])
    assert (plugin_path / "skills" / "approved-one" / "SKILL.md").is_file()

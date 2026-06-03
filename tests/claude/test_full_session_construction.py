"""End-to-end: load builtin manifest → construct QtClaudeAgent → verify options."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def loaded_builtins(monkeypatch):
    """Load the real builtin manifest into AgentRegistry, with SDK mocked."""
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())
    from lightfall.plugins.agent_plugin import AgentPlugin
    from lightfall.plugins.builtin_manifest import builtin_manifest
    from lightfall.plugins.loader import PluginLoader
    from lightfall.ui.panels.claude.agent_registry import AgentRegistry

    AgentRegistry.reset_instance()

    loader = PluginLoader()
    loader.register_plugin_type("agent", AgentPlugin)
    loader.load_manifest(builtin_manifest)
    loader.load_all_sync()
    yield
    AgentRegistry.reset_instance()


def test_construct_agent_with_all_builtins_enabled(loaded_builtins, qtbot, monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._read_list_pref",
        lambda self, key: None,  # no overrides — falls through to enabled_by_default
    )
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._migrate_legacy_pref_if_needed",
        lambda self: None,
    )
    from PySide6.QtWidgets import QWidget
    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)

    options = agent.options
    # qt server always present
    assert "qt" in options.mcp_servers
    # 6 tool-bearing plugins each get their own server
    expected_tool_servers = {
        "device_tools", "plan_tools", "engine_tools", "ipython_tools",
        "panel_builder", "ncs_core_tools",
    }
    assert expected_tool_servers.issubset(options.mcp_servers.keys())
    # No bundled "additional" server (skills + tools split into per-plugin servers).
    assert "additional" not in options.mcp_servers
    # plugins= present, points at a real on-disk dir with skills
    plugin_dir = Path(options.plugins[0]["path"])
    assert plugin_dir.exists()
    assert (plugin_dir / ".claude-plugin" / "plugin.json").exists()
    # Each prompt-bearing plugin has a SKILL.md
    expected_skills = {"alignment", "plan_design", "scan_planning", "panel_design", "panel_builder"}
    for skill_name in expected_skills:
        assert (plugin_dir / "skills" / skill_name / "SKILL.md").exists(), f"missing {skill_name}"
    # No skill content baked into the system prompt
    assert "## Beamline Alignment Expertise" not in options.system_prompt
    # Cleanup the temp dir
    agent.stop()

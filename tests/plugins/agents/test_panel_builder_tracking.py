"""Verify panel_builder tools commit with agent-supplied descriptions."""
from __future__ import annotations

import asyncio
import json as json_mod
import subprocess
from pathlib import Path

import pytest

from lucid.plugins.agents.panel_builder import PanelBuilderAgent
from lucid.plugins.user_plugins import UserPluginService
from lucid.ui.panels.claude.agent_registry import AgentRegistry
from lucid.ui.panels.registry import PanelRegistry
from lucid.utils.git_tracker import GitTracker


def _unwrap(result):
    """Tools may return raw dicts or mcp_result-wrapped envelopes."""
    if isinstance(result, dict) and "content" in result:
        return json_mod.loads(result["content"][0]["text"])
    return result


@pytest.fixture(autouse=True)
def reset_singletons():
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()
    PanelRegistry.reset()
    GitTracker.reset_instance()
    yield
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()
    PanelRegistry.reset()
    GitTracker.reset_instance()


@pytest.fixture
def tracked_dirs(tmp_path, monkeypatch):
    GitTracker.reset_instance()
    repo_root = tmp_path / "lucid"
    repo_root.mkdir()
    plugins_dir = repo_root / "plugins"
    plugins_dir.mkdir()

    tracker = GitTracker(repo_root=repo_root)
    monkeypatch.setattr(GitTracker, "_instance", tracker)

    # Make UserPluginService use this dir AND make the user-plugin-roots
    # detector recognize it (so __init_subclass__ auto-enqueues).
    monkeypatch.setattr(
        "lucid.plugins.types._user_plugin_roots",
        lambda: [plugins_dir.resolve()],
    )
    service = UserPluginService.get_instance()
    monkeypatch.setattr(service, "_plugins_dir", plugins_dir)
    yield plugins_dir


def _git_log_subjects(repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def test_create_user_plugin_commits_with_description(tracked_dirs):
    """The agent's `description` becomes the commit subject."""
    agent = PanelBuilderAgent()
    tools = agent.create_tools()
    create_tool = next(t for t in tools if getattr(t, "name", None) == "ncs_create_user_plugin")

    code = '''"""thermometer."""
from lucid.plugins.agent_plugin import AgentPlugin

class ThermAgent(AgentPlugin):
    @property
    def name(self): return "thermometer"
    @property
    def description(self): return "thermometer"
    def get_system_prompt(self): return "## therm"
'''
    result = asyncio.run(create_tool.handler({
        "name": "thermometer",
        "code": code,
        "description": "create thermometer panel for sample env",
    }))
    body = _unwrap(result)
    assert body["success"], body

    subjects = _git_log_subjects(tracked_dirs.parent)
    assert subjects == ["agent: create thermometer panel for sample env"]

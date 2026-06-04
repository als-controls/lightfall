"""Tests for session-time SDK plugin-dir + MCP server assembly."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin


class _PromptOnly(AgentPlugin):
    @property
    def name(self): return "prompt_only"
    @property
    def description(self): return "prompt only plugin"
    def get_system_prompt(self): return "## Prompt body\n\nText here."


class _ToolsOnly(AgentPlugin):
    @property
    def name(self): return "tools_only"
    @property
    def description(self): return "tools only plugin"
    def create_tools(self): return [object()]


class _Both(AgentPlugin):
    @property
    def name(self): return "both"
    @property
    def description(self): return "x" * 1500
    def get_system_prompt(self): return "Both prompt"
    def create_tools(self): return [object()]


class _WithRefs(AgentPlugin):
    def __init__(self, refs_dir):
        self._refs = refs_dir
    @property
    def name(self): return "with_refs"
    @property
    def description(self): return "with refs"
    def get_system_prompt(self): return "with refs body"
    def get_references_dir(self): return self._refs


def test_prompt_only_plugin_writes_skill_md(tmp_path):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_PromptOnly(), plugin_dir)

    skill_md = plugin_dir / "skills" / "prompt_only" / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "name: prompt-only" in content  # underscores → hyphens at materialization
    assert "description: prompt only plugin" in content
    assert "## Prompt body" in content


def test_tools_only_plugin_does_not_write_skill_md(tmp_path):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_ToolsOnly(), plugin_dir)

    assert not (plugin_dir / "skills" / "tools_only").exists()


def test_long_description_truncates_with_warning(tmp_path, caplog):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_Both(), plugin_dir)

    skill_md = (plugin_dir / "skills" / "both" / "SKILL.md").read_text()
    desc_line = next(line for line in skill_md.splitlines() if line.startswith("description:"))
    # 1024-char limit; "description: " prefix = 13 chars; total = 13 + 1024 = 1037
    assert len(desc_line) <= 13 + 1024


def test_references_dir_copied(tmp_path):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    src_refs = tmp_path / "src_refs"
    src_refs.mkdir()
    (src_refs / "guide.md").write_text("# Guide", encoding="utf-8")

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_WithRefs(src_refs), plugin_dir)

    copied = plugin_dir / "skills" / "with_refs" / "references" / "guide.md"
    assert copied.exists()
    assert copied.read_text() == "# Guide"


def test_init_session_plugin_dir_writes_plugin_json(tmp_path):
    from lightfall.claude._session_assembly import init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
    assert plugin_json.exists()
    import json
    data = json.loads(plugin_json.read_text())
    assert data["name"] == "lightfall-session"


def test_assemble_mcp_servers_skips_tool_less_plugins(tmp_path, monkeypatch):
    """Plugin without tools doesn't get its own server entry."""
    from lightfall.claude._session_assembly import assemble_mcp_servers
    from claude_agent_sdk import tool

    # Stub create_sdk_mcp_server to avoid pulling in real SDK plumbing
    captured = {}
    def _stub(name, version, tools):
        captured[name] = ("stub", tools)
        return f"stub-{name}"
    monkeypatch.setattr("lightfall.claude._session_assembly.create_sdk_mcp_server", _stub)

    @tool(name="real_tool", description="x", input_schema={"type": "object", "properties": {}})
    async def real_tool(args): return {"content": [{"type": "text", "text": "ok"}]}

    class _ToolBearing(AgentPlugin):
        @property
        def name(self): return "tb"
        @property
        def description(self): return "tb"
        def create_tools(self): return [real_tool]

    servers, allowed = assemble_mcp_servers([_PromptOnly(), _ToolBearing()])
    assert "prompt_only" not in servers
    assert servers["tb"] == "stub-tb"
    assert any(t.startswith("mcp__tb__") for t in allowed)

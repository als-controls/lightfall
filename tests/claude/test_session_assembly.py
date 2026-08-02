"""Tests for session-time SDK plugin-dir + MCP server assembly."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightfall.plugins.tool_plugin import ToolPlugin


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

    class _NoTools(ToolPlugin):
        @property
        def name(self): return "no_tools"
        @property
        def description(self): return "no tools plugin"

    class _ToolBearing(ToolPlugin):
        @property
        def name(self): return "tb"
        @property
        def description(self): return "tb"
        def create_tools(self): return [real_tool]

    servers, allowed = assemble_mcp_servers([_NoTools(), _ToolBearing()])
    assert "no_tools" not in servers
    assert servers["tb"] == "stub-tb"
    assert any(t.startswith("mcp__tb__") for t in allowed)


def test_external_servers_merged_with_wildcard_allowed_tools():
    from lightfall.claude._session_assembly import assemble_mcp_servers

    class _External(ToolPlugin):
        @property
        def name(self): return "osprey"
        @property
        def description(self): return "ext"
        def create_external_servers(self):
            return {"controls": {"type": "stdio", "command": "python", "args": ["-m", "x"]}}

    servers, allowed = assemble_mcp_servers([_External()])
    assert servers["controls"] == {"type": "stdio", "command": "python", "args": ["-m", "x"]}
    assert "mcp__controls__*" in allowed


def test_external_server_name_collision_is_skipped(monkeypatch):
    from lightfall.claude._session_assembly import assemble_mcp_servers
    from claude_agent_sdk import tool

    monkeypatch.setattr(
        "lightfall.claude._session_assembly.create_sdk_mcp_server",
        lambda name, version, tools: f"stub-{name}",
    )

    @tool(name="t", description="x", input_schema={"type": "object", "properties": {}})
    async def t(args): return {"content": [{"type": "text", "text": "ok"}]}

    class _InProc(ToolPlugin):
        @property
        def name(self): return "controls"
        @property
        def description(self): return "in-process controls"
        def create_tools(self): return [t]

    class _Ext(ToolPlugin):
        @property
        def name(self): return "ext"
        @property
        def description(self): return "ext"
        def create_external_servers(self):
            return {"controls": {"type": "stdio", "command": "python"}}

    servers, allowed = assemble_mcp_servers([_InProc(), _Ext()])
    # In-process server registered first wins; external one is skipped.
    assert servers["controls"] == "stub-controls"
    assert "mcp__controls__*" not in allowed

"""build_claude_options reproduces QtClaudeAgent's historical options."""
from __future__ import annotations

from pathlib import Path

from lightfall.agents.backend import SessionConfig, SubagentSpec
from lightfall.agents.tooling import ToolServer, tool


@tool("ping", "Ping", {})
async def _ping(args):
    return {"content": []}


def _cfg(tmp_path: Path, **kw) -> SessionConfig:
    base = {
        "cwd": str(tmp_path), "system_prompt": "SYS",
        "tool_servers": [("qt", ToolServer("qt-tools", "0.1.0", (_ping,)))],
        "external_servers": {"ext": {"type": "stdio", "command": "python", "args": []}},
        "allowed_tools": ["mcp__qt__ping"], "subagents": [], "skills_dir": tmp_path,
        "permission_mode": "default", "max_turns": 20, "model": None, "effort": None,
        "resume": None, "cli_path": None,
    }
    base.update(kw)
    return SessionConfig(**base)


def test_options_match_historical_shape(tmp_path, qtbot):
    from lightfall.agents.backends.claude_code.options import (
        SDK_MAX_BUFFER_SIZE,
        build_claude_options,
    )
    from lightfall.claude.permission_manager import PermissionManager

    opts = build_claude_options(_cfg(tmp_path), PermissionManager(), require_approval=True)
    assert opts.cwd == str(tmp_path)
    assert opts.system_prompt == "SYS"
    assert set(opts.mcp_servers) == {"qt", "ext"}
    assert opts.mcp_servers["qt"]["type"] == "sdk"
    assert opts.allowed_tools == ["mcp__qt__ping"]
    assert opts.plugins == [{"type": "local", "path": str(tmp_path)}]
    assert opts.setting_sources == []
    assert opts.include_partial_messages is True
    assert opts.max_buffer_size == SDK_MAX_BUFFER_SIZE
    assert opts.thinking == {"type": "adaptive", "display": "summarized"}
    assert opts.can_use_tool is not None
    assert "PreToolUse" in opts.hooks
    assert opts.model is None and opts.resume is None


def test_subagents_become_agent_definitions(tmp_path, qtbot):
    from lightfall.agents.backends.claude_code.options import build_claude_options
    from lightfall.claude.permission_manager import PermissionManager

    sub = SubagentSpec("helper", "Helps", "PROMPT", model="haiku", effort=None, memory=True)
    opts = build_claude_options(_cfg(tmp_path, subagents=[sub]), PermissionManager(), True)
    d = opts.agents["helper"]
    assert (d.description, d.prompt, d.model, d.memory) == ("Helps", "PROMPT", "haiku", "project")


def test_model_effort_resume_cli_path_flow_through(tmp_path, qtbot):
    from lightfall.agents.backends.claude_code.options import build_claude_options
    from lightfall.claude.permission_manager import PermissionManager

    cfg = _cfg(tmp_path, model="opus", effort="high", resume="s1", cli_path="C:/x/claude")
    opts = build_claude_options(cfg, PermissionManager(), True)
    assert (opts.model, opts.effort, opts.resume, str(opts.cli_path)) == (
        "opus", "high", "s1", "C:/x/claude",
    )


def test_permission_factories_still_importable_from_old_path():
    from lightfall.claude.permission_manager import (  # noqa: F401
        create_can_use_tool_callback,
        create_pre_tool_use_hook,
    )

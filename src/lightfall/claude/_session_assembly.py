"""Session-time assembly of the SDK plugin directory + per-plugin MCP servers.

Called by lightfall.claude.agent (QtClaudeAgent.__init__) at agent-construction
time. Translates ToolRegistry's enabled plugins into the inputs needed for
ClaudeAgentOptions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import create_sdk_mcp_server

from lightfall.agents.backends.claude_code.tools import to_sdk_tool
from lightfall.agents.tooling import ToolServer, ToolSpec, as_tool_spec, tool_id
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.tool_plugin import ToolPlugin


def init_session_plugin_dir(path: Path) -> Path:
    """Create a fresh SDK plugin dir at `path` with a minimal plugin.json.

    Returns `path` for convenience.
    """
    path.mkdir(parents=True, exist_ok=True)
    claude_plugin = path / ".claude-plugin"
    claude_plugin.mkdir(exist_ok=True)
    (claude_plugin / "plugin.json").write_text(
        json.dumps({"name": "lightfall-session", "version": "0.0.0"}),
        encoding="utf-8",
    )
    (path / "skills").mkdir(exist_ok=True)
    return path


def collect_tool_servers(
    enabled_plugins: list[ToolPlugin],
) -> tuple[list[ToolServer], dict[str, Any], list[str]]:
    """Backend-neutral: (tool servers, external server specs, allowed tool ids).

    One ToolServer per plugin that has tools (named ``plugin.name``); external
    stdio/http specs from ``create_external_servers()`` keyed by server name,
    allowed as the ``tool_id(name, "*")`` namespace. Name collisions with an
    in-process server are logged and skipped.
    """
    servers: list[ToolServer] = []
    external: dict[str, Any] = {}
    allowed: list[str] = []

    for plugin in enabled_plugins:
        tools = plugin.create_tools()
        if not tools:
            continue
        specs: list[ToolSpec] = []
        for obj in tools:
            spec = as_tool_spec(obj)
            if spec is None:
                logger.warning(
                    "agent '{}': tool object {!r} has no name; skipping allowed_tools entry",
                    plugin.name, obj,
                )
                continue
            specs.append(spec)
            allowed.append(tool_id(plugin.name, spec.name))
        servers.append(ToolServer(name=plugin.name, version="1.0.0", tools=tuple(specs)))

    taken = {s.name for s in servers}
    for plugin in enabled_plugins:
        for server_name, spec in plugin.create_external_servers().items():
            if server_name in taken or server_name in external:
                logger.warning(
                    "agent '{}': external MCP server name '{}' collides with an "
                    "existing server; skipping",
                    plugin.name, server_name,
                )
                continue
            external[server_name] = spec
            allowed.append(tool_id(server_name, "*"))

    return servers, external, allowed


def assemble_mcp_servers(
    enabled_plugins: list[ToolPlugin],
) -> tuple[dict[str, Any], list[str]]:
    """Claude-format (mcp_servers, allowed_tools) -- contract unchanged.

    In-process servers go through ``create_sdk_mcp_server`` (kept as a
    module-level name because tests monkeypatch it).
    """
    servers, external, allowed = collect_tool_servers(enabled_plugins)
    mcp_servers: dict[str, Any] = {}
    for server in servers:
        mcp_servers[server.name] = create_sdk_mcp_server(
            name=server.name,
            version=server.version,
            tools=[to_sdk_tool(t) for t in server.tools],
        )
    mcp_servers.update(external)
    return mcp_servers, allowed

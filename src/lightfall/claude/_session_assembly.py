"""Session-time assembly of the SDK plugin directory + per-plugin MCP servers.

Called by lightfall.claude.agent (QtClaudeAgent.__init__) at agent-construction
time. Translates AgentRegistry's enabled plugins into the inputs needed for
ClaudeAgentOptions.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import create_sdk_mcp_server

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.agent_plugin import AgentPlugin


# Per-SDK constraints
_SKILL_DESCRIPTION_LIMIT = 1024
_SKILL_NAME_LIMIT = 64


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


def materialize_skill(plugin: AgentPlugin, plugin_dir: Path) -> None:
    """Write `<plugin_dir>/skills/<name>/SKILL.md` for `plugin`, if it has a prompt.

    No-op for plugins where `get_system_prompt()` is empty/whitespace.
    Truncates description to the SDK's 1024-char limit; logs a warning if so.
    Converts `_` → `-` in the SKILL.md frontmatter `name` (per spec Open Q).
    Copies `get_references_dir()` (if set) to `references/` alongside.
    """
    body = plugin.get_system_prompt().strip()
    if not body:
        return

    skill_dir = plugin_dir / "skills" / plugin.name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_name = plugin.name.replace("_", "-")
    if len(skill_name) > _SKILL_NAME_LIMIT:
        logger.warning(
            "agent '{}': SKILL.md frontmatter name truncated from {} to {} chars",
            plugin.name, len(skill_name), _SKILL_NAME_LIMIT,
        )
        skill_name = skill_name[:_SKILL_NAME_LIMIT]

    description = plugin.description
    if len(description) > _SKILL_DESCRIPTION_LIMIT:
        logger.warning(
            "agent '{}': SKILL.md description truncated from {} to {} chars",
            plugin.name, len(description), _SKILL_DESCRIPTION_LIMIT,
        )
        description = description[:_SKILL_DESCRIPTION_LIMIT]

    frontmatter = (
        f"---\n"
        f"name: {skill_name}\n"
        f"description: {description}\n"
        f"---\n\n"
    )
    (skill_dir / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")

    refs = plugin.get_references_dir()
    if refs is not None:
        target = skill_dir / "references"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(refs, target)


def assemble_mcp_servers(
    enabled_plugins: list[AgentPlugin],
) -> tuple[dict[str, Any], list[str]]:
    """Build (mcp_servers, allowed_tools) from the enabled AgentPlugins.

    The returned dict has one server per plugin that has tools, keyed by
    plugin.name. Server names follow the SDK convention: tools become
    `mcp__<plugin.name>__<tool_name>` in `allowed_tools`. It also contains
    external stdio server entries contributed via `create_external_servers()`,
    keyed by their declared server name (namespace `mcp__<server_name>__*`).

    Caller is expected to merge this with the always-on `qt` server and
    its allowed_tools entries.
    """
    mcp_servers: dict[str, Any] = {}
    allowed_tools: list[str] = []

    for plugin in enabled_plugins:
        tools = plugin.create_tools()
        if not tools:
            continue
        server = create_sdk_mcp_server(
            name=plugin.name, version="1.0.0", tools=tools,
        )
        mcp_servers[plugin.name] = server
        for tool in tools:
            tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if tool_name is None:
                logger.warning(
                    "agent '{}': tool object {!r} has no name; skipping allowed_tools entry",
                    plugin.name, tool,
                )
                continue
            allowed_tools.append(f"mcp__{plugin.name}__{tool_name}")

    # External (stdio/http) MCP servers contributed by plugins. Unlike
    # in-process tools, external tool names aren't known until the server is
    # launched, so we allow the whole `mcp__<name>__*` namespace and let the
    # session's PreToolUse hook / PermissionManager gate per call.
    for plugin in enabled_plugins:
        for server_name, spec in plugin.create_external_servers().items():
            if server_name in mcp_servers:
                logger.warning(
                    "agent '{}': external MCP server name '{}' collides with an "
                    "existing server; skipping",
                    plugin.name, server_name,
                )
                continue
            mcp_servers[server_name] = spec
            allowed_tools.append(f"mcp__{server_name}__*")

    return mcp_servers, allowed_tools

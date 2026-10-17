"""Rules-file section telling the agy model which Lightfall MCP servers exist.

The operator agent sets ``excludeDefaultComponents: true`` (about 11k fewer
tokens per first turn), which also drops agy's own MCP server listing.  Without
this section the model has to guess server names and every ``call_mcp_tool``
is denied by the hook.  agy prefixes plugin servers with the plugin name, so
the wire name of server key ``bus`` is ``lightfall_bus``.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from lightfall.agents.backends.agy.home import PLUGIN_NAME
from lightfall.agents.tooling import ToolServer

_DESC_MAX = 120


def server_wire_name(key: str) -> str:
    """The name agy gives the plugin's MCP server ``key`` (``ServerName``)."""
    return f"{PLUGIN_NAME}_{key}"


def _summary(description: str) -> str:
    first = (description or "").strip().splitlines()[0].strip() if description.strip() else ""
    if len(first) > _DESC_MAX:
        first = first[: _DESC_MAX - 1].rstrip() + "…"
    return first


def render_tool_directory(
    servers: Iterable[tuple[str, ToolServer]],
    external_keys: Iterable[str],
    schema_root: Path,
) -> str:
    """Markdown listing every server and tool, or "" when there are none.

    ``schema_root`` is ``<session home>/.gemini/antigravity-cli/mcp``, where agy
    writes one ``<ServerName>/<ToolName>.json`` schema per tool.
    """
    servers = list(servers)
    external_keys = list(external_keys)
    if not servers and not external_keys:
        return ""
    lines = [
        "## Calling Lightfall tools",
        "",
        "Lightfall's tools are MCP tools. Call them with `call_mcp_tool`, passing",
        "`ServerName` (exactly as listed below), `ToolName`, and `Arguments` as a JSON",
        "object. Any other server name is refused.",
        "",
        "Each tool's full argument schema is at",
        f"`{schema_root}\\<ServerName>\\<ToolName>.json`; read it with `view_file` before",
        "calling a tool whose arguments you are unsure of.",
        "",
    ]
    for key, server in servers:
        lines.append(f"### `{server_wire_name(key)}`")
        for spec in server.tools:
            summary = _summary(spec.description)
            lines.append(f"- `{spec.name}` — {summary}" if summary else f"- `{spec.name}`")
        lines.append("")
    for key in external_keys:
        lines.append(f"### `{server_wire_name(key)}`")
        lines.append(f"- (external server; list its tools by reading `{schema_root / server_wire_name(key)}`)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

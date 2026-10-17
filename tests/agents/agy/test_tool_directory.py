"""The agy rules file must tell the model the real MCP server names.

``excludeDefaultComponents`` strips agy's own MCP listing, so Lightfall writes
one: ``call_mcp_tool`` usage, ``lightfall_<key>`` server names, tool names with
one-line descriptions, and where the full schemas live (readable with view_file).
"""
from __future__ import annotations

from pathlib import Path

from lightfall.agents.backends.agy.hook_policy import _server_key
from lightfall.agents.backends.agy.tool_directory import render_tool_directory
from lightfall.agents.tooling import ToolServer, ToolSpec


async def _noop(args):
    return {"content": []}


def _server(name, *tools):
    return ToolServer(name, "1.0.0", tuple(ToolSpec(t, d, {}, _noop) for t, d in tools))


SERVERS = [
    ("bus", _server("bus", ("list_agents", "List running agents.\nMore detail here."),
                    ("launch_agent", "Launch a peer agent."))),
    ("engine_tools", _server("engine_tools", ("lightfall_wait_for_idle", "Block until idle. " + "x" * 300))),
]


def test_names_servers_with_plugin_prefix_and_lists_tools(tmp_path):
    text = render_tool_directory(SERVERS, ["external_srv"], tmp_path / "mcp")
    assert "call_mcp_tool" in text
    assert "`lightfall_bus`" in text and "`lightfall_engine_tools`" in text
    assert "`lightfall_external_srv`" in text
    assert "`list_agents` — List running agents." in text
    assert "More detail here." not in text  # first line only
    assert "`launch_agent`" in text and "`lightfall_wait_for_idle`" in text
    # descriptions are capped so the directory stays small
    line = next(ln for ln in text.splitlines() if "lightfall_wait_for_idle" in ln)
    assert len(line) < 200


def test_points_at_schema_files_for_view_file(tmp_path):
    root = tmp_path / "mcp"
    text = render_tool_directory(SERVERS, [], root)
    assert "view_file" in text
    assert str(root / "lightfall_bus" / "list_agents.json") in text or str(root) in text


def test_every_listed_server_name_is_accepted_by_the_hook(tmp_path):
    text = render_tool_directory(SERVERS, ["external_srv"], tmp_path / "mcp")
    keys = {"bus", "engine_tools", "external_srv"}
    import re

    names = set(re.findall(r"`(lightfall_[A-Za-z0-9_.-]+)`", text))
    servers = {n for n in names if _server_key(n, keys) is not None}
    assert servers == {"lightfall_bus", "lightfall_engine_tools", "lightfall_external_srv"}


def test_no_servers_renders_nothing(tmp_path):
    assert render_tool_directory([], [], Path(tmp_path)) == ""

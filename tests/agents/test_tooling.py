"""Neutral tool layer: ToolSpec / tool() / ToolServer / tool_id / as_tool_spec."""
from __future__ import annotations

import asyncio

from lightfall.agents.tooling import ToolServer, ToolSpec, as_tool_spec, tool, tool_id


def test_tool_decorator_builds_toolspec():
    @tool("echo", "Echo back", {"text": str})
    async def echo(args):
        return {"content": [{"type": "text", "text": args["text"]}]}

    assert isinstance(echo, ToolSpec)
    assert echo.name == "echo"
    assert echo.description == "Echo back"
    assert echo.input_schema == {"text": str}
    out = asyncio.run(echo.handler({"text": "hi"}))
    assert out["content"][0]["text"] == "hi"


def test_tool_id_matches_claude_wire_names():
    assert tool_id("qt", "screenshot") == "mcp__qt__screenshot"
    assert tool_id("bus", "*") == "mcp__bus__*"


def test_toolserver_is_hashable_tuple_of_tools():
    @tool("a", "A", {})
    async def a(args):
        return {"content": []}

    srv = ToolServer(name="x", version="1.0.0", tools=(a,))
    assert isinstance(srv.tools, tuple)
    assert srv.tools[0].name == "a"


def test_as_tool_spec_passthrough_for_toolspec():
    @tool("a", "A", {})
    async def a(args):
        return {"content": []}

    assert as_tool_spec(a) is a


def test_as_tool_spec_accepts_sdk_tool():
    from claude_agent_sdk import tool as sdk_tool

    @sdk_tool("legacy", "Legacy plugin tool", {"x": int})
    async def legacy(args):
        return {"content": []}

    spec = as_tool_spec(legacy)
    assert isinstance(spec, ToolSpec)
    assert (spec.name, spec.description, spec.input_schema) == (
        "legacy", "Legacy plugin tool", {"x": int},
    )
    assert spec.handler is legacy.handler


def test_as_tool_spec_rejects_nameless_objects():
    assert as_tool_spec(object()) is None

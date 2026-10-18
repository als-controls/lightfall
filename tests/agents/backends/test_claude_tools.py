"""Claude adapter turns neutral ToolServers into SDK MCP servers unchanged."""
from __future__ import annotations

from lightfall.agents.tooling import ToolServer, tool, tool_id


@tool("ping", "Ping", {})
async def _ping(args):
    return {"content": [{"type": "text", "text": "pong"}]}


def test_to_sdk_tool_preserves_fields():
    from claude_agent_sdk import SdkMcpTool

    from lightfall.agents.backends.claude_code.tools import to_sdk_tool

    sdk = to_sdk_tool(_ping)
    assert isinstance(sdk, SdkMcpTool)
    assert (sdk.name, sdk.description, sdk.input_schema, sdk.handler) == (
        "ping", "Ping", {}, _ping.handler,
    )


def test_to_sdk_server_is_sdk_server_config():
    from lightfall.agents.backends.claude_code.tools import to_sdk_server

    cfg = to_sdk_server(ToolServer("bus", "1.0.0", (_ping,)))
    assert cfg["type"] == "sdk"
    assert cfg["name"] == "bus"


def test_bus_and_skills_factories_return_toolservers():
    from lightfall.agents.bus_tools import BUS_ALLOWED_TOOLS, create_bus_tools_server
    from lightfall.agents.skill_tools import SKILLS_ALLOWED_TOOLS, create_skill_tools_server

    bus = create_bus_tools_server(lambda: "lightfall")
    skills = create_skill_tools_server(lambda: "lightfall", lambda: None)
    assert isinstance(bus, ToolServer) and bus.name == "bus"
    assert isinstance(skills, ToolServer) and skills.name == "skills"
    assert sorted(tool_id(bus.name, t.name) for t in bus.tools) == sorted(BUS_ALLOWED_TOOLS)
    assert [tool_id(skills.name, t.name) for t in skills.tools] == SKILLS_ALLOWED_TOOLS


def test_mixed_plugin_tools_assemble():
    """A plugin returning one ToolSpec + one raw SdkMcpTool still yields one
    SDK server keyed by plugin.name and both mcp__ ids."""
    from claude_agent_sdk import tool as sdk_tool

    from lightfall.claude._session_assembly import assemble_mcp_servers, collect_tool_servers
    from lightfall.plugins.tool_plugin import ToolPlugin

    @sdk_tool("legacy", "Legacy", {})
    async def legacy(args):
        return {"content": []}

    class _Mixed(ToolPlugin):
        name = "mixed"
        description = "mixed"

        def create_tools(self):
            return [_ping, legacy]

    servers, external, allowed = collect_tool_servers([_Mixed()])
    assert [s.name for s in servers] == ["mixed"]
    assert external == {}
    assert allowed == ["mcp__mixed__ping", "mcp__mixed__legacy"]

    sdk_servers, sdk_allowed = assemble_mcp_servers([_Mixed()])
    assert sdk_servers["mixed"]["type"] == "sdk"
    assert sdk_allowed == allowed

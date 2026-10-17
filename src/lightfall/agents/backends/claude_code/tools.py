"""Adapt neutral ToolSpec/ToolServer to claude_agent_sdk MCP servers."""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server

from lightfall.agents.tooling import ToolServer, ToolSpec


def to_sdk_tool(spec: ToolSpec) -> SdkMcpTool:
    return SdkMcpTool(
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema,
        handler=spec.handler,
        annotations=spec.annotations,
    )


def to_sdk_server(server: ToolServer) -> Any:
    return create_sdk_mcp_server(
        name=server.name,
        version=server.version,
        tools=[to_sdk_tool(t) for t in server.tools],
    )

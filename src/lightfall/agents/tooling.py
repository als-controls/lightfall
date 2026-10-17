"""Backend-neutral tool definitions for Lightfall agents.

Tools are written once as ``ToolSpec`` (same shape as the Claude SDK's
``SdkMcpTool``: name, description, input_schema, async handler taking the
args dict and returning an MCP-style ``{"content": [...], "is_error"?}``
dict). Backends adapt them: ``lightfall.agents.backends.claude_code.tools``
wraps them into SDK MCP servers.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    """One tool: wire name, model-facing description, input schema, handler."""

    name: str
    description: str
    input_schema: dict[str, Any] | type
    handler: ToolHandler
    annotations: Any = None


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any] | type,
    annotations: Any = None,
) -> Callable[[ToolHandler], ToolSpec]:
    """Decorator: same call shape as ``claude_agent_sdk.tool``, backend-neutral."""

    def wrap(handler: ToolHandler) -> ToolSpec:
        return ToolSpec(name, description, input_schema, handler, annotations)

    return wrap


def tool_id(server: str, tool: str) -> str:
    """Canonical tool identifier used in allow-lists and the permission whitelist."""
    return f"mcp__{server}__{tool}"


@dataclass(frozen=True)
class ToolServer:
    """A named group of tools exposed to a session as one MCP server."""

    name: str
    version: str
    tools: tuple[ToolSpec, ...]

    def tool_ids(self) -> list[str]:
        return [tool_id(self.name, t.name) for t in self.tools]


def as_tool_spec(obj: Any) -> ToolSpec | None:
    """Normalise a tool object to ``ToolSpec``.

    Accepts a ``ToolSpec`` or anything shaped like ``claude_agent_sdk.SdkMcpTool``
    (``.name``, ``.description``, ``.input_schema``, ``.handler``) so plugin repos
    that still use the SDK decorator keep working. Returns ``None`` when the
    object has no usable name/handler.
    """
    if isinstance(obj, ToolSpec):
        return obj
    name = getattr(obj, "name", None)
    handler = getattr(obj, "handler", None)
    if not name or handler is None:
        return None
    return ToolSpec(
        name=name,
        description=getattr(obj, "description", "") or "",
        input_schema=getattr(obj, "input_schema", {}) or {},
        handler=handler,
        annotations=getattr(obj, "annotations", None),
    )

"""Backend-neutral tool definitions for Lightfall agents.

Tools are written once as ``ToolSpec`` (same shape as the Claude SDK's
``SdkMcpTool``: name, description, input_schema, async handler taking the
args dict and returning an MCP-style ``{"content": [...], "is_error"?}``
dict). Backends adapt them: ``lightfall.agents.backends.claude_code.tools``
wraps them into SDK MCP servers.
"""
from __future__ import annotations

import types as builtin_types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import (
    Annotated,
    Any,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

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


def _py_type_schema(py_type: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema dict (SDK parity)."""
    origin = get_origin(py_type)

    # NotRequired/Required/ReadOnly survive include_extras=True; unwrap them
    if getattr(origin, "_name", None) in ("NotRequired", "Required", "ReadOnly"):
        return _py_type_schema(get_args(py_type)[0])

    if origin is Annotated:
        args = get_args(py_type)
        schema = _py_type_schema(args[0])
        for meta in args[1:]:
            if isinstance(meta, str):
                schema["description"] = meta
                break
        return schema

    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}

    origin = getattr(py_type, "__origin__", None)

    if origin is Union or isinstance(py_type, builtin_types.UnionType):
        args = py_type.__args__
        non_none = [a for a in args if a is not builtin_types.NoneType]
        if len(non_none) == 1:
            return _py_type_schema(non_none[0])
        return {"anyOf": [_py_type_schema(a) for a in non_none]}

    if origin is list:
        item_args = getattr(py_type, "__args__", None)
        if item_args:
            return {"type": "array", "items": _py_type_schema(item_args[0])}
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}

    if py_type is list:
        return {"type": "array"}
    if py_type is dict:
        return {"type": "object"}

    if is_typeddict(py_type):
        return _typeddict_schema(py_type)

    return {"type": "string"}


def _typeddict_schema(td_class: type) -> dict[str, Any]:
    """Convert a TypedDict class to a JSON Schema dict (SDK parity)."""
    hints = get_type_hints(td_class, include_extras=True)

    properties: dict[str, Any] = {}
    for field_name, field_type in hints.items():
        properties[field_name] = _py_type_schema(field_type)

    required_keys = getattr(td_class, "__required_keys__", set(properties.keys()))
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required_keys:
        schema["required"] = sorted(required_keys)
    return schema


def json_schema(spec: ToolSpec) -> dict[str, Any]:
    """The JSON Schema a backend sends for ``spec``'s input (SDK wire parity)."""
    source = spec.input_schema
    if isinstance(source, dict):
        if "type" in source and "properties" in source and isinstance(source["type"], str):
            return source
        properties = {name: _py_type_schema(ptype) for name, ptype in source.items()}
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties.keys()),
        }
    if is_typeddict(source):
        return _typeddict_schema(source)
    return {"type": "object", "properties": {}}

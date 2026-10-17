"""Fail-closed PreToolUse policy for agy sessions.

agy calls its PreToolUse hook before every tool call; ``decide`` maps the hook
payload to ``{"decision": "allow"|"deny", "reason": str}``. Only Lightfall's own
MCP servers (and external plugin servers configured with them) are usable;
everything else is denied.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.agents.backends.agy.ask_tool import ASK_TOOL_ID
from lightfall.agents.tooling import tool_id

_PREFIX = "lightfall_"


def _allow(reason: str) -> dict[str, str]:
    return {"decision": "allow", "reason": reason}


def _deny(reason: str) -> dict[str, str]:
    return {"decision": "deny", "reason": reason}


def _server_key(server_name: Any, server_keys: set[str]) -> str | None:
    if not isinstance(server_name, str) or not server_name.startswith(_PREFIX):
        return None
    key = server_name[len(_PREFIX):]
    return key if key in server_keys else None


def _under_roots(raw: Any, roots: list[Path]) -> bool:
    if not isinstance(raw, str) or not raw:
        return False
    try:
        target = Path(raw).resolve()
        for root in roots:
            # roots may be junctions/symlinks: accept the literal or the resolved root
            if target.is_relative_to(root.resolve()) or target.is_relative_to(root):
                return True
    except (OSError, ValueError):
        return False
    return False


async def decide(payload: dict, *, permission_manager: Any, require_approval: bool,
                 server_keys: set[str], allowed_view_roots: list[Path]) -> dict[str, str]:
    call = payload.get("toolCall") if isinstance(payload, dict) else None
    if not isinstance(call, dict):
        return _deny("Malformed hook payload.")
    name = call.get("name")
    args = call.get("args")
    if not isinstance(args, dict):
        args = {}

    if name == "call_mcp_tool":
        key = _server_key(args.get("ServerName"), server_keys)
        tool_name = args.get("ToolName")
        if key is None or not isinstance(tool_name, str) or not tool_name:
            return _deny(f"MCP server {args.get('ServerName')!r} is not available in Lightfall.")
        tid = tool_id(key, tool_name)
        tool_input = args.get("Arguments") or {}
        if tid == ASK_TOOL_ID:
            return _allow("ask_user is interactive")
        if not require_approval:
            return _allow("approval not required")
        if permission_manager.is_auto_approved(tid):
            return _allow("auto-approved")
        try:
            ok, msg = await permission_manager.request_permission(tid, tool_input)
        except Exception as e:  # noqa: BLE001 - fail closed
            return _deny(f"Permission system error: {e}")
        return _allow("approved by user") if ok else _deny(msg or "User denied permission")

    if name in ("list_resources", "read_resource"):
        if _server_key(args.get("ServerName"), server_keys) is not None:
            return _allow("Lightfall MCP resource probe")
        return _deny(f"MCP server {args.get('ServerName')!r} is not available in Lightfall.")

    if name == "view_file":
        if _under_roots(args.get("AbsolutePath"), allowed_view_roots):
            return _allow("Lightfall tool schema or plugin file")
        return _deny("view_file is only allowed inside the Lightfall agent home.")

    return _deny(f"Tool {name} is not available in Lightfall.")

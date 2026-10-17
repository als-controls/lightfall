"""Fail-closed PreToolUse policy for agy sessions.

agy calls its PreToolUse hook before every tool call; ``decide`` maps the hook
payload to ``{"decision": "allow"|"deny", "reason": str}``.

- Lightfall's own MCP servers (and external plugin servers configured with them)
  go through ``PermissionManager`` under their ``mcp__<key>__<tool>`` ids.
- The agy built-ins the operator agent is given (``AGY_BUILTIN_TOOLS``) go
  through ``PermissionManager`` under the name of the matching Claude Code
  built-in, so the read-only auto-approvals, ``acceptEdits`` and "Always allow"
  behave exactly as they do on the Claude backend.
- Everything else is denied.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

from typing import Any

from lightfall.agents.backends.agy.ask_tool import ASK_TOOL_ID
from lightfall.agents.tooling import tool_id

_PREFIX = "lightfall_"

# agy built-in -> Claude Code built-in it stands in for (the permission id).
# Names verified against agy 1.4.1's tool registry.
BUILTIN_PERMISSION_IDS: dict[str, str] = {
    "view_file": "Read",
    "list_dir": "LS",
    "grep_search": "Grep",
    "find_by_name": "Glob",
    "write_to_file": "Write",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "MultiEdit",
    "edit_file": "Edit",
    "run_command": "Bash",
    "read_url_content": "WebFetch",
    "search_web": "WebSearch",
}
AGY_BUILTIN_TOOLS: tuple[str, ...] = tuple(BUILTIN_PERMISSION_IDS)

# Display-only keys agy adds to every tool call's args.
_META_ARGS = ("toolAction", "toolSummary")


def _allow(reason: str) -> dict[str, str]:
    return {"decision": "allow", "reason": reason}


def _deny(reason: str) -> dict[str, str]:
    return {"decision": "deny", "reason": reason}


def _server_key(server_name: Any, server_keys: set[str]) -> str | None:
    if not isinstance(server_name, str) or not server_name.startswith(_PREFIX):
        return None
    key = server_name[len(_PREFIX):]
    return key if key in server_keys else None


async def _gate(tid: str, tool_input: dict, *, permission_manager: Any,
                require_approval: bool) -> dict[str, str]:
    if not require_approval:
        return _allow("approval not required")
    if permission_manager.is_auto_approved(tid):
        return _allow("auto-approved")
    try:
        ok, msg = await permission_manager.request_permission(tid, tool_input)
    except Exception as e:  # noqa: BLE001 - fail closed
        return _deny(f"Permission system error: {e}")
    return _allow("approved by user") if ok else _deny(msg or "User denied permission")


async def decide(payload: dict, *, permission_manager: Any, require_approval: bool,
                 server_keys: set[str]) -> dict[str, str]:
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
        if tid == ASK_TOOL_ID:
            return _allow("ask_user is interactive")
        return await _gate(tid, args.get("Arguments") or {},
                           permission_manager=permission_manager, require_approval=require_approval)

    if name in ("list_resources", "read_resource"):
        if _server_key(args.get("ServerName"), server_keys) is not None:
            return _allow("Lightfall MCP resource probe")
        return _deny(f"MCP server {args.get('ServerName')!r} is not available in Lightfall.")

    perm = BUILTIN_PERMISSION_IDS.get(name) if isinstance(name, str) else None
    if perm is not None:
        tool_input = {k: v for k, v in args.items() if k not in _META_ARGS}
        return await _gate(perm, tool_input,
                           permission_manager=permission_manager, require_approval=require_approval)

    return _deny(f"Tool {name} is not available in Lightfall.")

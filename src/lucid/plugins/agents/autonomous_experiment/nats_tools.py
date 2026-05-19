"""MCP tools that bridge the LUCID embedded agent to Tsuchinoko's NATS surface.

All tools share a single request helper. Each tool is stateless and
returns a dict shaped for the agent SDK (``success``, plus tool-specific
fields, or ``success: false`` with an ``error`` string).
"""
from __future__ import annotations

from typing import Any

from lucid.plugins.agents._mcp_helpers import mcp_result
from lucid.utils.logging import logger


def _ipc_request(subject: str, payload: dict, *, timeout: float = 5.0) -> dict | None:
    """Send a NATS request via the LUCID IPC service.

    Returns the decoded reply dict on success, or ``None`` if the
    broker did not reply within *timeout* seconds (or the IPC service
    is unavailable). Callers must distinguish ``None`` from
    ``{"status": "error", ...}``.
    """
    from lucid.ipc.service import get_ipc_service
    ipc = get_ipc_service()
    if ipc is None:
        return None
    return ipc.request(subject, payload, timeout_ms=int(timeout * 1000))


def _ipc_error_response() -> dict:
    return {
        "success": False,
        "error": "LUCID IPC is not running; enable it in Settings → IPC and retry.",
    }


def _wire_error_response(subject: str, reply: dict | None) -> dict | None:
    """Return a structured error dict, or None if the call succeeded."""
    if reply is None:
        return {
            "success": False,
            "error": f"No reply on '{subject}' (timeout or broker unreachable).",
        }
    if isinstance(reply, dict) and reply.get("status") == "error":
        return {
            "success": False,
            "error": reply.get("message", "<tsuchinoko returned an error with no message>"),
        }
    return None


def build_tools() -> list[Any]:
    try:
        from claude_agent_sdk import tool
    except ImportError:
        logger.warning(
            "claude_agent_sdk not available; autonomous_experiment tools disabled"
        )
        return []

    @tool(
        name="tsuchinoko_discover",
        description=(
            "Discover Tsuchinoko instances on the NATS bus. Returns at most "
            "one instance (multi-instance routing is out of scope). Empty "
            "list means no Tsuchinoko is running — tell the user to start one."
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def discover(args: dict) -> dict[str, Any]:
        from lucid.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        reply = _ipc_request("_tsuchinoko.discover", {}, timeout=2.0)
        if reply is None:
            return mcp_result({"success": True, "instances": []})
        return mcp_result({"success": True, "instances": [reply]})

    return [discover]

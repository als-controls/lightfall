"""Helper functions for MCP tool result formatting.

MCP (Model Context Protocol) requires tools to return results in a specific
format with a 'content' array containing typed content blocks. These helpers
ensure consistent formatting across all tool implementations.

Example usage:
    from lightfall.plugins.agents._mcp_helpers import mcp_result, mcp_error

    async def my_tool(args: dict) -> dict:
        if error_condition:
            return mcp_error("Something went wrong")
        return mcp_result({"status": "ok", "data": [1, 2, 3]})
"""

from __future__ import annotations

import json
from typing import Any


def mcp_result(data: Any, is_error: bool = False) -> dict[str, Any]:
    """Format a result for MCP protocol.

    Args:
        data: The data to return (will be JSON serialized if not a string)
        is_error: Whether this is an error result

    Returns:
        MCP-formatted result dict with content wrapper
    """
    if isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, indent=2, default=str)

    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


def mcp_error(message: str) -> dict[str, Any]:
    """Format an error result for MCP protocol.

    Args:
        message: Error message

    Returns:
        MCP-formatted error result
    """
    return mcp_result(message, is_error=True)

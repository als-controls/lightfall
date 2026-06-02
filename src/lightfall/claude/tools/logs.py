"""Log inspection tool for the embedded Claude agent.

Exposes the in-process :class:`~lightfall.utils.log_buffer.LogBuffer` so the
agent can look back at recent log records — typically to investigate
something unexpected that happened outside its own tool calls.
"""

from __future__ import annotations

import json
from typing import Any

from lightfall.utils.log_buffer import LogBuffer


_DEFAULT_MAX_COUNT = 50
_HARD_MAX_COUNT = 500


def _format_record(rec, *, include_exception: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": rec.timestamp.isoformat(),
        "level": rec.level,
        "name": rec.name,
        "function": rec.function,
        "line": rec.line,
        "thread": rec.thread,
        "message": rec.message,
    }
    if include_exception and rec.exception_info:
        payload["exception"] = rec.exception_info
    return payload


def create_logs_tool():
    """Create the ``get_recent_logs`` MCP tool.

    Read-only and bound to the process-wide :class:`LogBuffer` singleton,
    so it doesn't need a target window like the Qt tools do.
    """
    from claude_agent_sdk import tool

    @tool(
        name="get_recent_logs",
        description=(
            "Read recent log records captured from the running LUCID process. "
            "Useful for investigating something unexpected that happened outside "
            "your own tool calls (background tasks, plan execution, device IO, "
            "Qt event handlers). The buffer holds the most recent records at "
            "the application's configured level — older records are evicted as "
            "newer ones arrive, so call this soon after the event of interest."
        ),
        input_schema={
            "level": {
                "type": "string",
                "description": (
                    "Minimum level to return: TRACE, DEBUG, INFO, WARNING, "
                    "ERROR, or CRITICAL. Default WARNING."
                ),
                "default": "WARNING",
            },
            "since_seconds": {
                "type": "number",
                "description": (
                    "Return records emitted within the last N seconds. "
                    "Default 120 (two minutes)."
                ),
                "default": 120,
            },
            "max_count": {
                "type": "number",
                "description": (
                    f"Maximum records to return (default {_DEFAULT_MAX_COUNT}, "
                    f"capped at {_HARD_MAX_COUNT})."
                ),
                "default": _DEFAULT_MAX_COUNT,
            },
            "contains": {
                "type": "string",
                "description": (
                    "Optional case-insensitive substring filter on the "
                    "message body."
                ),
            },
            "name_prefix": {
                "type": "string",
                "description": (
                    "Optional logger-name prefix filter (e.g. 'lightfall.devices' "
                    "or 'lightfall.acquire')."
                ),
            },
            "include_exception": {
                "type": "boolean",
                "description": (
                    "Include formatted tracebacks for records that captured "
                    "an exception. Default true."
                ),
                "default": True,
            },
        },
    )
    async def get_recent_logs(args: dict) -> dict[str, Any]:
        try:
            level = args.get("level", "WARNING")
            since_seconds = float(args.get("since_seconds", 120))
            max_count = int(args.get("max_count", _DEFAULT_MAX_COUNT))
            max_count = max(1, min(max_count, _HARD_MAX_COUNT))
            contains = args.get("contains") or None
            name_prefix = args.get("name_prefix") or None
            include_exception = bool(args.get("include_exception", True))

            buf = LogBuffer.get_instance()
            records = buf.get_records(
                level=level,
                since_seconds=since_seconds,
                contains=contains,
                name_prefix=name_prefix,
                max_count=max_count,
            )

            payload = {
                "filter": {
                    "level": level,
                    "since_seconds": since_seconds,
                    "max_count": max_count,
                    "contains": contains,
                    "name_prefix": name_prefix,
                },
                "buffer_size": len(buf),
                "returned": len(records),
                "records": [
                    _format_record(r, include_exception=include_exception)
                    for r in records
                ],
            }

            return {
                "content": [
                    {"type": "text", "text": json.dumps(payload, indent=2)}
                ]
            }

        except Exception as e:
            import traceback

            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"get_recent_logs error: {e}\n{traceback.format_exc()}",
                    }
                ],
                "is_error": True,
            }

    return get_recent_logs

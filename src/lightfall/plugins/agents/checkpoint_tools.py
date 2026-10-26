"""Agent tools for position checkpoints: list, take, compare, restore.

Restoring submits an ordinary ``bps.mv`` plan to the RunEngine queue, so it
is queued, logged and abortable like any other plan; nothing moves from the
tool call itself.
"""

from __future__ import annotations

from typing import Any

from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result
from lightfall.plugins.tool_plugin import ToolPlugin


def _summary(cp) -> dict[str, Any]:
    return {
        "id": cp.id,
        "time": cp.display_time,
        "label": cp.label,
        "source": cp.source,
        "reason": cp.reason,
        "positioners": len(cp.positions),
        "unreadable": list(cp.skipped),
    }


class CheckpointToolsAgent(ToolPlugin):
    """Tools for the position checkpoint log."""

    @property
    def name(self) -> str:
        return "checkpoint_tools"

    @property
    def description(self) -> str:
        return "List, take, compare and restore position checkpoints"

    @property
    def category(self) -> str:
        return "acquisition"

    def create_tools(self) -> list[Any]:
        from lightfall.agents.tooling import tool

        @tool(
            name="lightfall_list_checkpoints",
            description=(
                "List recent position checkpoints, newest first. A checkpoint is "
                "taken automatically when each plan you submit starts, and users "
                "can take them by hand. Each has an id, time, label, source "
                "(agent/user) and the number of positioners recorded."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "number", "description": "Maximum to return (default 20)."},
                },
            },
        )
        async def list_checkpoints(args: dict) -> dict[str, Any]:
            from lightfall.acquire.checkpoints import CheckpointStore

            limit = max(1, int(args.get("limit", 20)))
            items = CheckpointStore.get_instance().list()[:limit]
            return mcp_result({"checkpoints": [_summary(c) for c in items]})

        @tool(
            name="lightfall_create_checkpoint",
            description=(
                "Record the current position of every positioner now, e.g. before "
                "a manual alignment or when the user says 'remember this'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Short name, e.g. 'aligned on sample 3'."},
                    "reason": {"type": "string", "description": "Optional longer note."},
                },
                "required": ["label"],
            },
        )
        async def create_checkpoint(args: dict) -> dict[str, Any]:
            import asyncio

            from lightfall.acquire.checkpoints import CheckpointStore

            label = str(args.get("label") or "").strip()
            if not label:
                return mcp_error("'label' is required")
            cp = await asyncio.to_thread(
                CheckpointStore.get_instance().capture,
                label, source="agent", reason=str(args.get("reason") or ""),
            )
            return mcp_result(_summary(cp))

        @tool(
            name="lightfall_compare_checkpoint",
            description=(
                "Compare a checkpoint with current positions. Returns each "
                "positioner's saved and current value and whether it changed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "checkpoint_id": {"type": "string"},
                    "changed_only": {"type": "boolean", "description": "Default true."},
                },
                "required": ["checkpoint_id"],
            },
        )
        async def compare_checkpoint(args: dict) -> dict[str, Any]:
            import asyncio

            from lightfall.acquire.checkpoints import CheckpointStore, compare

            cp = CheckpointStore.get_instance().get(str(args.get("checkpoint_id", "")))
            if cp is None:
                return mcp_error("No checkpoint with that id; use lightfall_list_checkpoints.")
            rows = await asyncio.to_thread(compare, cp)
            if args.get("changed_only", True):
                rows = [r for r in rows if r["changed"]]
            return mcp_result({"checkpoint": _summary(cp), "rows": rows})

        @tool(
            name="lightfall_restore_checkpoint",
            description=(
                "Queue a plan that moves positioners back to a checkpoint. Give "
                "'devices' to restore only some (dotted names as listed by "
                "lightfall_compare_checkpoint); by default every positioner that "
                "changed is restored. Always show the user what will move (from "
                "lightfall_compare_checkpoint) and get their OK first. Do not "
                "restore a pseudo-axis and its real motors together."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "checkpoint_id": {"type": "string"},
                    "devices": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string", "description": "Why, for the logbook."},
                },
                "required": ["checkpoint_id"],
            },
        )
        async def restore_checkpoint(args: dict) -> dict[str, Any]:
            import asyncio

            from lightfall.acquire.checkpoints import CheckpointStore

            cp = CheckpointStore.get_instance().get(str(args.get("checkpoint_id", "")))
            if cp is None:
                return mcp_error("No checkpoint with that id; use lightfall_list_checkpoints.")
            try:
                proc_id, targets = await asyncio.to_thread(
                    submit_restore, cp, args.get("devices"), str(args.get("reason") or ""),
                )
            except KeyError as e:
                return mcp_error(str(e))
            if proc_id is None:
                return mcp_result({"submitted": False, "message": "Nothing to restore: no changes."})
            return mcp_result({"submitted": True, "procedure_id": proc_id, "targets": targets})

        return [list_checkpoints, create_checkpoint, compare_checkpoint, restore_checkpoint]


def submit_restore(cp, names: list[str] | None, reason: str = "") -> tuple[str | None, dict[str, float]]:
    """Queue a restore of ``names`` (default: every changed positioner).

    Returns ``(procedure_id or None, targets)``. Raises KeyError for an
    unknown name. Shared by the agent tool and the Checkpoints panel."""
    from lightfall.acquire.checkpoints import _catalog_devices, compare, restore_plan
    from lightfall.acquire.engine import get_engine

    devices = _catalog_devices()
    if names:
        missing = [n for n in names if n not in cp.positions]
        if missing:
            raise KeyError(f"Not in checkpoint {cp.id}: {', '.join(missing)}")
        targets = {n: cp.positions[n] for n in names}
    else:
        targets = {r["name"]: r["saved"] for r in compare(cp, devices) if r["changed"]}
    if not targets:
        return None, {}
    plan = restore_plan(devices, targets)  # KeyError for unknown names, before queueing
    label = f"restore checkpoint {cp.id} ({cp.label})"
    md = {"reason": reason} if reason else {}
    proc_id = get_engine().submit(plan, name=label, skip_pre_submit=True, **md)
    return proc_id, targets


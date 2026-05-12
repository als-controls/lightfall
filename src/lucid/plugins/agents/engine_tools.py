"""MCP tools for RunEngine interaction and run data access.

Provides tools for Claude to interact with the NCS RunEngine:
- Get engine status, pause, resume, abort
- Access run history and scan data via Tiled
"""

from __future__ import annotations

from typing import Any

from lucid.plugins.agent_plugin import AgentPlugin
from lucid.plugins.agents._mcp_helpers import mcp_result
from lucid.utils.logging import logger


class EngineToolsAgent(AgentPlugin):
    """MCP tools for RunEngine control and run data access."""

    @property
    def name(self) -> str:
        return "engine_tools"

    @property
    def description(self) -> str:
        return "Tools for RunEngine control and run data access"

    @property
    def category(self) -> str:
        return "acquisition"

    def _get_session_manager(self):
        from lucid.auth.session import SessionManager
        return SessionManager.get_instance()

    def _check_device_control_permission(self) -> tuple[bool, str | None]:
        from lucid.auth.policy import Permission
        session = self._get_session_manager()
        if not session.check_permission(Permission.DEVICE_CONTROL):
            return False, "Permission denied: DEVICE_CONTROL required"
        return True, None

    def create_tools(self) -> list[Any]:
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, engine tools disabled")
            return []

        @tool(
            name="ncs_get_run_status",
            description="Get the current RunEngine status including state, busy flag, and current procedure info.",
            input_schema={"type": "object", "properties": {}},
        )
        async def get_run_status(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            def _get():
                try:
                    from lucid.acquire.engine import get_engine
                    engine = get_engine()

                    result = {
                        "success": True,
                        "state": engine.state_name,
                        "state_value": engine.state.name,
                    }

                    # Check current procedure
                    proc = engine.get_current_procedure()
                    if proc is not None:
                        result["current_procedure"] = {
                            "name": getattr(proc, "name", None),
                            "priority": getattr(proc, "priority", None),
                        }
                    else:
                        result["current_procedure"] = None

                    # Queue info
                    if hasattr(engine, "_queue"):
                        result["queue_size"] = engine._queue.qsize()

                    return mcp_result(result)
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_get)

        @tool(
            name="ncs_pause_plan",
            description="Pause the currently running plan. Use defer=true (default) for safe pause at next checkpoint, or defer=false for immediate pause.",
            input_schema={
                "type": "object",
                "properties": {
                    "defer": {
                        "type": "boolean",
                        "description": "If true, pause at next checkpoint (safe). If false, pause immediately.",
                        "default": True,
                    },
                },
            },
        )
        async def pause_plan(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            defer = args.get("defer", True)

            def _pause():
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return mcp_result({"success": False, "error": error}, is_error=True)

                try:
                    from lucid.acquire.engine import get_engine
                    engine = get_engine()
                    engine.pause(defer=defer)
                    logger.info("Plan pause requested (defer={})", defer)
                    return mcp_result({
                        "success": True,
                        "message": f"Pause requested (defer={defer})",
                        "state": engine.state_name,
                    })
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_pause)

        @tool(
            name="ncs_resume_plan",
            description="Resume a paused plan.",
            input_schema={"type": "object", "properties": {}},
        )
        async def resume_plan(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            def _resume():
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return mcp_result({"success": False, "error": error}, is_error=True)

                try:
                    from lucid.acquire.engine import get_engine
                    engine = get_engine()
                    engine.resume()
                    logger.info("Plan resumed")
                    return mcp_result({
                        "success": True,
                        "message": "Plan resumed",
                        "state": engine.state_name,
                    })
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_resume)

        @tool(
            name="ncs_abort_plan",
            description="Abort the currently running plan.",
            input_schema={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for aborting",
                        "default": "",
                    },
                },
            },
        )
        async def abort_plan(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            reason = args.get("reason", "")

            def _abort():
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return mcp_result({"success": False, "error": error}, is_error=True)

                try:
                    from lucid.acquire.engine import get_engine
                    engine = get_engine()
                    engine.abort(reason=reason)
                    logger.info("Plan aborted: {}", reason or "(no reason)")
                    return mcp_result({
                        "success": True,
                        "message": f"Plan aborted{': ' + reason if reason else ''}",
                        "state": engine.state_name,
                    })
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_abort)

        @tool(
            name="ncs_get_run_history",
            description="Get recent run history from the Tiled data catalog. Returns UIDs, plan names, start times, and exit status.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of runs to return (default: 10)",
                        "default": 10,
                    },
                },
            },
        )
        async def get_run_history(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            limit = args.get("limit", 10)

            def _get():
                try:
                    from lucid.services.tiled_service import TiledService
                    service = TiledService.get_instance()

                    if not service.is_connected or service._client is None:
                        return mcp_result({
                            "success": False,
                            "error": "Tiled service not connected",
                            "hint": "Check Tiled connection in preferences.",
                        }, is_error=True)

                    client = service._client
                    runs = []
                    # Tiled client is a catalog; iterate in reverse for most recent.
                    # NOTE: list(client) only returns the first page (default 100).
                    # Use client.items() to get ALL entries.
                    keys = [k for k, _ in client.items()]
                    for uid in reversed(keys[-limit:]):
                        try:
                            run = client[uid]
                            metadata = run.metadata
                            start = metadata.get("start", {})
                            stop = metadata.get("stop", {})
                            runs.append({
                                "uid": uid,
                                "plan_name": start.get("plan_name", "unknown"),
                                "time": start.get("time"),
                                "exit_status": stop.get("exit_status", "unknown"),
                                "num_points": stop.get("num_events", {}).get("primary"),
                            })
                        except Exception as e:
                            runs.append({"uid": uid, "error": str(e)})

                    return mcp_result({
                        "success": True,
                        "count": len(runs),
                        "runs": runs,
                    })
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_get)

        @tool(
            name="ncs_get_scan_data",
            description="Get data from a specific run by UID. Returns column names, shape, and first N rows of the primary data stream.",
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {
                        "type": "string",
                        "description": "Run UID",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Maximum number of rows to return (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["uid"],
            },
        )
        async def get_scan_data(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            uid = args["uid"]
            max_rows = args.get("max_rows", 50)

            def _get():
                from lucid.services.tiled_service import TiledService
                service = TiledService.get_instance()

                if not service.is_connected or service._client is None:
                    return mcp_result({
                        "success": False,
                        "error": "Tiled service not connected",
                    }, is_error=True)

                client = service._client
                try:
                    run = client[uid]
                except KeyError:
                    return mcp_result({
                        "success": False,
                        "error": f"Run '{uid}' not found",
                    }, is_error=True)

                try:
                    if "primary" not in run:
                        return mcp_result({
                            "success": False,
                            "error": "No 'primary' stream found",
                            "available_streams": list(run),
                            "uid": uid,
                        })

                    from lucid.utils.tiled_helpers import read_events
                    stream = run["primary"]
                    events = read_events(stream)

                    if events is None:
                        return mcp_result({
                            "success": False,
                            "error": "No readable data in primary stream",
                            "stream_keys": list(stream.keys()),
                            "uid": uid,
                        })

                    # events is an xarray Dataset (or similar mapping).
                    # Use dict-style access — .keys() and dataset[col].
                    import numpy as np
                    all_cols = list(events.keys())
                    # Filter out timestamp columns (ts_*) for cleaner output
                    columns = [c for c in all_cols if not c.startswith("ts_")]
                    n_rows = len(np.asarray(events[columns[0]])) if columns else 0
                    shape = [n_rows, len(columns)]

                    # Build row dicts from the column arrays
                    rows = []
                    limit = min(n_rows, max_rows)
                    col_arrays = {c: np.asarray(events[c])[:limit] for c in columns}
                    for i in range(limit):
                        row = {}
                        for c in columns:
                            val = col_arrays[c][i]
                            # Convert numpy types to Python natives for JSON
                            row[c] = val.item() if hasattr(val, "item") else val
                        rows.append(row)

                    return mcp_result({
                        "success": True,
                        "uid": uid,
                        "columns": columns,
                        "shape": shape,
                        "rows_returned": len(rows),
                        "data": rows,
                    })
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_get)

        @tool(
            name="ncs_get_last_run",
            description="Get metadata for the most recent run (shortcut). Returns UID, plan_name, start/stop time, and exit status.",
            input_schema={"type": "object", "properties": {}},
        )
        async def get_last_run(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            def _get():
                try:
                    from lucid.services.tiled_service import TiledService
                    service = TiledService.get_instance()

                    if not service.is_connected or service._client is None:
                        return mcp_result({
                            "success": False,
                            "error": "Tiled service not connected",
                        }, is_error=True)

                    client = service._client
                    # NOTE: list(client) only returns the first page (default 100).
                    # Use client.items() to get ALL entries.
                    keys = [k for k, _ in client.items()]
                    if not keys:
                        return mcp_result({
                            "success": False,
                            "error": "No runs found in catalog",
                        })

                    uid = keys[-1]
                    run = client[uid]
                    metadata = run.metadata
                    start = metadata.get("start", {})
                    stop = metadata.get("stop", {})

                    return mcp_result({
                        "success": True,
                        "uid": uid,
                        "plan_name": start.get("plan_name", "unknown"),
                        "start_time": start.get("time"),
                        "stop_time": stop.get("time"),
                        "exit_status": stop.get("exit_status", "unknown"),
                        "num_points": stop.get("num_events", {}).get("primary"),
                        "streams": list(run),
                    })
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_get)

        @tool(
            name="ncs_show_run",
            description=(
                "Display a Bluesky run by uid in the Visualization panel. "
                "Opens the panel if it isn't already shown. "
                "Use ncs_get_last_run or ncs_get_run_history to find a uid."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {
                        "type": "string",
                        "description": "Run uid (start document uid).",
                    },
                },
                "required": ["uid"],
            },
        )
        async def show_run(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread

            uid = args.get("uid")
            if not uid:
                return mcp_result(
                    {"success": False, "error": "uid is required"},
                    is_error=True,
                )

            def _show():
                try:
                    from lucid.core.services import ServiceRegistry
                    from lucid.services.tiled_service import TiledService
                    from lucid.ui.docking import DockingManager
                    from lucid.ui.panels.visualization_panel import VisualizationPanel

                    service = TiledService.get_instance()
                    if not service.is_connected or service._client is None:
                        return mcp_result(
                            {"success": False, "error": "Tiled service not connected"},
                            is_error=True,
                        )
                    try:
                        entry = service._client[uid]
                    except KeyError:
                        return mcp_result(
                            {"success": False, "error": f"Run uid {uid!r} not found"},
                            is_error=True,
                        )

                    dm = ServiceRegistry.get_instance().get(DockingManager, None)
                    if dm is None:
                        return mcp_result(
                            {"success": False, "error": "DockingManager not available"},
                            is_error=True,
                        )

                    viz_panel_id = "lucid.panels.visualization"
                    dm.show_panel(viz_panel_id)
                    panel = dm.get_panel(viz_panel_id)
                    if not isinstance(panel, VisualizationPanel):
                        return mcp_result(
                            {"success": False, "error": "Visualization panel unavailable"},
                            is_error=True,
                        )

                    panel.open_run(entry)
                    return mcp_result({"success": True, "uid": uid})
                except Exception as e:
                    logger.exception("ncs_show_run failed")
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_show)

        return [
            get_run_status,
            pause_plan,
            resume_plan,
            abort_plan,
            get_run_history,
            get_scan_data,
            get_last_run,
            show_run,
        ]

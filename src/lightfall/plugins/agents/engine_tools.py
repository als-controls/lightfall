"""MCP tools for RunEngine interaction and run data access.

Provides tools for Claude to interact with the NCS RunEngine:
- Get engine status, pause, resume, abort
- Access run history and scan data via Tiled
"""

from __future__ import annotations

import asyncio
from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.plugins.agents._mcp_helpers import mcp_result
from lightfall.utils.logging import logger

# Upper bound on lightfall_wait_for_idle.timeout_seconds. Long enough for any
# realistic scan, short enough that a typo can't strand the model for
# the rest of the session.
_WAIT_TIMEOUT_MAX_SECONDS = 3600.0
# Smallest practical poll cadence — prevents busy-spinning if the model
# passes 0 or a negative number. Crossing to the GUI thread on every
# tick is cheap but not free.
_WAIT_POLL_MIN_SECONDS = 0.01
# Grace window below which "engine was idle on first poll" rather than
# "we waited and no run opened" — used by the plan_never_started check.
# 50 ms comfortably exceeds the immediate-return case (single GUI hop +
# loop overhead) without overlapping any realistic polling cadence.
_PLAN_START_GRACE_S = 0.05
# How long to wait before re-checking the most-recent Tiled uid when it
# looks like the plan didn't open a run. Tiled occasionally finishes
# indexing slightly *after* the engine flips back to IDLE — without this
# retry, a successful scan can be reported as plan_never_started.
_TILED_INDEX_RETRY_S = 0.5


def _last_run_payload() -> dict[str, Any] | None:
    """Return metadata for the most recent Tiled run, or None if unavailable.

    Designed to be called on the GUI thread (it touches ``TiledService``).
    Errors are swallowed and surfaced as ``None`` — callers embed this in
    larger payloads and shouldn't fail just because Tiled is down.
    """
    try:
        from lightfall.services.tiled_service import TiledService

        service = TiledService.get_instance()
        if not service.is_connected or service._client is None:
            return None

        client = service._client
        keys = [k for k, _ in client.items()]
        if not keys:
            return None

        uid = keys[-1]
        run = client[uid]
        metadata = run.metadata
        start = metadata.get("start", {})
        stop = metadata.get("stop", {})
        return {
            "uid": uid,
            "plan_name": start.get("plan_name", "unknown"),
            "start_time": start.get("time"),
            "stop_time": stop.get("time"),
            "exit_status": stop.get("exit_status", "unknown"),
            "num_points": stop.get("num_events", {}).get("primary"),
            "streams": list(run),
        }
    except Exception:
        logger.exception("_last_run_payload failed")
        return None


def _most_recent_uid() -> str | None:
    """Return only the most-recent Tiled key (or None if unavailable).

    Built on top of ``_last_run_payload`` rather than touching Tiled
    directly so test stubs that patch ``_last_run_payload`` automatically
    affect this helper too. Used by ``_wait_for_idle_payload`` to snapshot
    "which run was latest before the wait" so it can distinguish a
    successful empty wait from a plan that failed before opening a run
    document.
    """
    payload = _last_run_payload()
    if not payload:
        return None
    uid = payload.get("uid")
    return uid if isinstance(uid, str) and uid else None


def _read_engine_state_snapshot() -> tuple[str, bool]:
    """Return ``(EngineState.name, is_idle)`` for the current engine.

    Runs on the GUI thread via ``run_on_main_thread``. Failures bubble up
    as a synthetic ``ERROR`` state so the wait loop can report something
    sensible on timeout instead of looping silently on an exception.
    """
    try:
        from lightfall.acquire.engine import get_engine

        engine = get_engine()
        return engine.state.name, engine.is_idle
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("lightfall_wait_for_idle: engine state read failed: {}", exc)
        return "ERROR", False


async def _wait_for_idle_payload(
    *,
    timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 0.5,
    include_last_run: bool = True,
) -> dict[str, Any]:
    """Block until the RunEngine reaches IDLE, the timeout elapses, or cancelled.

    Polls ``engine.is_idle`` on the GUI thread between cooperative
    ``asyncio.sleep`` calls so the SDK event loop stays responsive (cancel
    requests can still land, the worker can still drain).

    Returns the response envelope documented in the tool's MCP description,
    including a ``status`` field that distinguishes the three success modes:

    - ``"idle"``               — engine reached IDLE and (when requested)
                                 a fresh ``last_run`` was returned.
    - ``"plan_never_started"`` — engine reached IDLE but no new run document
                                 opened during the wait. The most likely
                                 cause is a plan that failed validation or
                                 raised before ``bps.open_run`` (e.g. an
                                 ``md`` dict with a reserved key of the wrong
                                 type — issue 7). ``last_run`` is null in
                                 this case so the caller cannot accidentally
                                 fit data from a stale prior run.
    - ``"timeout"``             — wait abandoned without reaching IDLE.

    On ``CancelledError`` the exception propagates — callers (the SDK worker)
    drain the response stream on cancel, so we must not block shutdown by
    swallowing it.
    """
    from lightfall.claude._internal.threading import run_on_main_thread

    timeout = max(0.0, min(float(timeout_seconds), _WAIT_TIMEOUT_MAX_SECONDS))
    interval = max(_WAIT_POLL_MIN_SECONDS, float(poll_interval_seconds))

    # Snapshot which run was most-recent BEFORE the wait so we can detect a
    # plan that finished (or failed) without opening a run document.
    # Skipped when include_last_run=False since the caller has explicitly
    # told us they don't care about run identity.
    initial_uid: str | None = (
        run_on_main_thread(_most_recent_uid) if include_last_run else None
    )

    loop = asyncio.get_event_loop()
    start = loop.time()
    last_state = "UNKNOWN"
    reached_idle = False

    while True:
        state_name, is_idle = run_on_main_thread(_read_engine_state_snapshot)
        last_state = state_name

        if is_idle:
            reached_idle = True
            break

        elapsed = loop.time() - start
        if elapsed >= timeout:
            break

        # Cap sleep so we don't overshoot the deadline on the final tick.
        remaining = timeout - elapsed
        await asyncio.sleep(min(interval, remaining))

    elapsed = loop.time() - start
    last_run: dict[str, Any] | None = None
    status = "idle" if reached_idle else "timeout"

    if reached_idle and include_last_run:
        final_uid = run_on_main_thread(_most_recent_uid)
        # If we actually slept (the engine was busy when we arrived) AND
        # the latest run document didn't change, the submitted plan never
        # opened a run — we masquerade as success if we return the old
        # last_run (the original failure mode). The grace window keeps the
        # "engine was already idle on first poll" case classified as idle.
        actually_waited = elapsed > max(interval, _PLAN_START_GRACE_S)
        if actually_waited and final_uid == initial_uid:
            # Tiled sometimes finishes indexing the new run shortly *after*
            # the engine flips back to IDLE. Re-check once after a brief
            # pause before declaring plan_never_started — otherwise a
            # genuinely successful scan can be mislabeled and the agent
            # refuses to fit perfectly good data.
            await asyncio.sleep(_TILED_INDEX_RETRY_S)
            final_uid = run_on_main_thread(_most_recent_uid)
        if actually_waited and final_uid == initial_uid:
            status = "plan_never_started"
            last_run = None
        else:
            last_run = run_on_main_thread(_last_run_payload)

    return {
        "success": True,
        "reached_idle": reached_idle,
        "state": last_state,
        "elapsed_seconds": elapsed,
        "last_run": last_run,
        "status": status,
        "reason": "" if reached_idle else "timeout",
    }


def _beam_status_payload(force_refresh: bool = False) -> dict[str, Any]:
    """Read the ALS beam status via the polling service.

    Values reflect the service's most recent successful poll. ``force_refresh``
    kicks an immediate background poll; freshly-polled values may not be
    reflected until that poll completes, so the returned snapshot can lag by
    one cycle.
    """
    from lightfall.services.als_beam_status import ALSBeamStatusService

    service = ALSBeamStatusService.get_instance()
    if force_refresh:
        service.force_refresh()
    data = service.get_introspection_data()
    data["success"] = True
    return data


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
        from lightfall.auth.session import SessionManager
        return SessionManager.get_instance()

    def _check_device_control_permission(self) -> tuple[bool, str | None]:
        from lightfall.auth.policy import Permission
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
            name="lightfall_get_run_status",
            description="Get the current RunEngine status including state, busy flag, and current procedure info.",
            input_schema={"type": "object", "properties": {}},
        )
        async def get_run_status(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread

            def _get():
                try:
                    from lightfall.acquire.engine import get_engine
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
            name="lightfall_pause_plan",
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
            from lightfall.claude._internal.threading import run_on_main_thread

            defer = args.get("defer", True)

            def _pause():
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return mcp_result({"success": False, "error": error}, is_error=True)

                try:
                    from lightfall.acquire.engine import get_engine
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
            name="lightfall_resume_plan",
            description="Resume a paused plan.",
            input_schema={"type": "object", "properties": {}},
        )
        async def resume_plan(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread

            def _resume():
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return mcp_result({"success": False, "error": error}, is_error=True)

                try:
                    from lightfall.acquire.engine import get_engine
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
            name="lightfall_abort_plan",
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
            from lightfall.claude._internal.threading import run_on_main_thread

            reason = args.get("reason", "")

            def _abort():
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return mcp_result({"success": False, "error": error}, is_error=True)

                try:
                    from lightfall.acquire.engine import get_engine
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
            name="lightfall_get_run_history",
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
            from lightfall.claude._internal.threading import run_on_main_thread

            limit = args.get("limit", 10)

            def _get():
                try:
                    from lightfall.services.tiled_service import TiledService
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
            name="lightfall_get_scan_data",
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
            from lightfall.claude._internal.threading import run_on_main_thread

            uid = args["uid"]
            max_rows = args.get("max_rows", 50)

            def _get():
                from lightfall.services.tiled_service import TiledService
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

                    from lightfall.utils.tiled_helpers import read_events
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
            name="lightfall_get_last_run",
            description="Get metadata for the most recent run (shortcut). Returns UID, plan_name, start/stop time, and exit status.",
            input_schema={"type": "object", "properties": {}},
        )
        async def get_last_run(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread

            def _get():
                try:
                    from lightfall.services.tiled_service import TiledService
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
            name="lightfall_show_run",
            description=(
                "Display a Bluesky run by uid in the Visualization panel. "
                "Opens the panel if it isn't already shown. "
                "Use lightfall_get_last_run or lightfall_get_run_history to find a uid."
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
            from lightfall.claude._internal.threading import run_on_main_thread

            uid = args.get("uid")
            if not uid:
                return mcp_result(
                    {"success": False, "error": "uid is required"},
                    is_error=True,
                )

            def _show():
                try:
                    from lightfall.core.services import ServiceRegistry
                    from lightfall.services.tiled_service import TiledService
                    from lightfall.ui.docking import DockingManager
                    from lightfall.ui.panels.visualization_panel import VisualizationPanel

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

                    viz_panel_id = "lightfall.panels.visualization"
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
                    logger.exception("lightfall_show_run failed")
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_show)

        @tool(
            name="lightfall_wait_for_idle",
            description=(
                "Block until the RunEngine returns to IDLE, then return its state, "
                "an outcome `status`, and (optionally) the most recent run's "
                "metadata. Use this to wait for a scan to finish before fitting "
                "data or running follow-up plans. This suspends the model inside "
                "the tool call until the engine is idle, so prefer it over polling "
                "lightfall_get_run_status in a loop, and over ScheduleWakeup — "
                "ScheduleWakeup currently does not fire in Lightfall's embedded "
                "Claude session.\n"
                "\n"
                "status values:\n"
                "  - 'idle'               : engine reached IDLE; `last_run` is the\n"
                "                           run that just finished.\n"
                "  - 'plan_never_started' : engine returned to IDLE but no new run\n"
                "                           document opened during the wait — the\n"
                "                           submitted plan failed before bps.open_run.\n"
                "                           `last_run` is null (a stale prior run is\n"
                "                           intentionally suppressed). Inspect engine\n"
                "                           logs; do NOT fit any data.\n"
                "  - 'timeout'            : abandoned without reaching IDLE.\n"
                "\n"
                "Defaults: timeout_seconds=600 (max 3600), poll_interval=0.5s."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Abandon and return reason='timeout' if the engine isn't idle by then. Clamped to [0, 3600].",
                        "default": 600.0,
                    },
                    "poll_interval_seconds": {
                        "type": "number",
                        "description": "How often to re-check engine state. Clamped to a minimum of 0.01s.",
                        "default": 0.5,
                    },
                    "include_last_run": {
                        "type": "boolean",
                        "description": "When idle, include the most recent run's metadata (uid, plan_name, exit_status, …) so you don't need a second lightfall_get_last_run call.",
                        "default": True,
                    },
                },
            },
        )
        async def wait_for_idle(args: dict) -> dict[str, Any]:
            timeout_seconds = float(args.get("timeout_seconds", 600.0))
            poll_interval_seconds = float(args.get("poll_interval_seconds", 0.5))
            include_last_run = bool(args.get("include_last_run", True))

            try:
                payload = await _wait_for_idle_payload(
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    include_last_run=include_last_run,
                )
                return mcp_result(payload)
            except asyncio.CancelledError:
                # Propagate cleanly — the SDK worker drains the response
                # stream on cancel, and we must not block shutdown.
                raise
            except Exception as e:
                logger.exception("lightfall_wait_for_idle failed")
                return mcp_result({"success": False, "error": str(e)}, is_error=True)

        @tool(
            name="lightfall_get_beam_status",
            description=(
                "Get ALS storage-ring beam status: ring current (mA), beam/shutter "
                "availability, energy (GeV), lifetime (hours), beam-position stability, "
                "and the operations comment. Values reflect the most recent completed poll "
                "(the service polls about every 60 s). Use this to explain why a beamline "
                "diode reads no beam (ring dump vs shutter closed vs mis-steering). "
                "force_refresh=true schedules an immediate background poll, but its results "
                "appear on a SUBSEQUENT call, not this one."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Schedule an immediate background poll. Its result appears on a later call, not this one.",
                        "default": False,
                    },
                },
            },
        )
        async def get_beam_status(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread

            force = args.get("force_refresh", False)

            def _get():
                try:
                    return mcp_result(_beam_status_payload(force_refresh=force))
                except Exception as e:
                    return mcp_result({"success": False, "error": str(e)}, is_error=True)

            return run_on_main_thread(_get)

        return [
            get_run_status,
            pause_plan,
            resume_plan,
            abort_plan,
            get_run_history,
            get_scan_data,
            get_last_run,
            show_run,
            get_beam_status,
            wait_for_idle,
        ]

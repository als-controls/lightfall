"""MCP tools that bridge the LUCID embedded agent to Tsuchinoko's NATS surface.

All tools share a single request helper. Each tool is stateless and
returns a dict shaped for the agent SDK (``success``, plus tool-specific
fields, or ``success: false`` with an ``error`` string).
"""
from __future__ import annotations

from typing import Any

from lightfall.plugins.agents._mcp_helpers import mcp_result
from lightfall.utils.logging import logger


def _ipc_request(subject: str, payload: dict, *, timeout: float = 5.0) -> dict | None:
    """Send a NATS request via the LUCID IPC service.

    Returns the decoded reply dict on success, or ``None`` if the
    broker did not reply within *timeout* seconds (or the IPC service
    is unavailable). Callers must distinguish ``None`` from
    ``{"status": "error", ...}``.
    """
    from lightfall.ipc.service import get_ipc_service
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
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        reply = _ipc_request("_tsuchinoko.discover", {}, timeout=2.0)
        if reply is None:
            return mcp_result({"success": True, "instances": []})
        return mcp_result({"success": True, "instances": [reply]})

    @tool(
        name="tsuchinoko_upload_design_code",
        description=(
            "Upload an agent-authored callable (acquisition function, kernel, "
            "prior mean, or noise function) to the running Tsuchinoko instance. "
            "Returns the 'user:<name>' ref to use in tsuchinoko_configure. "
            "Tsuchinoko validates name (^[a-z][a-z0-9_]{0,62}$), kind "
            "(acquisition|kernel|prior_mean|noise), syntax, and the expected "
            "callable name before writing the file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["acquisition", "kernel", "prior_mean", "noise"],
                },
                "code": {"type": "string"},
            },
            "required": ["name", "kind", "code"],
        },
    )
    async def upload_design_code(args: dict) -> dict[str, Any]:
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        subject = "tsuchinoko.experiment.upload_design_code"
        reply = _ipc_request(subject, {
            "name": args["name"], "kind": args["kind"], "code": args["code"],
        }, timeout=5.0)
        err = _wire_error_response(subject, reply)
        if err is not None:
            return mcp_result(err, is_error=True)
        return mcp_result({
            "success": True,
            "ref": reply["ref"],
            "path": reply.get("path", ""),
        })

    @tool(
        name="tsuchinoko_configure",
        description=(
            "Send an experiment design to Tsuchinoko. IMPORTANT: configure only "
            "updates GP parameters — it does NOT reset the iteration counter, "
            "accumulated data, or run state. Always call tsuchinoko_stop() first "
            "and wait for Inactive state before configuring a new experiment. "
            "The payload schema is documented in the autonomous_experiment skill "
            "prompt (parameter_bounds, kernel, acquisition_function, prior_mean, "
            "noise_function, noise_variances, initial_points, training_method, "
            "hyperparameters, x_out, dimensionality). Unknown keys are an error "
            "— fix them before retrying. Use 'user:<name>' refs for callables "
            "previously uploaded via tsuchinoko_upload_design_code."
        ),
        input_schema={
            "type": "object",
            "properties": {"payload": {"type": "object"}},
            "required": ["payload"],
        },
    )
    async def configure(args: dict) -> dict[str, Any]:
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        subject = "tsuchinoko.experiment.configure"
        reply = _ipc_request(subject, args["payload"], timeout=5.0)
        err = _wire_error_response(subject, reply)
        if err is not None:
            return mcp_result(err, is_error=True)
        return mcp_result({"success": True})

    @tool(
        name="tsuchinoko_status",
        description=(
            "Query Tsuchinoko's current state. Returns "
            "{state, iteration, data_count}. Use this for textual progress "
            "checks during a running adaptive experiment."
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def status(args: dict) -> dict[str, Any]:
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        subject = "tsuchinoko.status"
        reply = _ipc_request(subject, {}, timeout=5.0)
        err = _wire_error_response(subject, reply)
        if err is not None:
            return mcp_result(err, is_error=True)
        return mcp_result({
            "success": True,
            "state": reply.get("state"),
            "iteration": reply.get("iteration"),
            "data_count": reply.get("data_count"),
        })

    def _make_control(action: str, subject: str, description: str):
        @tool(
            name=f"tsuchinoko_{action}",
            description=description,
            input_schema={"type": "object", "properties": {}},
        )
        async def control(args: dict) -> dict[str, Any]:
            from lightfall.ipc.service import get_ipc_service
            if get_ipc_service() is None:
                return mcp_result(_ipc_error_response(), is_error=True)
            reply = _ipc_request(subject, {}, timeout=5.0)
            err = _wire_error_response(subject, reply)
            if err is not None:
                return mcp_result(err, is_error=True)
            return mcp_result({
                "success": True,
                "state": reply.get("state"),
            })
        return control

    pause = _make_control(
        "pause", "tsuchinoko.experiment.pause",
        "Pause Tsuchinoko's adaptive loop. The LUCID adaptive_experiment "
        "plan keeps the run open; new targets stop until tsuchinoko_resume.",
    )
    resume = _make_control(
        "resume", "tsuchinoko.experiment.resume",
        "Resume a paused Tsuchinoko adaptive loop.",
    )
    stop = _make_control(
        "stop", "tsuchinoko.experiment.stop",
        "Stop Tsuchinoko's adaptive loop and finalise. The LUCID plan exits "
        "cleanly once targets stop arriving (configurable timeout).",
    )

    return [discover, upload_design_code, configure, status, pause, resume, stop]

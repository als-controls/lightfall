"""MCP tools exposing :class:`~lightfall.agents.bus.AgentBus` to agents.

Every :class:`~lightfall.claude.agent.QtClaudeAgent` session gets a dedicated
``bus`` SDK MCP server (see ``create_bus_tools_server``) so agents can
message each other and discover who else is on the bus, regardless of which
:class:`~lightfall.agents.spec.AgentSpec` is driving the session.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loguru import logger

from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result

try:
    from lightfall.claude._internal.threading import run_on_main_thread
except ImportError:  # pragma: no cover - SDK-optional / headless environments
    def run_on_main_thread(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)


BUS_ALLOWED_TOOLS = ["mcp__bus__send_message", "mcp__bus__list_agents"]

LIST_AGENTS_NOTE = (
    "Agents marked subagent_eligible appear in your Agent tool only from session "
    "start; delegating to a newly created one requires a session restart. Launch "
    "non-running openable agents via the open_agent_tab panel action."
)


def _base_name(registered_name: str) -> str:
    """Strip a bus-collision suffix like '#2' from a registered endpoint name."""
    return registered_name.split("#", 1)[0]


def agent_roster() -> list[dict]:
    """Merge defined agent specs (registry) with running endpoints (bus).

    Runs on the GUI thread (see the ``run_on_main_thread`` hop in
    ``list_agents`` below). For every enabled spec, reports whether a
    matching endpoint (exact name, or a suffixed collision variant like
    "name#2") is currently registered on the bus. Any bus-registered
    endpoint with no corresponding spec (legacy/suffixed-only entries) is
    appended with scope "runtime".

    If the registry cannot be enumerated, falls back to the plain bus list
    (running agents only) and logs a warning -- this must never raise.
    """
    from lightfall.agents.bus import AgentBus

    running_agents = AgentBus.get_instance().list_agents()
    running_names = [a["name"] for a in running_agents]
    running_bases = {_base_name(name) for name in running_names}
    running_descriptions = {a["name"]: a["description"] for a in running_agents}

    try:
        from lightfall.agents.registry import AgentSpecRegistry

        specs = AgentSpecRegistry.get_instance().enabled_specs()
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_roster: registry enumeration failed, falling back to bus list: {}", exc)
        return [
            {
                "name": a["name"],
                "description": a["description"],
                "scope": "runtime",
                "running": True,
                "openable": False,
                "subagent_eligible": False,
            }
            for a in running_agents
        ]

    spec_names = {spec.name for spec in specs}
    roster: list[dict] = []
    for spec in specs:
        roster.append(
            {
                "name": spec.name,
                "description": spec.description,
                "scope": spec.scope,
                "running": spec.name in running_bases,
                "openable": spec.openable,
                "subagent_eligible": spec.subagent,
            }
        )

    for name in running_names:
        if name not in spec_names:
            roster.append(
                {
                    "name": name,
                    "description": running_descriptions.get(name, ""),
                    "scope": "runtime",
                    "running": True,
                    "openable": False,
                    "subagent_eligible": False,
                }
            )

    return roster


def _make_tools(sender_name: Callable[[], str]):
    """Build the ``send_message`` / ``list_agents`` ``@tool`` callables.

    Args:
        sender_name: Callable returning this session's current bus name
            (read live at call time, since ``QtClaudeAgent.bus_name`` may be
            overwritten after registration).
    """
    from claude_agent_sdk import tool

    @tool(
        name="send_message",
        description=(
            "Send a message to another agent registered on the shared agent "
            "bus. Use list_agents first to see who's available."
        ),
        input_schema={
            "to": {
                "type": "string",
                "description": "Name of the target agent, as returned by list_agents.",
            },
            "message": {
                "type": "string",
                "description": "The message text to deliver.",
            },
        },
    )
    async def send_message(args: dict) -> dict[str, Any]:
        try:
            from lightfall.agents.bus import AgentBus

            to = args.get("to", "")
            message = args.get("message", "")
            result = run_on_main_thread(
                AgentBus.get_instance().send, sender_name(), to, message
            )
            is_error = result.get("status") != "delivered"
            return mcp_result(result, is_error=is_error)
        except Exception as exc:  # noqa: BLE001
            logger.exception("bus_tools.send_message failed")
            return mcp_error(f"send_message error: {exc}")

    @tool(
        name="list_agents",
        description=(
            "List the agents currently registered on the shared agent bus, "
            "including whether each one is busy."
        ),
        input_schema={},
    )
    async def list_agents(args: dict) -> dict[str, Any]:
        try:
            roster = run_on_main_thread(agent_roster)
            return mcp_result({"agents": roster, "note": LIST_AGENTS_NOTE})
        except Exception as exc:  # noqa: BLE001
            logger.exception("bus_tools.list_agents failed")
            return mcp_error(f"list_agents error: {exc}")

    return [send_message, list_agents]


def create_bus_tools_server(sender_name: Callable[[], str]):
    """Create the always-on ``bus`` SDK MCP server for a session.

    Args:
        sender_name: Callable returning the session's current bus name.

    Returns:
        MCP server instance with the ``send_message`` and ``list_agents`` tools.
    """
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(
        name="bus",
        version="1.0.0",
        tools=_make_tools(sender_name),
    )


__all__ = [
    "create_bus_tools_server",
    "_make_tools",
    "BUS_ALLOWED_TOOLS",
    "agent_roster",
    "LIST_AGENTS_NOTE",
]

"""MCP tools exposing skill drafting to agents.

Every :class:`~lightfall.claude.agent.QtClaudeAgent` session gets a dedicated
``skills`` SDK MCP server (see ``create_skill_tools_server``) so agents can
propose new skills or revisions to existing ones. Drafts are never applied
automatically -- a human must review and promote them out of ``_drafts/``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result
from lightfall.utils.logging import logger

try:
    from lightfall.claude._internal.threading import run_on_main_thread
except ImportError:  # pragma: no cover - SDK-optional / headless environments
    def run_on_main_thread(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)


SKILLS_ALLOWED_TOOLS = ["mcp__skills__draft_skill"]


def _make_tools(author_name: Callable[[], str], session_id: Callable[[], str | None]):
    """Build the ``draft_skill`` ``@tool`` callable.

    Args:
        author_name: Callable returning this session's current author/bus
            name (read live at call time).
        session_id: Callable returning this session's current session id
            (or ``None``).
    """
    from claude_agent_sdk import tool

    @tool(
        name="draft_skill",
        description=(
            "Draft a new skill, or propose a revision to an existing one. "
            "The draft is saved for human review and does not take effect "
            "until a human approves it by moving it out of _drafts/."
        ),
        input_schema={
            "name": {
                "type": "string",
                "description": "Skill name (lowercase alphanumeric, hyphens, underscores).",
            },
            "description": {
                "type": "string",
                "description": "One-line human-readable description of the skill.",
            },
            "body": {
                "type": "string",
                "description": "Skill body content (markdown).",
            },
        },
    )
    async def draft_skill(args: dict) -> dict[str, Any]:
        try:
            from lightfall.agents.drafts import DraftError, save_draft

            name = args.get("name", "")
            description = args.get("description", "")
            body = args.get("body", "")

            def _save() -> tuple[str, Any, bool]:
                # Caught HERE, on the main-thread side of the hop, rather
                # than relying on DraftError propagating back across
                # run_on_main_thread: the real implementation rewraps any
                # exception raised on the marshalled path as a generic
                # RuntimeError with a traceback dump, which would otherwise
                # bury the clean validation message.
                try:
                    path, is_revision = save_draft(
                        name,
                        description,
                        body,
                        author=author_name(),
                        session_id=session_id(),
                    )
                except DraftError as exc:
                    return ("invalid", str(exc), False)
                return ("ok", path, is_revision)

            status, payload, is_revision = run_on_main_thread(_save)
            if status == "invalid":
                return mcp_error(payload)
            path = payload

            message = (
                f"Draft saved to {path}. A human must approve it "
                "(move it out of _drafts/) before it takes effect."
            )
            if is_revision:
                message += (
                    f" This is a proposed REVISION of the active skill '{name}'."
                )

            _fire_toast(name, author_name())

            return mcp_result(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("skill_tools.draft_skill failed")
            return mcp_error(f"draft_skill error: {exc}")

    return [draft_skill]


def _fire_toast(name: str, author: str) -> None:
    try:
        def _show() -> None:
            from lightfall.ui.toast import ToastManager

            mgr = ToastManager.get_instance()
            mgr.info(
                f"Skill draft: {name}",
                f"drafted by {author} — pending approval",
            )

        run_on_main_thread(_show)
    except Exception:  # noqa: BLE001 — never let a toast failure break the run
        logger.exception("skill_tools toast failed")


def create_skill_tools_server(
    author_name: Callable[[], str], session_id: Callable[[], str | None]
):
    """Create the always-on ``skills`` SDK MCP server for a session.

    Args:
        author_name: Callable returning the session's current author/bus name.
        session_id: Callable returning the session's current session id.

    Returns:
        MCP server instance with the ``draft_skill`` tool.
    """
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(
        name="skills",
        version="1.0.0",
        tools=_make_tools(author_name, session_id),
    )


__all__ = ["create_skill_tools_server", "_make_tools", "SKILLS_ALLOWED_TOOLS"]

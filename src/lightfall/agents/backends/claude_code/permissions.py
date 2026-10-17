"""Claude SDK permission glue: PreToolUse hook + can_use_tool callback.

Both factories adapt a ``PermissionManager`` (backend-neutral approval/question
UI broker) to the claude_agent_sdk callback shapes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lightfall.claude.permission_manager import PermissionManager


def create_pre_tool_use_hook(
    permission_manager: PermissionManager,
    require_approval: bool = True,
):
    """
    Create a PreToolUse hook callback for the Claude Agent SDK.

    This hook fires before any tool is executed, providing robust
    permission interception for all tool types including MCP tools.

    Args:
        permission_manager: The PermissionManager instance
        require_approval: When True, gate normal tools through the
            approval UI. When False (e.g. ``permission_mode=
            bypassPermissions``), skip the gate for normal tools but
            still force ``AskUserQuestion`` through ``can_use_tool``
            so the question UI fires.

    Returns:
        Async hook callback compatible with HookMatcher.hooks
    """

    async def pre_tool_use_hook(
        hook_input,
        tool_use_id,
        context,
    ):
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})

        # AskUserQuestion needs to be handled in can_use_tool (which can
        # return updated_input with the user's answers); a hook can only
        # allow / deny / ask. Returning empty dict ("no decision") lets
        # the SDK fall back to its CLI default — which in headless mode
        # silently dismisses the question without ever asking us.
        # Explicitly returning permissionDecision="ask" forces the SDK
        # to route through can_use_tool where we render the question UI.
        # This applies regardless of ``require_approval`` — the question
        # tool is interactive by design.
        if tool_name == "AskUserQuestion":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                },
            }

        # When approvals are bypassed, defer to the CLI's permission
        # rules for all other tools — don't interpose our approval UI.
        if not require_approval:
            return {}

        # Check auto-approval first
        if permission_manager.is_auto_approved(tool_name):
            return {}  # Empty dict = allow (no override)

        # Request permission from UI with error handling
        try:
            allowed, message = await permission_manager.request_permission(
                tool_name, tool_input
            )
        except Exception as e:
            # If permission request fails (e.g., event loop dying),
            # deny to be safe rather than crashing
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Permission system error: {e}",
                }
            }

        if allowed:
            return {}  # Allow
        else:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": message or "User denied permission",
                }
            }

    return pre_tool_use_hook


def create_can_use_tool_callback(
    permission_manager: PermissionManager,
    require_approval: bool = True,
):
    """
    Create a can_use_tool callback for the Claude Agent SDK.

    This factory function creates an async callback that integrates
    with the PermissionManager for UI-based approval.

    Args:
        permission_manager: The PermissionManager instance
        require_approval: When True, route normal tools through the
            approval UI. When False, auto-allow normal tools but still
            handle ``AskUserQuestion`` via the question UI.

    Returns:
        Async callback function compatible with ClaudeAgentOptions.can_use_tool
    """
    from claude_agent_sdk import (
        PermissionResult,
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
    )

    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext
    ) -> PermissionResult:
        """
        Permission callback that waits for UI approval.

        Special case: ``AskUserQuestion`` is the CLI's built-in clarifying
        question tool. We render it as a question UI and inject the user's
        answers via ``updated_input``; ``PermissionResultAllow`` can carry
        that, hooks cannot, so this is the only place it can be handled.

        Args:
            tool_name: Name of the tool
            tool_input: Tool input parameters
            context: Permission context (unused currently)

        Returns:
            PermissionResult allowing or denying the tool use
        """
        if tool_name == "AskUserQuestion":
            questions = (
                tool_input.get("questions", [])
                if isinstance(tool_input, dict) else []
            )
            if not questions:
                return PermissionResultDeny(
                    message="AskUserQuestion called with no questions"
                )
            try:
                answered, answers = await permission_manager.request_question(
                    questions
                )
            except Exception as e:
                return PermissionResultDeny(
                    message=f"Question system error: {e}"
                )
            if not answered:
                return PermissionResultDeny(message="User declined to answer")
            return PermissionResultAllow(
                updated_input={"questions": questions, "answers": answers}
            )

        # If approvals are bypassed, auto-allow non-AskUserQuestion tools.
        # We're only here because some hook said "ask" (or the CLI's rules
        # otherwise routed through can_use_tool); honor the bypass intent.
        if not require_approval:
            return PermissionResultAllow()

        try:
            allowed, message = await permission_manager.request_permission(
                tool_name, tool_input
            )
        except Exception as e:
            return PermissionResultDeny(
                message=f"Permission system error: {e}"
            )

        if allowed:
            return PermissionResultAllow()
        else:
            return PermissionResultDeny(message=message or "User denied permission")

    return can_use_tool

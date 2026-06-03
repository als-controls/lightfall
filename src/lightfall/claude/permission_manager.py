"""Permission manager for coordinating tool approval between threads."""

import asyncio
import uuid
from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal


class PermissionManager(QObject):
    """
    Manages tool permissions and user preferences.

    This class coordinates between the background worker thread (running
    async Claude operations) and the main Qt thread (displaying UI).

    Thread Safety:
        - permission_requested signal is emitted from worker thread
        - Qt handles cross-thread signal delivery automatically
        - respond() is called from main thread, uses call_soon_threadsafe
          to wake the waiting coroutine

    Signals:
        permission_requested(str, str, dict): Emitted when a tool needs approval
            (request_id, tool_name, tool_input)
        auto_approvals_changed(set): Emitted when auto-approval list changes
    """

    # Signals
    permission_requested = Signal(str, str, dict)  # request_id, tool_name, tool_input
    question_requested = Signal(str, list)  # request_id, questions
    auto_approvals_changed = Signal(set)  # Current set of auto-approved tools

    # File-edit tools auto-approved when permission_mode == "acceptEdits"
    # (matches the Claude Agent SDK's own acceptEdits semantics).
    EDIT_TOOLS = frozenset({
        "Edit", "edit",
        "Write", "write",
        "NotebookEdit", "notebook_edit",
        "MultiEdit", "multi_edit",
    })

    # Default read-only tools that are auto-approved (whitelist design).
    # Only tools explicitly listed here skip the approval prompt.
    # Everything else requires user approval.
    DEFAULT_AUTO_APPROVED = frozenset({
        # Claude Code SDK built-in tools (read-only)
        "Read", "read",
        "Glob", "glob",
        "Grep", "grep",
        "LS", "ls",
        "ListFiles", "list_files",
        # Qt inspection tools
        "mcp__qt__screenshot",
        "mcp__qt__get_widget_tree",
        "mcp__qt__find_widget",
        "mcp__qt__get_recent_logs",
        # Read-only Lightfall tools (per-plugin namespaces after SDK-native migration).
        # LightfallCoreToolPlugin is manifest-driven via AgentRegistry (lightfall_core_tools server).
        "mcp__lightfall_core_tools__lightfall_list_panels",
        "mcp__lightfall_core_tools__lightfall_get_panel_info",
        "mcp__lightfall_core_tools__lightfall_get_application_info",
        "mcp__lightfall_core_tools__lightfall_set_emotion",
        # device_tools agent
        "mcp__device_tools__lightfall_list_devices",
        "mcp__device_tools__lightfall_get_device",
        "mcp__device_tools__lightfall_read_device",
        "mcp__device_tools__lightfall_get_device_state",
        "mcp__device_tools__lightfall_get_catalog_info",
        # plan_tools agent
        "mcp__plan_tools__lightfall_list_plans",
        "mcp__plan_tools__lightfall_get_user_plan",
        # engine_tools agent
        "mcp__engine_tools__lightfall_get_run_status",
        "mcp__engine_tools__lightfall_get_run_history",
        "mcp__engine_tools__lightfall_get_scan_data",
        "mcp__engine_tools__lightfall_get_last_run",
        "mcp__engine_tools__lightfall_wait_for_idle",
        # ipython_tools agent
        "mcp__ipython_tools__lightfall_ipython_get_namespace",
        # Bare names (fallback for SDK that may dispatch without prefix)
        "lightfall_set_emotion",
    })

    def __init__(
        self,
        parent: QObject | None = None,
        permission_mode: str = "default",
    ):
        """
        Initialize the permission manager.

        Follows a whitelist design: all tools require approval by default.
        Only tools explicitly added to the auto-approved set skip the prompt.

        Args:
            parent: Parent QObject
            permission_mode: SDK permission mode. When 'acceptEdits', file-edit
                tools (Edit/Write/NotebookEdit/MultiEdit) are added to the
                auto-approved set, matching the SDK's own acceptEdits semantics.
        """
        super().__init__(parent)

        # Auto-approved tools — whitelist only (configurable by external UI)
        self._auto_approved: set[str] = set(self.DEFAULT_AUTO_APPROVED)
        if permission_mode == "acceptEdits":
            self._auto_approved |= self.EDIT_TOOLS

        # User's "Always Allow" choices (session-based, can be persisted)
        self._user_always_allowed: set[str] = set()

        # Pending permission requests
        self._pending_requests: dict[str, asyncio.Event] = {}
        self._pending_loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._responses: dict[str, tuple[bool, str]] = {}  # (allowed, message)

        # Pending AskUserQuestion requests — parallel plumbing to permissions,
        # but the response is a free-form answers dict, not a (bool, str) pair.
        self._pending_questions: dict[str, asyncio.Event] = {}
        self._pending_question_loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._question_responses: dict[str, dict[str, str] | None] = {}

        # Load saved preferences (but validate against whitelist policy)
        self._load_preferences()

    # --- Public API for external configuration ---

    @property
    def auto_approved_tools(self) -> set[str]:
        """
        Get current set of auto-approved tools.

        This includes both configured auto-approved tools and
        user's "Always Allow" choices.

        Returns:
            Set of tool names that are auto-approved
        """
        return self._auto_approved | self._user_always_allowed

    def set_auto_approved(self, tools: set[str]) -> None:
        """
        Set the base auto-approved tools.

        Called by external settings UI to configure which tools
        are auto-approved by default.

        Args:
            tools: Set of tool names to auto-approve
        """
        self._auto_approved = set(tools)
        self.auto_approvals_changed.emit(self.auto_approved_tools)

    def add_always_allowed(self, tool_name: str) -> None:
        """
        Add tool to user's 'Always Allow' list (session-scoped).

        This approval is cleared on next application startup
        (whitelist design — tools must be re-approved each session).

        Args:
            tool_name: Name of tool to always allow
        """
        self._user_always_allowed.add(tool_name)
        self.auto_approvals_changed.emit(self.auto_approved_tools)

    def remove_always_allowed(self, tool_name: str) -> None:
        """
        Remove tool from user's 'Always Allow' list.

        Args:
            tool_name: Name of tool to remove
        """
        self._user_always_allowed.discard(tool_name)
        self.auto_approvals_changed.emit(self.auto_approved_tools)

    def clear_always_allowed(self) -> None:
        """Clear all user 'Always Allow' choices."""
        self._user_always_allowed.clear()
        self.auto_approvals_changed.emit(self.auto_approved_tools)

    def is_auto_approved(self, tool_name: str) -> bool:
        """
        Check if a tool is auto-approved.

        Args:
            tool_name: Name of the tool to check

        Returns:
            True if tool is auto-approved
        """
        return tool_name in self.auto_approved_tools

    # --- Permission request handling ---

    def cancel_all_pending(self) -> None:
        """Cancel all pending permission requests.

        Denies all waiting requests, waking their coroutines.
        Called when the user cancels a query so pending approval
        dialogs don't block the cancellation.
        """
        for request_id in list(self._pending_requests.keys()):
            self._responses[request_id] = (False, "Query cancelled by user")
            loop = self._pending_loops.get(request_id)
            event = self._pending_requests.get(request_id)
            if loop and event:
                try:
                    loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    pass
        # Also wake pending question requests with a cancel response.
        for request_id in list(self._pending_questions.keys()):
            self._question_responses[request_id] = None
            loop = self._pending_question_loops.get(request_id)
            event = self._pending_questions.get(request_id)
            if loop and event:
                try:
                    loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    pass

    async def request_permission(
        self,
        tool_name: str,
        tool_input: dict[str, Any]
    ) -> tuple[bool, str]:
        """
        Request permission for a tool use.

        Called from the worker thread's async context. This method:
        1. Checks if tool is auto-approved
        2. If not, emits signal to UI and waits for response
        3. Returns the permission result

        Args:
            tool_name: Name of the tool requesting permission
            tool_input: The tool's input parameters

        Returns:
            Tuple of (allowed: bool, message: str)
        """
        # Check auto-approval first
        if self.is_auto_approved(tool_name):
            return (True, "")

        # Generate request ID
        request_id = str(uuid.uuid4())

        # Create event for waiting
        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        self._pending_requests[request_id] = event
        self._pending_loops[request_id] = loop

        # Emit signal to main thread (Qt handles cross-thread delivery)
        self.permission_requested.emit(request_id, tool_name, tool_input)

        # Wait for response with timeout to prevent hanging if CLI dies
        try:
            await asyncio.wait_for(event.wait(), timeout=300)  # 5 min timeout
        except TimeoutError:
            # Clean up and deny — user didn't respond in time
            self._pending_requests.pop(request_id, None)
            self._pending_loops.pop(request_id, None)
            self._responses.pop(request_id, None)
            return (False, "Permission request timed out")

        # Clean up and return result
        self._pending_requests.pop(request_id, None)
        self._pending_loops.pop(request_id, None)

        return self._responses.pop(request_id, (False, "No response received"))

    def respond(
        self,
        request_id: str,
        allowed: bool,
        always: bool = False,
        message: str = ""
    ) -> None:
        """
        Respond to a permission request.

        Called from the main thread when user clicks a button.

        Args:
            request_id: The request ID to respond to
            allowed: Whether permission is granted
            always: If True and allowed, add tool to always-allowed list
            message: Optional message (used for deny reason)
        """
        if request_id not in self._pending_requests:
            # Request already handled or expired
            return

        # Store response
        self._responses[request_id] = (allowed, message)

        # If "Always Allow" was selected, add to the list
        # We need to get the tool name - it's not directly available here,
        # so we rely on the caller to handle that separately via add_always_allowed()

        # Wake up the waiting coroutine (thread-safe)
        loop = self._pending_loops.get(request_id)
        event = self._pending_requests.get(request_id)

        if loop and event:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # Loop already closed — clean up silently
                self._pending_requests.pop(request_id, None)
                self._pending_loops.pop(request_id, None)
                self._responses.pop(request_id, None)

    async def request_question(
        self, questions: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, str]]:
        """Request the user to answer one or more multi-choice questions.

        Emits ``question_requested`` and waits for a response via
        ``respond_to_question``. Returns ``(answered, answers)`` where
        ``answered=False`` means the user cancelled.
        """
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        self._pending_questions[request_id] = event
        self._pending_question_loops[request_id] = loop

        self.question_requested.emit(request_id, questions)

        try:
            await asyncio.wait_for(event.wait(), timeout=300)
        except TimeoutError:
            self._pending_questions.pop(request_id, None)
            self._pending_question_loops.pop(request_id, None)
            self._question_responses.pop(request_id, None)
            return (False, {})

        self._pending_questions.pop(request_id, None)
        self._pending_question_loops.pop(request_id, None)
        answers = self._question_responses.pop(request_id, None)
        if answers is None:
            return (False, {})
        return (True, answers)

    def respond_to_question(
        self, request_id: str, answers: dict[str, str] | None
    ) -> None:
        """Provide answers to a pending question request.

        Pass ``answers=None`` to indicate the user cancelled.
        """
        if request_id not in self._pending_questions:
            return
        self._question_responses[request_id] = answers
        loop = self._pending_question_loops.get(request_id)
        event = self._pending_questions.get(request_id)
        if loop and event:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                self._pending_questions.pop(request_id, None)
                self._pending_question_loops.pop(request_id, None)
                self._question_responses.pop(request_id, None)

    def has_pending_request(self, request_id: str) -> bool:
        """
        Check if a request is still pending.

        Args:
            request_id: The request ID to check

        Returns:
            True if request is still pending
        """
        return request_id in self._pending_requests

    # --- Persistence ---

    def save_preferences(self) -> None:
        """Save user preferences to QSettings.

        Note: Under the whitelist design, persisted always-allowed tools
        are cleared on next startup. This save is only useful for
        within-session widget recreation (e.g., panel re-initialization).
        """
        settings = QSettings("ncs", "pyside-claude")
        settings.setValue(
            "permission/user_always_allowed",
            list(self._user_always_allowed)
        )

    def _load_preferences(self) -> None:
        """Load user preferences from QSettings.

        Follows whitelist design: persisted "Always Allow" choices are
        cleared on startup. Users must re-approve tools each session
        for security. Only the base auto-approved set (read-only Qt
        introspection tools) persists across sessions.
        """
        # Clear any previously persisted always-allowed tools.
        # This ensures a clean slate each session (whitelist design).
        settings = QSettings("ncs", "pyside-claude")
        settings.remove("permission/user_always_allowed")


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

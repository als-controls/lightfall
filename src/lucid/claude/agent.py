"""QtClaudeAgent - Low-level API for Claude integration with Qt."""

import os
import platform
import tempfile
from typing import Any

from lucid.utils.logging import logger
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from lucid.claude.tools import create_qt_tools_server
from lucid.claude._internal.worker import PersistentClaudeWorker
from lucid.claude.permission_manager import (
    PermissionManager,
    create_can_use_tool_callback,
    create_pre_tool_use_hook,
)


def _patch_sdk_for_windows_cmdline_limit():
    """
    Monkey-patch the Claude Agent SDK to handle Windows command line length limits.

    Windows has an 8191 character command line limit. The SDK handles --agents by
    writing to a temp file when too long, but not other large arguments like
    --system-prompt or --mcp-config. This patch extends that handling.
    """
    if platform.system() != "Windows":
        return

    try:
        from claude_agent_sdk._internal.transport import subprocess_cli
    except ImportError:
        return  # SDK structure changed, skip patching

    if getattr(subprocess_cli, '_cmdline_patched', False):
        return

    original_build_command = subprocess_cli.SubprocessCLITransport._build_command

    def patched_build_command(self):
        """Patched _build_command that writes large arguments to temp files."""
        cmd = original_build_command(self)
        cmd_str = " ".join(cmd)
        cmd_limit = 8000

        if len(cmd_str) > cmd_limit:
            # Arguments that support @filepath syntax for file-based input
            large_args = ["--system-prompt", "--mcp-config", "--agents", "--settings"]

            for arg_name in large_args:
                try:
                    arg_idx = cmd.index(arg_name)
                    arg_value = cmd[arg_idx + 1]

                    if arg_value.startswith("@") or len(arg_value) < 500:
                        continue

                    suffix = ".json" if arg_value.startswith("{") else ".txt"
                    temp_file = tempfile.NamedTemporaryFile(
                        mode="w", suffix=suffix, delete=False, encoding="utf-8"
                    )
                    temp_file.write(arg_value)
                    temp_file.close()

                    if not hasattr(self, '_temp_files'):
                        self._temp_files = []
                    self._temp_files.append(temp_file.name)

                    cmd[arg_idx + 1] = f"@{temp_file.name}"

                except (ValueError, IndexError):
                    pass

        return cmd

    subprocess_cli.SubprocessCLITransport._build_command = patched_build_command
    subprocess_cli._cmdline_patched = True


# Apply the patch on module load
_patch_sdk_for_windows_cmdline_limit()


# System prompt for Qt understanding
QT_SYSTEM_PROMPT = ""


class QtClaudeAgent(QObject):
    """
    Low-level Claude Agent for Qt applications.

    This class provides programmatic access to Claude with Qt widget interaction
    capabilities. It manages the Claude Agent SDK client and provides signals for
    receiving responses.

    Signals:
        message_received(str): Emitted when Claude sends a text message
        thinking_received(str): Emitted when Claude's thinking is available
        tool_called(str, dict): Emitted when a tool is called (tool_name, tool_input)
        tool_result(str, dict): Emitted when a tool returns a result
        error_occurred(str): Emitted when an error occurs
        query_completed(): Emitted when a query finishes successfully
        result_received(dict): Emitted with usage/cost information
    """

    # Signals
    message_received = Signal(str)
    thinking_received = Signal(str)
    tool_called = Signal(str, dict)
    tool_result = Signal(str, dict)
    error_occurred = Signal(str)
    query_completed = Signal()
    query_cancelled = Signal()  # Emitted when a query is cancelled
    result_received = Signal(dict)
    permission_requested = Signal(str, str, dict)  # request_id, tool_name, tool_input

    def __init__(
        self,
        target_window: QWidget,
        api_key: str | None = None,
        api_url: str | None = None,
        cli_path: str | None = None,
        permission_mode: str = "default",
        max_turns: int = 20,
        additional_tools: list | None = None,
        additional_system_prompt: str | None = None,
        require_approval: bool = True,
        parent: QObject | None = None
    ):
        """
        Initialize the Qt Claude Agent.

        Args:
            target_window: The Qt widget to interact with
            api_key: Anthropic API key. Optional if you have authenticated via `claude login`
                    (Claude Pro/Max subscription). Can also be set via ANTHROPIC_API_KEY
                    or ANTHROPIC_AUTH_TOKEN environment variables.
            api_url: Not used - set ANTHROPIC_BASE_URL environment variable instead
                    (kept for backward compatibility)
            cli_path: Path to Claude Code CLI executable (auto-detected if not provided)
            permission_mode: Permission mode for tools ('default', 'acceptEdits', 'bypassPermissions')
            max_turns: Maximum conversation turns
            additional_tools: Optional list of additional MCP tool functions to register.
                             Each tool should be decorated with @tool from claude_agent_sdk.
            additional_system_prompt: Optional additional text to append to the system prompt.
            require_approval: If True, show UI approval for tool calls (default True).
                            Read-only tools (screenshot, get_widget_tree, find_widget) are
                            auto-approved. Interactive tools require user confirmation.
            parent: Parent QObject

        Note:
            Authentication can be provided in two ways:
            1. API Key: Pass api_key or set ANTHROPIC_API_KEY environment variable
            2. OAuth (subscription): Run `claude login` in terminal to authenticate with your
               Claude Pro/Max subscription. The CLI will use stored OAuth credentials.
        """
        super().__init__(parent)

        self.target_window = target_window

        # Try multiple environment variables for API key (optional - CLI can use OAuth)
        self.api_key = (
            api_key
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
        )

        # Try multiple environment variables for API URL
        self.api_url = (
            api_url
            or os.getenv("ANTHROPIC_BASE_URL")
            or os.getenv("ANTHROPIC_API_URL")
        )

        # API key is now optional - CLI can authenticate via OAuth from `claude login`

        # Setup permission manager for tool approval UI
        self._require_approval = require_approval
        self._permission_manager: PermissionManager | None = None

        if require_approval:
            self._permission_manager = PermissionManager(parent=self)
            # Forward permission requests to our signal
            self._permission_manager.permission_requested.connect(
                self.permission_requested.emit
            )

        # Create MCP tools server with Qt tools
        self.qt_tools = create_qt_tools_server(target_window)

        # Build allowed tools list - start with Qt tools
        allowed_tools = [
            "mcp__qt__screenshot",
            "mcp__qt__get_widget_tree",
            "mcp__qt__find_widget",
            "mcp__qt__click_widget",
            "mcp__qt__type_text",
            "mcp__qt__show_controller",
        ]

        # Store additional tools and create a combined server if needed
        self._additional_tools = additional_tools or []
        mcp_servers = {"qt": self.qt_tools}

        if self._additional_tools:
            # Create a separate MCP server for additional tools
            from claude_agent_sdk import create_sdk_mcp_server
            additional_server = create_sdk_mcp_server(
                name="additional",
                version="1.0.0",
                tools=self._additional_tools,
            )
            mcp_servers["additional"] = additional_server

            # Add allowed tools for additional tools
            for tool_func in self._additional_tools:
                if hasattr(tool_func, '__name__'):
                    tool_name = tool_func.__name__
                elif hasattr(tool_func, 'name'):
                    tool_name = tool_func.name
                else:
                    continue
                allowed_tools.append(f"mcp__additional__{tool_name}")

        # Build system prompt
        system_prompt = QT_SYSTEM_PROMPT
        if additional_system_prompt:
            system_prompt = f"{system_prompt}\n\n{additional_system_prompt}"

        # Configure Claude options
        options_dict = {
            "mcp_servers": mcp_servers,
            "allowed_tools": allowed_tools,
            "system_prompt": system_prompt,
            "permission_mode": permission_mode,
            "max_turns": max_turns,
        }

        # Add CLI path if provided
        if cli_path:
            options_dict["cli_path"] = cli_path

        # Set API key and URL directly in os.environ rather than passing via options.
        # This avoids Windows command line length limits (8191 chars) that occur when
        # the entire environment is serialized to command line arguments.
        if self.api_key:
            os.environ["ANTHROPIC_API_KEY"] = self.api_key
        if self.api_url:
            os.environ["ANTHROPIC_BASE_URL"] = self.api_url
        # Note: subprocess will inherit os.environ automatically, no need to pass env

        # Add permission callbacks if approval is required
        if require_approval and self._permission_manager:
            options_dict["can_use_tool"] = create_can_use_tool_callback(
                self._permission_manager
            )
            # Register PreToolUse hook — this is the primary permission
            # gate that intercepts ALL tool calls including MCP tools.
            try:
                from claude_agent_sdk import HookMatcher
                options_dict["hooks"] = {
                    "PreToolUse": [
                        HookMatcher(
                            matcher=None,  # match all tools
                            hooks=[create_pre_tool_use_hook(self._permission_manager)],
                        )
                    ],
                }
            except ImportError:
                logger.debug("HookMatcher not available, using can_use_tool only")

        self.options = ClaudeAgentOptions(**options_dict)

        # Create Claude SDK client
        self.client = ClaudeSDKClient(options=self.options)

        # Persistent worker reference
        self._worker: PersistentClaudeWorker | None = None
        self._is_connected = False

    def _ensure_connected(self) -> bool:
        """
        Ensure the persistent worker is connected.

        Returns:
            True if connected, False otherwise
        """
        if self._is_connected and self._worker and self._worker.isRunning():
            return True

        # Create and start persistent worker
        self._worker = PersistentClaudeWorker(
            self.client,
            permission_manager=self._permission_manager,
            parent=self,
        )

        # Connect signals
        self._worker.message_received.connect(self.message_received)
        self._worker.thinking_received.connect(self.thinking_received)
        self._worker.tool_called.connect(self.tool_called)
        self._worker.tool_result.connect(self.tool_result)
        self._worker.error_occurred.connect(self.error_occurred)
        self._worker.query_completed.connect(self.query_completed)
        self._worker.query_cancelled.connect(self.query_cancelled)
        self._worker.result_received.connect(self.result_received)

        # Track connection result
        result = {"success": False, "error": None}

        def on_connected():
            self._is_connected = True
            result["success"] = True

        def on_error(error):
            result["error"] = error
            result["success"] = False

        self._worker.connected.connect(on_connected)
        self._worker.error_occurred.connect(on_error)

        # Start worker (will connect in background)
        self._worker.start()

        # Wait for connection - process Qt events while waiting
        from PySide6.QtWidgets import QApplication
        import time
        timeout = 30  # seconds
        start_time = time.time()
        while not result["success"] and time.time() - start_time < timeout:
            QApplication.processEvents()  # Process Qt events including signals
            time.sleep(0.01)
            if result.get("success") or not self._worker.isRunning():
                break

        # If connection failed, emit the detailed error
        if not result["success"] and result.get("error"):
            self.error_occurred.emit(result["error"])

        return result["success"]

    def query_sync(self, prompt: str) -> None:
        """
        Send a query to Claude (non-blocking).

        This method sends a query to the persistent worker. The worker processes
        the query asynchronously and emits signals as responses are received.

        Args:
            prompt: The prompt/question to send to Claude
        """
        # Ensure connected
        if not self._ensure_connected():
            self.error_occurred.emit("Failed to connect to Claude")
            return

        # Send query to worker (non-blocking)
        self._worker.send_query(prompt)

    async def query(self, prompt: str) -> None:
        """
        Send a query to Claude (async version).

        This is an async wrapper that runs query_sync in a thread pool.
        Prefer query_sync for simplicity in Qt applications.

        Args:
            prompt: The prompt/question to send to Claude
        """
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.query_sync, prompt)

    def stop(self) -> None:
        """
        Stop the worker and disconnect.
        """
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            if not self._worker.wait(5000):  # 5s timeout
                logger.warning("Claude worker did not stop in time, terminating")
                self._worker.terminate()
                self._worker.wait(1000)
        self._is_connected = False

    def is_busy(self) -> bool:
        """
        Check if a query is currently running.

        Returns:
            True if a query is in progress, False otherwise
        """
        if self._worker and self._worker.isRunning():
            # Check both: items in queue OR actively processing
            return self._worker.is_processing or not self._worker._query_queue.empty()
        return False

    def cancel(self) -> bool:
        """
        Cancel the current query if one is running.

        This requests cancellation of the current query. The cancellation
        happens at the next check point during response processing. The
        query_cancelled signal will be emitted when cancellation completes.

        Returns:
            True if a cancellation was requested, False if no query was running.
        """
        if self._worker and self._worker.isRunning():
            return self._worker.cancel_current_query()
        return False

    # --- Permission API ---

    @property
    def permission_manager(self) -> PermissionManager | None:
        """
        Get the permission manager.

        Returns:
            PermissionManager if require_approval=True, else None
        """
        return self._permission_manager

    def respond_to_permission(
        self,
        request_id: str,
        allowed: bool,
        always: bool = False,
        message: str = ""
    ) -> None:
        """
        Respond to a permission request.

        Args:
            request_id: The request ID from permission_requested signal
            allowed: Whether to allow the tool use
            always: If True and allowed, auto-approve this tool in future
            message: Optional message (used as deny reason)
        """
        if self._permission_manager:
            self._permission_manager.respond(request_id, allowed, always, message)

    def reset_conversation(self) -> None:
        """Reset the conversation by stopping the worker.

        The next query will automatically reconnect with a fresh
        conversation via ``_ensure_connected()``.
        """
        self.stop()
        logger.info("Claude conversation reset")

    def add_always_allowed_tool(self, tool_name: str) -> None:
        """
        Add a tool to the always-allowed list.

        Args:
            tool_name: Full tool name (e.g., "mcp__qt__click_widget")
        """
        if self._permission_manager:
            self._permission_manager.add_always_allowed(tool_name)

    def __del__(self):
        """
        Cleanup when agent is destroyed.
        """
        self.stop()

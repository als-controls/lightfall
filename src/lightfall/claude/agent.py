"""QtClaudeAgent - Low-level API for Claude integration with Qt."""

import os
import platform
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import ClaudeSDKClient
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from lightfall.agents.backend import SessionConfig
from lightfall.agents.backends.claude_code.options import (
    SDK_MAX_BUFFER_SIZE,  # noqa: F401
    build_claude_options,
)
from lightfall.agents.bus_tools import BUS_ALLOWED_TOOLS, create_bus_tools_server
from lightfall.agents.skill_tools import SKILLS_ALLOWED_TOOLS, create_skill_tools_server
from lightfall.agents.tooling import ToolServer, tool_id
from lightfall.claude._internal.worker import PersistentClaudeWorker
from lightfall.claude._session_assembly import collect_tool_servers
from lightfall.claude.permission_manager import PermissionManager
from lightfall.claude.tools import create_qt_tools_server
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.agents.spec import AgentSpec
    from lightfall.agents.worker import AgentWorker


def lightfall_agent_cwd() -> str:
    """Stable working directory for the Claude agent subprocess.

    Pinned to ``~/lightfall`` (created if missing) so the SDK groups session
    transcripts under one deterministic project dir across launches, and so
    ``list_sessions(directory=...)`` matches the dir the CLI wrote to.
    """
    path = Path.home() / "lightfall"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _rewrite_oversized_args(cmd: list[str]) -> tuple[list[str], list[str]]:
    """Rewrite oversized CLI args to temp-file-backed forms, in place.

    Returns ``(cmd, temp_file_paths)``. ``cmd`` is mutated and also returned
    for convenience.

    Two distinct rewrite strategies, because the CLI treats these arg
    families differently:

    * ``--mcp-config`` / ``--settings`` already accept a bare file path as
      their value natively, so the temp file's path is substituted directly.
    * ``--system-prompt`` does NOT accept a bare path. The historical
      workaround was to rewrite it to ``@<tempfile>`` (the SDK's own
      at-file convention), but CLI >=2.1.221 silently ignores the
      ``@file`` convention for ``--system-prompt`` — the prompt is dropped
      entirely with no error (verified empirically 2026-08-03: every
      embedded session with a >8000-char command line ran with NO system
      prompt). The CLI does support a dedicated first-class flag,
      ``--system-prompt-file <path>``, which takes a plain path (no ``@``).
      So for ``--system-prompt`` we rewrite BOTH the flag name (to
      ``--system-prompt-file``) and the value (to a plain temp path).

    ``--agents`` is intentionally not handled here: SDK 0.2.93 no longer
    puts ``--agents`` on the command line at all (agents are sent via the
    initialize request instead), so this rewrite would never fire — see
    "No --agents CLI flag needed" in
    ``claude_agent_sdk/_internal/transport/subprocess_cli.py``.
    """
    temp_files: list[str] = []

    # Args that accept a bare file path as their value natively.
    file_path_args = {"--mcp-config", "--settings"}
    # Args that need both the flag name and value rewritten because the
    # original flag has no native file-path form.
    flag_rename_args = {"--system-prompt": "--system-prompt-file"}

    # Skip-guard: if the CLI-native flag is already present, don't touch it.
    already_rewritten = {new for new in flag_rename_args.values() if new in cmd}

    for arg_name in set(file_path_args) | set(flag_rename_args):
        if flag_rename_args.get(arg_name) in already_rewritten:
            continue
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
            temp_files.append(temp_file.name)

            cmd[arg_idx + 1] = temp_file.name
            if arg_name in flag_rename_args:
                cmd[arg_idx] = flag_rename_args[arg_name]

        except (ValueError, IndexError):
            pass

    return cmd, temp_files


def _patch_sdk_for_windows_cmdline_limit():
    """
    Monkey-patch the Claude Agent SDK to handle Windows command line length limits.

    Windows has an 8191 character command line limit. The SDK handles --agents by
    writing to a temp file when too long, but not other large arguments like
    --system-prompt or --mcp-config. This patch extends that handling.

    See ``_rewrite_oversized_args`` for why ``--system-prompt`` requires a
    flag-name rewrite (to ``--system-prompt-file``) rather than the ``@file``
    convention used elsewhere.
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
            cmd, temp_files = _rewrite_oversized_args(cmd)
            if temp_files:
                if not hasattr(self, '_temp_files'):
                    self._temp_files = []
                self._temp_files.extend(temp_files)

        return cmd

    subprocess_cli.SubprocessCLITransport._build_command = patched_build_command
    subprocess_cli._cmdline_patched = True


# Apply the patch on module load
_patch_sdk_for_windows_cmdline_limit()


# System prompt for Qt understanding
QT_SYSTEM_PROMPT = """You are a synchrotron beamline AI assistant integrated with a Qt/PySide6 application named
Lightfall

## Your Capabilities

You have domain-specific tools provided by the application - use these FIRST when they match the task. These tools understand the application's structure and can perform actions directly.

You also have general Qt inspection and interaction tools as a fallback:
- screenshot: Capture the window's current visual state
- get_widget_tree: View the widget hierarchy and structure
- find_widget: Locate widgets by object name
- click_widget: Click buttons and interactive widgets
- type_text: Enter text into input fields
- get_recent_logs: Read recent log records from the running Lightfall process. Use this when something unexpected happened outside your own tool calls (e.g., a panel didn't update as expected, a plan failed, a device went offline). Defaults to WARNING+ in the last two minutes; widen the filter (e.g., level="DEBUG", since_seconds=600) only when narrower scopes don't surface the issue.

## Tool Selection Guidelines

1. **Prefer domain-specific tools** - If a tool exists for your specific task (e.g., opening a panel, running a scan, controlling a device), use it directly instead of navigating the UI manually.

2. **Use Qt tools when needed for:**
   - Understanding unfamiliar parts of the UI
   - Interacting with widgets that lack domain-specific tools
   - Debugging or explaining the current UI state to the user
   - Situations where the user explicitly asks you to inspect the interface

3. **Avoid unnecessary exploration** - Don't take screenshots or inspect widget trees unless you need that information. If you know what tool to use, use it.

## Plan Execution Tools

You have tools for running Bluesky plans in the Lightfall RunEngine:

- **lightfall_list_plans**: List all registered plans (built-in + user plans). Use this FIRST to discover what's available and see parameter signatures. Optionally filter by category.
- **lightfall_run_plan**: Run a registered plan by name with parameters. Use this when the user wants to run a known plan (e.g., "run a scan", "do a count"). Resolves device names automatically.
- **lightfall_run_plan_code**: Run arbitrary Python code as a plan. Use this when the user needs a custom/ad-hoc plan that isn't in the registry, or wants to compose multiple plans together. The code should use `yield from` with bluesky plans. Common imports (bp, bps, np, all devices) are pre-loaded.
- **lightfall_create_user_plan**: Create a persistent user plan file (saved to ~/lightfall/plans/). Use this when the user wants to save a reusable plan for future use, not for one-off execution.

### When to use which:
- "Run a scan" → lightfall_list_plans to check params, then lightfall_run_plan
- "Scan motor1 from -5 to 5" → lightfall_run_plan(plan_name="scan", params={...})
- "Do 3 scans with increasing range" → lightfall_run_plan_code with a loop
- "Create a plan I can reuse" → lightfall_create_user_plan

### Waiting for a scan to finish

Use **lightfall_wait_for_idle** whenever you need the engine to settle before doing
follow-up work (fitting, summarising, kicking off a dependent plan). It blocks
the tool call until the engine returns to IDLE, then optionally returns the
most recent run's metadata in the same response (`include_last_run=True` by
default), so you can pipe straight into `lightfall_get_scan_data(uid=...)` without
another round-trip. Prefer this over polling `lightfall_get_run_status` in a loop,
and **do not use a scheduled wakeup (such as `ScheduleWakeup`) for this** — scheduled
wakeups do not fire in Lightfall's embedded session, so any wakeup you schedule will
silently drop and the conversation will stall waiting for the user to nudge
you. `lightfall_wait_for_idle` keeps the model suspended inside the tool call,
which is the right pattern here.

## Qt Tool Notes
- Widget object names (setObjectName) identify elements in the widget tree
- Verify widgets exist and are enabled before interacting
- Some widgets have auto-generated names like "<unnamed_QPushButton>"
"""


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
    context_usage = Signal(dict)  # context-window usage after each turn
    cockpit_reset = Signal()  # title-bar cockpit should zero (new/reset session)
    session_id_changed = Signal(str)  # current SDK session id
    permission_requested = Signal(str, str, dict)  # request_id, tool_name, tool_input
    permission_expired = Signal(str)  # request_id: ended unanswered, drop its card
    question_requested = Signal(str, list)  # request_id, questions
    # Partial streaming
    partial_block_started = Signal(str, str)
    partial_text = Signal(str, str)
    partial_thinking = Signal(str, str)
    partial_block_finished = Signal(str)
    # Task tool subagent lifecycle
    task_started = Signal(str, str, str)
    task_progress = Signal(str, str, dict, str)
    task_finished = Signal(str, str, str, str, dict)

    def __init__(
        self,
        target_window: QWidget,
        api_key: str | None = None,
        api_url: str | None = None,
        cli_path: str | None = None,
        permission_mode: str = "default",
        max_turns: int = 20,
        additional_system_prompt: str | None = None,
        require_approval: bool = True,
        model: str | None = None,
        effort: str | None = None,
        resume: str | None = None,
        disable_betas: bool = False,
        spec: "AgentSpec | None" = None,
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

        # Resolve the spec that drives this session's system prompt, cwd,
        # tool selection, and subagents. Fall back to the legacy hardcoded
        # QT_SYSTEM_PROMPT path (registry not loaded, e.g. bare constructor
        # tests) so existing callers keep working.
        if spec is None:
            from lightfall.agents.registry import AgentSpecRegistry
            spec = AgentSpecRegistry.get_instance().get("lightfall")
            if spec is None:
                logger.warning(
                    "QtClaudeAgent: no 'lightfall' AgentSpec in the registry; "
                    "falling back to the legacy hardcoded system prompt"
                )
        self._spec = spec

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

        # Setup permission manager. ALWAYS created — even with
        # ``permission_mode="bypassPermissions"`` we still need it for
        # ``AskUserQuestion`` routing (which is an interactive tool, not
        # a permission ask, and must always reach the user).
        self._require_approval = require_approval
        self._permission_manager = PermissionManager(
            parent=self, permission_mode=permission_mode
        )
        # Question requests must work regardless of approval mode — AskUserQuestion
        # is an interactive tool, not a permission gate.
        self._permission_manager.question_requested.connect(
            self.question_requested.emit
        )
        if require_approval:
            # Forward standard permission requests to our signal
            self._permission_manager.permission_requested.connect(
                self.permission_requested.emit
            )
            self._permission_manager.permission_expired.connect(
                self.permission_expired.emit
            )

        # Create MCP tools server with Qt tools
        tool_servers: list[tuple[str, ToolServer]] = [
            ("qt", create_qt_tools_server(target_window)),
        ]

        # Build allowed tools list - start with Qt tools
        allowed_tools = [tool_id("qt", n) for n in (
            "screenshot", "get_widget_tree", "find_widget", "click_widget",
            "type_text", "show_controller", "get_recent_logs",
        )]

        external_servers: dict[str, Any] = {}

        # Bus name defaults to the spec name (or "lightfall" on the legacy
        # path); the endpoint layer overwrites this with the actual
        # registered name once the session joins the shared agent bus. The
        # bus tools server reads it live via a closure, so it stays correct
        # even after being overwritten post-construction.
        self.bus_name = spec.name if spec else "lightfall"
        tool_servers.append(("bus", create_bus_tools_server(lambda: self.bus_name)))
        allowed_tools.extend(BUS_ALLOWED_TOOLS)

        # The skills server is likewise always-on: any session can propose a
        # skill draft, gated on human approval before it takes effect.
        tool_servers.append(("skills", create_skill_tools_server(
            lambda: self.bus_name, lambda: self._current_session_id
        )))
        allowed_tools.extend(SKILLS_ALLOWED_TOOLS)

        # Synthesize per-session SDK plugin dir
        from lightfall.claude._session_assembly import init_session_plugin_dir

        self._session_plugin_dir = Path(tempfile.mkdtemp(prefix="lightfall_claude_"))
        init_session_plugin_dir(self._session_plugin_dir)

        if self._spec is not None:
            from lightfall.agents.assembly import assemble_spec_options
            from lightfall.agents.registry import AgentSpecRegistry
            from lightfall.agents.spec import AgentSpecError
            from lightfall.ui.panels.claude.tool_registry import ToolRegistry

            try:
                from lightfall.agents.backends.info import backend_info

                spec_options = assemble_spec_options(
                    self._spec,
                    ToolRegistry.get_instance(),
                    AgentSpecRegistry.get_instance(),
                    self._session_plugin_dir,
                    backend=backend_info(),
                )
            except AgentSpecError as exc:
                logger.warning(
                    "agent '{}': failed to assemble spec options ({}); "
                    "falling back to the legacy hardcoded system prompt",
                    self._spec.name, exc,
                )
                self._spec = None

        if self._spec is not None:
            tool_servers += [(s.name, s) for s in spec_options["tool_servers"]]
            external_servers.update(spec_options["external_servers"])
            allowed_tools.extend(spec_options["allowed_tools"])
            system_prompt = spec_options["system_prompt"]
            project_cwd = spec_options["cwd"]
            subagents = spec_options["subagent_specs"]
        else:
            # Legacy path: no spec available (registry not loaded).
            from lightfall.agents.skills_store import materialize_skills
            from lightfall.ui.panels.claude.tool_registry import ToolRegistry

            enabled = ToolRegistry.get_instance().enabled_plugins()
            servers, external, allowed = collect_tool_servers(enabled)
            tool_servers += [(s.name, s) for s in servers]
            external_servers.update(external)
            allowed_tools.extend(allowed)
            subagents = []
            materialize_skills(tuple(p.name for p in enabled), self._session_plugin_dir)

            system_prompt = QT_SYSTEM_PROMPT
            project_cwd = lightfall_agent_cwd()

        if additional_system_prompt:
            system_prompt = f"{system_prompt}\n\n{additional_system_prompt}"

        self._model = model
        self._effort = effort
        self._resume_session_id = resume
        self._current_session_id: str | None = None
        self._project_cwd = project_cwd

        self._session_config = SessionConfig(
            cwd=self._project_cwd,
            system_prompt=system_prompt,
            tool_servers=tool_servers,
            external_servers=external_servers,
            allowed_tools=allowed_tools,
            subagents=subagents,
            skills_dir=self._session_plugin_dir,
            permission_mode=permission_mode,
            max_turns=max_turns,
            model=self._model,
            effort=self._effort,
            resume=self._resume_session_id,
            cli_path=cli_path,
        )

        # Persistent worker reference (set before backend construction, which
        # may raise, so __del__ -> stop() still finds it).
        self._worker: AgentWorker | None = None
        self._is_connected = False

        from lightfall.agents.backends.select import AGY, backend_kind

        self._agy = backend_kind() == AGY
        if self._agy:
            # agy backend: no Claude SDK client, options or ANTHROPIC_* env.
            self.options = None
            self.client = None
            self._backend = self._make_agy_backend()
        else:
            self._backend = None
            # Set API key and URL directly in os.environ rather than passing via options.
            # This avoids Windows command line length limits (8191 chars) that occur when
            # the entire environment is serialized to command line arguments.
            if self.api_key:
                os.environ["ANTHROPIC_API_KEY"] = self.api_key
            if self.api_url:
                os.environ["ANTHROPIC_BASE_URL"] = self.api_url
            if disable_betas:
                # Proxy gateways (Azure AI Foundry, cborg, ...) reject the CLI's
                # default beta headers -- e.g. Azure 400s on
                # `anthropic-beta: advisor-tool-2026-03-01`. This env var makes the
                # CLI drop the nonessential traffic that carries those headers, so
                # the request is accepted. Verified against the CMS Azure Foundry
                # backend (with a current model id).
                # Set-only: we never unset it here, so a manually-exported override
                # survives. (Cost: toggling the pref off in-app needs a restart to
                # re-enable betas -- rare, and worth not clobbering a manual export.)
                os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
            # Note: subprocess will inherit os.environ automatically, no need to pass env

            self.options = build_claude_options(
                self._session_config, self._permission_manager, require_approval
            )
            # Client construction stays here so tests can monkeypatch
            # lightfall.claude.agent.ClaudeSDKClient.
            self.client = ClaudeSDKClient(options=self.options)

        self.session_id_changed.connect(self._store_session_id)

    def _make_agy_backend(self):
        """Build an AgyCliBackend from the current ``_session_config`` and prefs.

        Raises:
            ValueError: the agy executable cannot be found.
        """
        from lightfall.agents.backends.agy import discover
        from lightfall.agents.backends.agy.backend import AgyCliBackend
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

        p = ClaudeSettingsProvider
        agy = discover.find_agy(p.get_agy_path())
        if agy is None:
            raise ValueError(
                "agy (Antigravity CLI) not found. Install it, put it on PATH, "
                "or set the agy_path preference."
            )
        return AgyCliBackend(
            self._session_config,
            self._permission_manager,
            self._require_approval,
            agy=agy,
            spec_name=self._spec.name if self._spec else None,
            project=p.get_agy_project(),
            location=p.get_agy_location(),
        )

    def _ensure_connected(self) -> bool:
        """
        Ensure the persistent worker is connected.

        Returns:
            True if connected, False otherwise
        """
        if self._is_connected and self._worker and self._worker.isRunning():
            return True

        # Create and start persistent worker
        if self._agy and self._backend is None:
            # A rebuild failed earlier (e.g. on reset): retry from the current
            # _session_config, never from a closed backend.
            try:
                self._backend = self._make_agy_backend()
            except ValueError as exc:
                self.error_occurred.emit(str(exc))
                return False
        if self._backend is not None:
            from lightfall.agents import worker as agent_worker

            self._worker = agent_worker.AgentWorker(
                self._backend,
                permission_manager=self._permission_manager,
                parent=self,
            )
        else:
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
        self._worker.context_usage.connect(self.context_usage)
        self._worker.session_id_changed.connect(self.session_id_changed)

        # Partial streaming forwards
        self._worker.partial_block_started.connect(self.partial_block_started)
        self._worker.partial_text.connect(self.partial_text)
        self._worker.partial_thinking.connect(self.partial_thinking)
        self._worker.partial_block_finished.connect(self.partial_block_finished)

        # Task tool subagent forwards
        self._worker.task_started.connect(self.task_started)
        self._worker.task_progress.connect(self.task_progress)
        self._worker.task_finished.connect(self.task_finished)

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
        import time

        from PySide6.QtWidgets import QApplication
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
            self.error_occurred.emit(
                "Failed to connect to the agent backend" if self._agy
                else "Failed to connect to Claude"
            )
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

    def stop(self, wait_ms: int = 5000) -> None:
        """
        Stop the worker and disconnect.

        Args:
            wait_ms: How long to join the worker thread. Pass 0 (app
                shutdown path) to signal the worker and abandon it without
                blocking — joining on the GUI thread during shutdown can
                exceed the 5 s exit watchdog, whose forced os._exit() then
                tears the process down mid-flight (0xC0000005).
        """
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            if wait_ms <= 0:
                logger.debug("Claude worker signalled to stop; not waiting (shutdown path)")
            elif not self._worker.wait(wait_ms):
                # Do NOT terminate(): killing a QThread that is executing
                # Python corrupts the interpreter heap and crashes the whole
                # process (0xC0000005). Abandon the worker instead — it is
                # reclaimed when it finally unblocks or the process exits.
                logger.warning(
                    "Claude worker did not stop within 5s; abandoning it "
                    "rather than force-terminating (terminate() would risk "
                    "corrupting the interpreter and crashing the process)."
                )
        self._is_connected = False

        # Clean up the per-session SDK plugin dir
        if hasattr(self, "_session_plugin_dir") and self._session_plugin_dir.exists():
            shutil.rmtree(self._session_plugin_dir, ignore_errors=True)

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

    def set_model(self, model: str | None) -> None:
        """Switch the model live (no reconnect). Applies on the next turn.

        Stores the choice so a later reconnect (e.g. an effort change) keeps
        the same model. If the worker is not connected yet, the stored value
        is picked up by ``_ensure_connected`` via the options.
        """
        self._model = model
        if self._worker is not None and self._worker.isRunning() and self._is_connected:
            self._worker.request_set_model(model)
        elif self._agy and not (self._worker is not None and self._worker.isRunning()):
            # No live worker: rebuild the (unconnected) agy backend with the new
            # model, keeping the conversation it would resume.
            import dataclasses

            resume = (self._backend.conversation_id if self._backend is not None
                      else self._session_config.resume)
            self._session_config = dataclasses.replace(
                self._session_config, model=model, resume=resume
            )
            try:
                self._backend = self._make_agy_backend()
            except ValueError as exc:
                self._backend = None  # _ensure_connected retries from _session_config
                logger.warning("agy model switch: {}", exc)

    def set_effort(self, effort: str | None) -> None:
        """Switch the reasoning effort live, for backends with ``caps.live_set_effort``
        (agy: the next turn respawns with ``--conversation``, history kept).

        Claude reads effort at construction only; its callers rebuild the agent.
        """
        self._effort = effort or None
        if self._worker is not None and self._worker.isRunning() and self._is_connected:
            self._worker.request_set_effort(self._effort)
        elif self._agy and not (self._worker is not None and self._worker.isRunning()):
            import dataclasses

            resume = (self._backend.conversation_id if self._backend is not None
                      else self._session_config.resume)
            self._session_config = dataclasses.replace(
                self._session_config, effort=self._effort, resume=resume
            )
            try:
                self._backend = self._make_agy_backend()
            except ValueError as exc:
                self._backend = None  # _ensure_connected retries from _session_config
                logger.warning("agy effort switch: {}", exc)

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

    def respond_to_question(
        self,
        request_id: str,
        answers: dict[str, str] | None,
    ) -> None:
        """Provide answers to a pending AskUserQuestion request.

        Pass ``answers=None`` to indicate the user cancelled the question;
        the agent will reply with a Deny so the model knows.
        """
        if self._permission_manager:
            self._permission_manager.respond_to_question(request_id, answers)

    def _is_main_session(self) -> bool:
        """True for the main assistant (the only session auto-restore follows)."""
        if self._spec is None:
            return True
        try:
            from lightfall.ui.panels.claude.agent_session_tab import MAIN_AGENT_NAME
        except Exception:  # noqa: BLE001
            MAIN_AGENT_NAME = "lightfall"
        return self._spec.name == MAIN_AGENT_NAME

    def _backend_kind_for_session(self) -> str:
        """The backend kind this agent was BUILT for (not the live pref)."""
        from lightfall.agents.backends.select import AGY, CLAUDE_CODE

        return AGY if getattr(self, "_agy", False) else CLAUDE_CODE

    def _store_session_id(self, session_id: str) -> None:
        """Remember + persist the current session id for restore/auto-restore."""
        self._current_session_id = session_id
        if not self._is_main_session():
            return  # auto-restore only follows the main assistant's id
        try:
            from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider
            ClaudeSettingsProvider.set_last_session_id(
                session_id, kind=self._backend_kind_for_session())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not persist last session id: {}", exc)

    def reset_conversation(self) -> None:
        """Reset the conversation: stop the worker AND start a brand-new session.

        Stopping the worker alone is not enough. ``_ensure_connected()`` rebuilds
        the worker around the SAME ``self.client``, and that client resumes the
        existing CLI conversation — so the chat would clear visually while the
        model still carried the old context. So here we also:

        * forget the in-memory + persisted session id (so nothing auto-resumes),
        * re-materialize the per-session plugin dir (``stop()`` deletes it), and
        * rebuild a fresh ``ClaudeSDKClient`` with ``resume`` /
          ``continue_conversation`` cleared,

        so the next query opens a genuinely new conversation.
        """
        import dataclasses

        from lightfall.agents.skills_store import materialize_skills
        from lightfall.claude._session_assembly import init_session_plugin_dir
        from lightfall.ui.panels.claude.tool_registry import ToolRegistry

        self.cockpit_reset.emit()
        self.stop()  # stops the worker; also rmtree's the session plugin dir

        # Forget the prior session so neither the rebuilt client nor auto-restore
        # resumes it.
        self._resume_session_id = None
        self._current_session_id = None
        try:
            if self._is_main_session():
                from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

                ClaudeSettingsProvider.set_last_session_id(
                    "", kind=self._backend_kind_for_session())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not clear last session id on reset: {}", exc)

        # Re-materialize the per-session plugin dir (stop() removed it) and
        # rebuild a fresh SDK client that does NOT resume the old conversation.
        # dataclasses.replace keeps every other option (tools, hooks, mcp
        # servers, model, effort, permission callbacks) intact.
        try:
            plugin_dir = Path(tempfile.mkdtemp(prefix="lightfall_claude_"))
            init_session_plugin_dir(plugin_dir)
            if self._spec is not None:
                materialize_skills(self._spec.skills, plugin_dir)
            else:
                enabled = ToolRegistry.get_instance().enabled_plugins()
                materialize_skills(tuple(p.name for p in enabled), plugin_dir)
            self._session_plugin_dir = plugin_dir

            if self._agy:
                # agy: the stopped worker already closed the old backend. Drop
                # it first so a failed rebuild can never resume the cleared
                # conversation; _ensure_connected retries the build.
                self._backend = None
                self._session_config = dataclasses.replace(
                    self._session_config, resume=None, skills_dir=plugin_dir,
                    model=self._model,
                )
                try:
                    self._backend = self._make_agy_backend()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("agy backend rebuild on reset failed: {}", exc)
                    self.error_occurred.emit(f"Could not start a new agy session: {exc}")
                    return
                logger.info("agy conversation reset (new session)")
                return

            self.options = dataclasses.replace(
                self.options,
                resume=None,
                continue_conversation=False,
                plugins=[{"type": "local", "path": str(plugin_dir)}],
            )
            self._session_config = dataclasses.replace(
                self._session_config, resume=None, skills_dir=plugin_dir
            )
            self.client = ClaudeSDKClient(options=self.options)
        except Exception:
            logger.exception("Failed to rebuild Claude client on reset")

        logger.info("Claude conversation reset (new session)")

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

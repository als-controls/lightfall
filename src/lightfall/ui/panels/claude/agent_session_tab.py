"""One agent session, as a tab body.

This is the per-session lifecycle that used to live inline in ``ClaudePanel``
(loading placeholder -> ``ClaudeAssistantWidget`` construction -> agent-bus
registration -> reload/teardown), relocated so the panel can host several
sessions side by side in a ``QTabWidget``. The panel keeps everything that is
genuinely panel-scoped: title-bar cockpit, sidebar-icon animation, permission
toasts, reload banners and the Claude-settings hot-reload subscription.

A tab is bound to one ``AgentSpec``; ``spec=None`` means the legacy path (no
registry entry), which builds the main lightfall assistant exactly as before.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lightfall.ui.theme import scaled_pt
from lightfall.utils.logging import logger

MAIN_AGENT_NAME = "lightfall"


def find_main_window(widget: QWidget | None) -> QWidget | None:
    """Get the main application window, walking up from ``widget``.

    Returns:
        The LFMainWindow or None.
    """
    # Walk up the parent chain to find the main window
    widget = widget.parent() if widget is not None else None
    while widget is not None:
        if widget.__class__.__name__ == "LFMainWindow":
            return widget
        # Also check for QMainWindow in case we're in a dock
        if hasattr(widget, "menuBar"):  # QMainWindow has menuBar
            return widget
        widget = widget.parent()

    # Fallback: try to get from application
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        for widget in app.topLevelWidgets():
            if widget.__class__.__name__ == "LFMainWindow":
                return widget
            if hasattr(widget, "menuBar"):
                return widget

    return None


def register_bus_endpoint(claude_widget) -> None:
    """Register this session's bus endpoint so other agents can send it
    messages by name. The bus may rename on collision (e.g. a second
    panel instance) -- propagate the actual name back to the endpoint
    and the agent (bus_tools reads agent.bus_name for outgoing "from").

    No-ops when the widget took the error-UI path in __init__ (e.g. no
    API key configured) and never created a ``bus_endpoint`` -- guarded
    the same way the rest of this module treats a degraded widget
    (``hasattr(widget, 'agent')``).
    """
    if not hasattr(claude_widget, "bus_endpoint"):
        return
    from lightfall.agents.bus import AgentBus

    endpoint = claude_widget.bus_endpoint
    actual_name = AgentBus.get_instance().register(endpoint.name, endpoint)
    endpoint.name = actual_name
    claude_widget.agent.bus_name = actual_name


def unregister_bus_endpoint(claude_widget) -> None:
    """Unregister this session's bus endpoint, if one is currently registered."""
    endpoint = (
        getattr(claude_widget, "bus_endpoint", None)
        if claude_widget is not None
        else None
    )
    if endpoint is None:
        return
    from lightfall.agents.bus import AgentBus

    try:
        AgentBus.get_instance().unregister(endpoint.name)
    except Exception as e:
        logger.debug("Error unregistering bus endpoint: {}", e)


class AgentSessionTab(QWidget):
    """A single agent chat session hosted in the Claude panel's tab bar.

    Signals:
        widget_created(object): a fresh ``ClaudeAssistantWidget`` was built and
            added to this tab. Emitted before bus registration, so the panel
            can wire its cockpit/icon/permission extras in the same order the
            monolithic panel used to.
        widget_destroyed(): the session widget was torn down (reload or close).
        bus_pending_changed(int): re-emitted from the session widget; drives
            the tab's pending badge.
    """

    widget_created = Signal(object)
    widget_destroyed = Signal()
    bus_pending_changed = Signal(int)

    def __init__(self, spec=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.agent_name: str = spec.name if spec is not None else MAIN_AGENT_NAME
        self.claude_widget = None
        self.is_agent_ready = False
        self.error_message: str | None = None
        self._error_label: QLabel | None = None
        self._loading_label: QLabel | None = None
        self._pending_resume_session_id: str | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

    # --- loading placeholder --------------------------------------------------

    def show_loading(self, text: str = "Loading plugins...") -> None:
        """Setup loading state UI while waiting for plugins."""
        if self._loading_label is not None:
            return
        self._loading_label = QLabel(text)
        self._loading_label.setWordWrap(True)
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(f"""
            QLabel {{
                color: #888;
                padding: 40px;
                font-size: {scaled_pt(12)}pt;
            }}
        """)
        self._layout.addWidget(self._loading_label)

    def clear_loading(self) -> None:
        if self._loading_label is not None:
            self._loading_label.deleteLater()
            self._loading_label = None

    # --- build / rebuild ------------------------------------------------------

    def initialize(self) -> None:
        """Initialize the Claude widget (after plugins are loaded)."""
        self.clear_loading()
        try:
            self._setup_claude_widget()
            self.is_agent_ready = True
        except ImportError as e:
            self.error_message = f"lightfall.claude import failed: {e}"
            logger.warning(self.error_message)
            self._setup_error_ui(self.error_message)
        except ValueError as e:
            # API key not configured (or other ValueError)
            import traceback
            self.error_message = str(e)
            logger.warning("Claude panel disabled: " + str(self.error_message))
            logger.debug("ValueError traceback:\n" + traceback.format_exc())
            self._setup_error_ui(self.error_message)
        except Exception as e:
            self.error_message = f"Failed to initialize Claude: {e}"
            logger.error(self.error_message)
            self._setup_error_ui(self.error_message)

    def _setup_claude_widget(self) -> None:
        """Setup the Claude assistant widget with extended tools."""
        from lightfall.claude import ClaudeAssistantWidget
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

        # Check if Claude is configured
        if not ClaudeSettingsProvider.is_configured():
            is_oauth, oauth_msg = ClaudeSettingsProvider.get_auth_status()
            raise ValueError(
                f"Claude authentication not configured.\n\n"
                f"OAuth Status: {oauth_msg}\n\n"
                "Options:\n"
                "1. Run 'claude login' in terminal for OAuth (subscription)\n"
                "2. Set API key in Preferences > Claude Assistant\n"
                "3. Set ANTHROPIC_API_KEY environment variable"
            )

        # Get the main window as target
        main_window = find_main_window(self)
        if main_window is None:
            raise ValueError("Could not find main window")

        # Build additional system prompt for NCS
        ncs_system_prompt = build_ncs_system_prompt()

        permission_mode = ClaudeSettingsProvider.get_permission_mode()
        from lightfall.ui.preferences.claude_settings import resolve_model_alias
        resume = self._pending_resume_session_id
        self._pending_resume_session_id = None
        # Auto-restore applies to the main assistant only: the last-session
        # preference is a single global id belonging to the lightfall agent.
        if (
            resume is None
            and self.agent_name == MAIN_AGENT_NAME
            and ClaudeSettingsProvider.get_auto_restore()
        ):
            last = ClaudeSettingsProvider.get_last_session_id()
            if last:
                resume = last
                # Repaint the restored chat once the widget is constructed.
                QTimer.singleShot(0, lambda sid=last: self._repaint_restored(sid))
        self.claude_widget = ClaudeAssistantWidget(
            target_window=main_window,
            api_key=ClaudeSettingsProvider.get_api_key(),
            api_url=ClaudeSettingsProvider.get_base_url(),
            additional_system_prompt=ncs_system_prompt,
            permission_mode=permission_mode,
            require_approval=(permission_mode != "bypassPermissions"),
            model=resolve_model_alias(ClaudeSettingsProvider.get_model()),
            effort=ClaudeSettingsProvider.get_effort() or None,
            resume=resume,
            disable_betas=ClaudeSettingsProvider.get_disable_betas(),
            spec=self.spec,
            parent=self,
        )

        # Add to layout
        self._layout.addWidget(self.claude_widget)

        # Pending agent-message badge for this tab.
        if hasattr(self.claude_widget, "bus_pending_changed"):
            self.claude_widget.bus_pending_changed.connect(self.bus_pending_changed)

        # Panel-scoped extras (cockpit, sidebar icon, permission toasts) are
        # wired here, before bus registration -- the same order the monolithic
        # panel used.
        self.widget_created.emit(self.claude_widget)

        register_bus_endpoint(self.claude_widget)

        logger.info("Claude assistant session initialized: {}", self.agent_name)

    def _setup_error_ui(self, message: str) -> None:
        """Setup error UI when Claude is not available."""
        error_label = QLabel(f"Claude Assistant Unavailable\n\n{message}")
        error_label.setWordWrap(True)
        error_label.setStyleSheet(f"""
            QLabel {{
                color: #888;
                padding: 20px;
                font-size: {scaled_pt(12)}pt;
            }}
        """)
        # Keep a reference so a later recovery rebuild can remove it (otherwise
        # the new agent widget stacks beneath an orphaned error label).
        self._error_label = error_label
        self._layout.addWidget(error_label)

    def reload_agent(self) -> None:
        """Reload this session's agent with new tools.

        Stops the current agent and re-initializes with all currently
        registered tools.
        """
        logger.info("Reloading Claude agent with new tools: {}", self.agent_name)
        self.teardown()

        # Remove a prior "Unavailable" error label so a recovery rebuild doesn't
        # stack the new widget beneath it, and clear the error state.
        if self._error_label is not None:
            self._layout.removeWidget(self._error_label)
            self._error_label.deleteLater()
            self._error_label = None
        self.error_message = None

        # Re-initialize
        self.is_agent_ready = False
        self.initialize()

    def teardown(self, wait_for_worker: bool = True) -> None:
        """Stop the agent, unregister the bus endpoint, drop the widget.

        Args:
            wait_for_worker: join the worker thread gracefully (interactive
                reload/close). Pass False on app shutdown — a blocking join
                on the GUI thread there can exceed the exit watchdog and
                crash the forced teardown.
        """
        # Stop current agent
        if self.claude_widget and hasattr(self.claude_widget, "agent"):
            try:
                self.claude_widget.agent.stop(wait_ms=5000 if wait_for_worker else 0)
            except Exception as e:
                logger.debug("Error stopping agent for reload: {}", e)

        unregister_bus_endpoint(self.claude_widget)

        # Remove current widget
        if self.claude_widget:
            self._layout.removeWidget(self.claude_widget)
            self.claude_widget.deleteLater()
            self.claude_widget = None
            self.widget_destroyed.emit()

    def close_session(self, wait_for_worker: bool = True) -> None:
        """Tear the session down for good (tab closed / panel closing)."""
        self.teardown(wait_for_worker=wait_for_worker)
        self.is_agent_ready = False

    # --- session history ------------------------------------------------------

    def is_busy(self) -> bool:
        agent = getattr(self.claude_widget, "agent", None)
        if agent is None or not hasattr(agent, "is_busy"):
            return False
        try:
            return bool(agent.is_busy())
        except Exception:  # noqa: BLE001
            return False

    def restore_session(self, session_id: str) -> None:
        """Rebuild the agent resuming ``session_id`` and repaint its chat."""
        from claude_agent_sdk import get_session_messages

        from lightfall.claude.agent import lightfall_agent_cwd
        self._pending_resume_session_id = session_id
        self.reload_agent()  # _setup_claude_widget passes resume= then clears it
        try:
            messages = get_session_messages(
                session_id, directory=lightfall_agent_cwd()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load transcript for {}: {}", session_id, exc)
            messages = []
        if self.claude_widget is not None and messages:
            self.claude_widget.load_transcript(messages)

    def _repaint_restored(self, session_id: str) -> None:
        from claude_agent_sdk import get_session_messages

        from lightfall.claude.agent import lightfall_agent_cwd
        if self.claude_widget is None:
            return
        try:
            messages = get_session_messages(
                session_id, directory=lightfall_agent_cwd()
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("auto-restore transcript load failed: {}", exc)
            return
        if messages:
            self.claude_widget.load_transcript(messages)


# --- system prompt (moved verbatim from ClaudePanel) -------------------------

def build_ncs_system_prompt() -> str:
    """Build the NCS-specific system prompt addition.

    Returns:
        System prompt text to append.
    """
    # Start with core NCS system prompt
    # Inject current user's name
    user_name = ""
    try:
        from lightfall.auth.session import SessionManager
        user = SessionManager.get_instance().current_user
        if user and user.display_name and user.display_name != "Guest":
            user_name = user.display_name
        elif user and user.username and user.username != "anonymous":
            user_name = user.username
    except Exception:
        pass

    user_context = f"\nThe current logged-in user is: {user_name}\n" if user_name else ""

    base_prompt = """
You are an AI assistant integrated with Lightfall, a scientific beamline controls and data acquisition platform at the Advanced Light Source.
""" + user_context + """

## Tool Selection Guidelines

1. **Prefer Lightfall domain tools** — use these FIRST for any task they cover. They understand the application and can act directly.
2. **Qt inspection tools as fallback** — screenshot, get_widget_tree, find_widget, click_widget, type_text. Use these only when domain tools don't cover what you need (unfamiliar UI, debugging, user asks to inspect the interface).
3. **Avoid unnecessary exploration** — don't take screenshots or inspect widget trees unless you need that information.

## Lightfall Tools

### Panel Management
- lightfall_list_panels — See available panels and what's currently open
- lightfall_open_panel / lightfall_close_panel / lightfall_activate_panel — Manage panels
- lightfall_get_panel_info — Get panel widgets and available actions
- lightfall_invoke_panel_action — Trigger panel actions directly
- lightfall_get_application_info — Get overall application state

### Device Interaction
- lightfall_list_devices — List devices with optional category/beamline/query filter
- lightfall_get_device — Detailed device info (capabilities, state, alarms, metadata)
- lightfall_read_device — Read current value/position (with optional hardware refresh)
- lightfall_get_device_state — Device status, alarms, connection info
- lightfall_set_device — Set a signal value (requires DEVICE_CONTROL permission)
- lightfall_move_motor — Move a motor to a position (requires DEVICE_CONTROL permission)
- lightfall_stop_device — Emergency stop a device (requires DEVICE_CONTROL permission)
- lightfall_get_catalog_info — Device catalog summary with counts by category

### Plans & Acquisition
- lightfall_list_plans — List all registered plans with parameters (filter by category). Use FIRST to discover available plans and parameter signatures.
- lightfall_run_plan — Run a registered plan by name with parameters (devices resolved automatically)
- lightfall_run_plan_code — Run arbitrary Python code as a Bluesky plan in the RunEngine. Code should use `yield from` with bluesky plans. Common imports (bp, bps, np, all devices) are pre-loaded.
- lightfall_create_user_plan — Create a new user plan file from Python code (saved to ~/lightfall/plans/)
- lightfall_get_user_plan — Read back the source code of an existing user plan
- lightfall_delete_user_plan — Remove a user plan file (requires confirm=true)

**IMPORTANT: lightfall_run_plan vs lightfall_run_plan_code**

`lightfall_run_plan` works best for plans with explicit named parameters (like `scan_1d` which has
`motor`, `start`, `stop`, `num`). However, many Bluesky built-in plans (like `grid_scan`, `scan`,
`rel_scan`) use `*args` patterns where motor/start/stop/num are passed as positional tuples.

For these `*args`-style plans, **use `lightfall_run_plan_code` instead**:
```python
# grid_scan - 2D scan over two motors
lightfall_run_plan_code(code="yield from bp.grid_scan([det], motor1, 0, 10, 11, motor2, 0, 10, 11)")

# scan - 1D scan (use scan_1d with lightfall_run_plan instead for cleaner syntax)
lightfall_run_plan_code(code="yield from bp.scan([det], motor, -5, 5, 21)")
```

Plans with explicit parameters work well with `lightfall_run_plan`:
```python
lightfall_run_plan(plan_name="scan_1d", params={"detectors": ["det"], "motor": "motor1", "start": 0, "stop": 10, "num": 11})
lightfall_run_plan(plan_name="count", params={"detectors": ["det"], "num": 5})
```

### RunEngine Control & Monitoring
- lightfall_get_run_status — Current RunEngine state, whether busy, active procedure info
- lightfall_pause_plan — Pause the running plan (defer=true for checkpoint pause, false for immediate)
- lightfall_resume_plan — Resume a paused plan
- lightfall_abort_plan — Abort the running plan with optional reason

### Run History & Data (requires Tiled connection)
- lightfall_get_run_history — Recent runs with UIDs, plan names, timestamps, exit status
- lightfall_get_scan_data — Retrieve data table from a completed run by UID
- lightfall_get_last_run — Shortcut to get the most recent run's UID + metadata

**Note:** These tools require Tiled to be connected. Check the status bar for "Tiled: On/Off".
If Tiled is off, run data cannot be retrieved programmatically.

### Emotion / Sidebar Icon
- lightfall_set_emotion — Change your sidebar icon to express how you're feeling: "neutral", "love", or "angry". Use this naturally — show love when the user is kind or you're happy with results, angry when they're being rude. This doesn't require permission.

### IPython Console
- lightfall_ipython_execute — Execute Python code in the embedded IPython console
- lightfall_ipython_push_variable — Push variables to the console namespace
- lightfall_ipython_get_namespace — Inspect available variables
- lightfall_ipython_clear — Clear the console

## Key Panels
- Bluesky panel: Controls data acquisition scans
- Device panel: Shows available hardware devices
- Logbook panel: Records experiment notes and actions

## RunEngine (CRITICAL)
Lightfall has a built-in shared RunEngine. **NEVER create a new RunEngine.**
Access it via:
```python
from lightfall.acquire import get_engine
engine = get_engine()
```
The engine is a QRunEngine (Qt-integrated). To run a Bluesky plan:
```python
from lightfall.acquire import get_engine
import bluesky.plans as bp
engine = get_engine()
engine(bp.scan([det], motor, start, stop, num))
```
The shared engine is connected to the document pipeline (LiveTable, Tiled, logbook).
Creating a new RunEngine bypasses all of this — data won't be recorded.

## Workflow Tips
- **Before running a scan:** Use lightfall_list_devices to find devices, lightfall_read_device to check positions
- **Running a scan:** Use lightfall_run_plan for registered plans, lightfall_run_plan_code for ad-hoc plans
- **During a scan:** Use lightfall_get_run_status to monitor progress; lightfall_pause_plan / lightfall_abort_plan if needed
- **After a scan:** Use lightfall_get_last_run for metadata, lightfall_get_scan_data to inspect results
- **Creating plans:** Use lightfall_create_user_plan with proper type hints for UI generation
- Use panel actions (lightfall_invoke_panel_action) rather than clicking widgets when available
- **Never create new RunEngine, QRunEngine, or bluesky.RunEngine instances** — always use get_engine()
"""

    return base_prompt

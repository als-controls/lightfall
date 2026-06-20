"""Claude Assistant Panel for Lightfall.

Provides an embedded Claude AI assistant with MCP tools for
interacting with the Lightfall application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from lightfall.claude.cockpit import CockpitState
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.theme import scaled_pt, scaled_px
from lightfall.ui.toast import ToastManager
from lightfall.utils.crash_diagnostics import gui_thread_only, safe_call
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    pass


class ReloadBannerWidget(QFrame):
    """Banner widget shown when new plugins are detected.

    Styled similar to the permission request widget from lightfall.claude.
    Shows a message about new plugins and a Reload button.
    """

    def __init__(
        self,
        plugin_names: list[str],
        on_reload: callable,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the reload banner.

        Args:
            plugin_names: Names of newly registered plugins.
            on_reload: Callback to invoke when Reload is clicked.
            parent: Parent widget.
        """
        super().__init__(parent)
        self._plugin_names = plugin_names
        self._on_reload = on_reload
        self._setup_ui()
        self._apply_theme_style()

    def _setup_ui(self) -> None:
        """Setup the banner UI."""
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # Icon and message
        count = len(self._plugin_names)
        if count == 1:
            message = f"\U0001F504 New tool plugin: <b>{self._plugin_names[0]}</b>"
        else:
            names = ", ".join(self._plugin_names[:3])
            if count > 3:
                names += f" (+{count - 3} more)"
            message = f"\U0001F504 {count} new tool plugins: <b>{names}</b>"

        self.info_label = QLabel(message)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.info_label, 1)

        # Reload button
        self.reload_btn = QPushButton("\u21BB Reload")
        self.reload_btn.setFixedHeight(24)
        self.reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reload_btn.setToolTip("Reload the agent with new tools")
        self.reload_btn.clicked.connect(self._handle_reload)
        layout.addWidget(self.reload_btn)

        # Dismiss button
        self.dismiss_btn = QPushButton("\u2715")
        self.dismiss_btn.setFixedSize(20, 20)
        self.dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss_btn.setToolTip("Dismiss")
        self.dismiss_btn.setStyleSheet("QPushButton { border: none; padding: 0; }")
        self.dismiss_btn.clicked.connect(self._handle_dismiss)
        layout.addWidget(self.dismiss_btn)

    def _apply_theme_style(self) -> None:
        """Apply theme-aware styling."""
        palette = self.palette()
        is_dark = palette.color(QPalette.ColorRole.Base).lightness() < 128

        if is_dark:
            bg = "rgba(90, 90, 60, 0.35)"
            border = "rgba(180, 180, 100, 0.5)"
        else:
            bg = "rgba(255, 250, 200, 0.6)"
            border = "rgba(200, 180, 80, 0.6)"

        self.setStyleSheet(f"""
            ReloadBannerWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QPushButton {{
                padding: 2px 8px;
                border-radius: 4px;
            }}
        """)

    def _handle_reload(self) -> None:
        """Handle reload button click."""
        self._on_reload()
        self.hide()
        self.deleteLater()

    def _handle_dismiss(self) -> None:
        """Handle dismiss button click."""
        self.hide()
        self.deleteLater()

    def add_plugin(self, plugin_name: str) -> None:
        """Add a plugin to the list and update the message.

        Args:
            plugin_name: Name of the newly registered plugin.
        """
        if plugin_name not in self._plugin_names:
            self._plugin_names.append(plugin_name)
            self._update_message()

    def _update_message(self) -> None:
        """Update the message label with current plugin count."""
        count = len(self._plugin_names)
        if count == 1:
            message = f"\U0001F504 New tool plugin: <b>{self._plugin_names[0]}</b>"
        else:
            names = ", ".join(self._plugin_names[:3])
            if count > 3:
                names += f" (+{count - 3} more)"
            message = f"\U0001F504 {count} new tool plugins: <b>{names}</b>"
        self.info_label.setText(message)


class ClaudePanel(BasePanel):
    """Claude AI Assistant panel.

    Embeds a Claude chat interface with MCP tools for:
    - Qt widget inspection and interaction (from lightfall.claude)
    - NCS panel management and introspection
    - Plugin-provided tools (Bluesky, devices, etc.)

    The panel requires an
    Anthropic API key to be configured.
    """

    panel_metadata = PanelMetadata(
        id="lightfall.panels.claude",
        name="Claude Assistant",
        description="AI assistant for interacting with the control system",
        icon="mdi6.robot",
        category="Tools",
        singleton=True,
        closable=True,
        keywords=["claude", "ai", "assistant", "llm", "chat", "help"],
        # Docking preferences - bottom sidebar (auto-hide icons on bottom edge)
        default_area="bottom",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=0,
        # claude_agent_sdk import chain is heavy (~300ms+); warm it in
        # the background while earlier panels initialize
        warmup_import="lightfall.claude",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Claude panel.

        Args:
            parent: Parent widget.
        """
        self._claude_widget = None
        self._agent = None
        self._error_message: str | None = None
        self._loading_label: QLabel | None = None
        self._reload_banner: ReloadBannerWidget | None = None
        self._pending_plugins: list[str] = []  # Plugins registered after setup
        self._is_agent_ready = False

        # Session restore state
        self._pending_resume_session_id: str | None = None

        # Title-bar cockpit (cost / context% / tokens)
        self._cockpit = CockpitState()
        self._cost_label: QLabel | None = None

        # Icon animation state
        self._thinking_timer: QTimer | None = None
        self._thinking_icon_toggle = False
        self._permission_timer: QTimer | None = None
        self._permission_icon_toggle = False
        self._idle_icon = "mdi6.robot"
        self._idle_color = ""

        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Setup the panel UI.

        If plugin loading is still in progress, shows a loading state and
        waits for completion before initializing the Claude widget.
        """
        # Subscribe to plugin registration signals for hot-reload
        self._subscribe_to_plugin_signals()

        # Check if plugin loading is complete
        if self._is_plugin_loading_complete():
            self._initialize_claude_widget()
        else:
            # Show loading state and wait for completion
            self._setup_loading_ui()
            self._subscribe_to_loading_complete()

    def _get_plugin_loader(self):
        """Get the plugin loader from services.

        Returns:
            PluginLoader instance or None if not available.
        """
        try:
            from lightfall.core.services import ServiceRegistry
            from lightfall.plugins import PluginLoader

            services = ServiceRegistry.get_instance()
            return services.get(PluginLoader)
        except Exception as e:
            logger.debug("Could not get plugin loader: {}", e)
            return None

    def _is_plugin_loading_complete(self) -> bool:
        """Check if plugin loading is complete.

        Returns:
            True if loading is complete or no loader is available.
        """
        loader = self._get_plugin_loader()
        if loader is None:
            return True  # No loader, assume complete
        return not loader.is_loading

    def _subscribe_to_loading_complete(self) -> None:
        """Subscribe to plugin loading completion signal."""
        loader = self._get_plugin_loader()
        if loader is not None:
            loader.loading_complete.connect(self._on_plugin_loading_complete)
            logger.debug("Subscribed to plugin loading_complete signal")

    def _subscribe_to_plugin_signals(self) -> None:
        """Subscribe to plugin signals for hot-reload (no-op; AgentRegistry has no signal)."""
        pass

    def _on_plugin_loading_complete(self, successful: int, failed: int) -> None:
        """Handle plugin loading completion.

        Args:
            successful: Number of successfully loaded plugins.
            failed: Number of failed plugins.
        """
        logger.info(
            "Plugin loading complete ({} successful, {} failed), initializing Claude",
            successful,
            failed,
        )

        # Remove loading UI
        if self._loading_label is not None:
            self._loading_label.deleteLater()
            self._loading_label = None

        # Initialize the Claude widget
        self._initialize_claude_widget()

    def _on_plugin_registered(self, plugin_name: str) -> None:
        """Handle new plugin registration after initial setup.

        Args:
            plugin_name: Name of the newly registered plugin.
        """
        if not self._is_agent_ready:
            # Agent not ready yet, will get plugin on initial setup
            return

        logger.info("New tool plugin registered: {}", plugin_name)

        # Add to pending list
        self._pending_plugins.append(plugin_name)

        # Show or update reload banner
        if self._reload_banner is None:
            self._reload_banner = ReloadBannerWidget(
                plugin_names=[plugin_name],
                on_reload=self._reload_agent,
                parent=self,
            )
            # Insert at top of layout
            self._layout.insertWidget(0, self._reload_banner)
        else:
            self._reload_banner.add_plugin(plugin_name)
            self._reload_banner.show()

    def _setup_loading_ui(self) -> None:
        """Setup loading state UI while waiting for plugins."""
        self._loading_label = QLabel("Loading plugins...")
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

    def _initialize_claude_widget(self) -> None:
        """Initialize the Claude widget (after plugins are loaded)."""
        try:
            self._setup_claude_widget()
            self._is_agent_ready = True
        except ImportError as e:
            self._error_message = f"lightfall.claude import failed: {e}"
            logger.warning(self._error_message)
            self._setup_error_ui(self._error_message)
        except ValueError as e:
            # API key not configured (or other ValueError)
            import traceback
            self._error_message = str(e)
            logger.warning("Claude panel disabled: " + str(self._error_message))
            logger.debug("ValueError traceback:\n" + traceback.format_exc())
            self._setup_error_ui(self._error_message)
        except Exception as e:
            self._error_message = f"Failed to initialize Claude: {e}"
            logger.error(self._error_message)
            self._setup_error_ui(self._error_message)

    def _reload_agent(self) -> None:
        """Reload the Claude agent with new tools.

        This stops the current agent and re-initializes with all
        currently registered tools.
        """
        logger.info("Reloading Claude agent with new tools")
        self._reset_cockpit()

        # Stop current agent
        if self._claude_widget and hasattr(self._claude_widget, 'agent'):
            try:
                self._claude_widget.agent.stop()
            except Exception as e:
                logger.debug("Error stopping agent for reload: {}", e)

        # Remove current widget
        if self._claude_widget:
            self._layout.removeWidget(self._claude_widget)
            self._claude_widget.deleteLater()
            self._claude_widget = None

        # Clear pending plugins list
        self._pending_plugins.clear()

        # Clear reload banner reference
        self._reload_banner = None

        # Re-initialize
        self._is_agent_ready = False
        self._initialize_claude_widget()

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
        main_window = self._get_main_window()
        if main_window is None:
            raise ValueError("Could not find main window")

        # Build additional system prompt for NCS
        ncs_system_prompt = self._build_ncs_system_prompt()

        permission_mode = ClaudeSettingsProvider.get_permission_mode()
        from lightfall.ui.preferences.claude_settings import resolve_model_alias
        resume = getattr(self, "_pending_resume_session_id", None)
        self._pending_resume_session_id = None
        if resume is None and ClaudeSettingsProvider.get_auto_restore():
            last = ClaudeSettingsProvider.get_last_session_id()
            if last:
                resume = last
                # Repaint the restored chat once the widget is constructed.
                QTimer.singleShot(0, lambda sid=last: self._repaint_restored(sid))
        self._claude_widget = ClaudeAssistantWidget(
            target_window=main_window,
            api_key=ClaudeSettingsProvider.get_api_key(),
            api_url=ClaudeSettingsProvider.get_base_url(),
            additional_system_prompt=ncs_system_prompt,
            permission_mode=permission_mode,
            require_approval=(permission_mode != "bypassPermissions"),
            model=resolve_model_alias(ClaudeSettingsProvider.get_model()),
            effort=ClaudeSettingsProvider.get_effort() or None,
            resume=resume,
            parent=self,
        )

        # Add to layout
        self._layout.addWidget(self._claude_widget)

        # Title-bar cockpit label (cost / context% / tokens)
        if self._cost_label is None:
            self._cost_label = QLabel(self._cockpit.format())
            self._cost_label.setObjectName("ClaudeCockpitLabel")
            self._cost_label.setStyleSheet("color: palette(mid); padding: 0 6px;")
            self._cost_label.setToolTip(self._cockpit.tooltip())
            self.add_title_bar_widget(self._cost_label)

        # Session history / restore (title-bar menu — per spec §4.3)
        from PySide6.QtWidgets import QMenu
        self._sessions_menu = QMenu()
        self._sessions_menu.aboutToShow.connect(self._populate_sessions_menu)
        self.add_title_bar_button("mdi6.history", "Restore a past session",
                                  menu=self._sessions_menu)

        # Connect permission signals to toast notifications and icon state
        self._claude_widget.approval_needed.connect(self._on_approval_needed)
        self._claude_widget.approval_needed.connect(
            lambda *_: self._icon_set_permission()
        )
        self._claude_widget.approval_resolved.connect(self._on_approval_resolved)
        self._claude_widget.approval_resolved.connect(
            lambda *_: self._icon_set_thinking()
        )

        # Connect icon state: query_started for immediate feedback
        self._claude_widget.query_started.connect(lambda: self._icon_set_thinking())

        self._claude_widget.model_change_requested.connect(self._on_pick_model)
        self._claude_widget.effort_change_requested.connect(self._on_pick_effort)

        # Connect agent signals to sidebar icon state
        self._connect_icon_signals()

        logger.info("Claude assistant panel initialized")

    def _populate_sessions_menu(self) -> None:
        from claude_agent_sdk import list_sessions

        from lightfall.claude.agent import lightfall_agent_cwd
        menu = self._sessions_menu
        menu.clear()
        try:
            infos = list_sessions(directory=lightfall_agent_cwd(), limit=15)
        except Exception as exc:  # noqa: BLE001
            logger.debug("list_sessions failed: {}", exc)
            infos = []
        if not infos:
            act = menu.addAction("No saved sessions")
            act.setEnabled(False)
            return
        for info in infos:
            title = (
                getattr(info, "custom_title", None)
                or getattr(info, "first_prompt", None)
                or getattr(info, "summary", None)
                or info.session_id[:8]
            )
            title = (title[:60] + "…") if len(title) > 60 else title
            act = menu.addAction(title)
            sid = info.session_id
            act.triggered.connect(
                lambda _checked=False, s=sid: self.restore_session(s)
            )

    def restore_session(self, session_id: str) -> None:
        """Rebuild the agent resuming ``session_id`` and repaint its chat."""
        from claude_agent_sdk import get_session_messages

        from lightfall.claude.agent import lightfall_agent_cwd
        self._pending_resume_session_id = session_id
        self._reload_agent()  # _setup_claude_widget passes resume= then clears it
        try:
            messages = get_session_messages(
                session_id, directory=lightfall_agent_cwd()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load transcript for {}: {}", session_id, exc)
            messages = []
        if self._claude_widget is not None and messages:
            self._claude_widget.load_transcript(messages)

    def _repaint_restored(self, session_id: str) -> None:
        from claude_agent_sdk import get_session_messages

        from lightfall.claude.agent import lightfall_agent_cwd
        if self._claude_widget is None:
            return
        try:
            messages = get_session_messages(
                session_id, directory=lightfall_agent_cwd()
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("auto-restore transcript load failed: {}", exc)
            return
        if messages:
            self._claude_widget.load_transcript(messages)

    def _build_ncs_system_prompt(self) -> str:
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

    def _on_pick_model(self, preset: str) -> None:
        from lightfall.ui.preferences.claude_settings import resolve_model_alias
        from lightfall.ui.preferences.manager import PreferencesManager
        PreferencesManager.get_instance().set("claude_model", preset)
        if self._claude_widget is not None and hasattr(self._claude_widget, "agent"):
            self._claude_widget.agent.set_model(resolve_model_alias(preset))

    def _on_pick_effort(self, level: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider
        from lightfall.ui.preferences.manager import PreferencesManager
        if level == ClaudeSettingsProvider.get_effort():
            return
        reply = QMessageBox.question(
            self, "Change effort",
            "Changing reasoning effort restarts the conversation "
            "(a fresh session). Continue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        PreferencesManager.get_instance().set("claude_effort", level)
        self._reload_agent()  # rebuilds widget+agent; effort read at construction

    def _setup_error_ui(self, message: str) -> None:
        """Setup error UI when Claude is not available.

        Args:
            message: Error message to display.
        """
        error_label = QLabel(f"Claude Assistant Unavailable\n\n{message}")
        error_label.setWordWrap(True)
        error_label.setStyleSheet(f"""
            QLabel {{
                color: #888;
                padding: 20px;
                font-size: {scaled_pt(12)}pt;
            }}
        """)
        self._layout.addWidget(error_label)

    def _on_approval_needed(
        self, request_id: str, tool_name: str, tool_input: dict
    ) -> None:
        """Handle permission request signal with an actionable toast.

        Args:
            request_id: Unique ID for this request.
            tool_name: Name of the tool requesting permission.
            tool_input: Input parameters for the tool.
        """
        # Extract a human-friendly tool name (remove mcp__ prefix if present)
        display_name = tool_name
        if display_name.startswith("mcp__"):
            display_name = display_name[5:]
        display_name = display_name.replace("_", " ").title()

        toast_mgr = ToastManager.get_instance()
        toast = toast_mgr.warning(
            "Permission Required",
            f"Claude wants to use: {display_name}",
            duration=30000,
        )

        # Add approve/deny buttons to the toast
        btn_container = QWidget(toast)
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(6)

        approve_btn = QPushButton("✓ Approve")
        approve_btn.setFixedHeight(22)
        approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        approve_btn.setStyleSheet(
            "QPushButton { background: #22c55e; color: white; border: none; "
            f"border-radius: 4px; padding: 2px 10px; font-weight: bold; font-size: {scaled_px(11)}px; }}"
            "QPushButton:hover { background: #16a34a; }"
        )

        deny_btn = QPushButton("✗ Deny")
        deny_btn.setFixedHeight(22)
        deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        deny_btn.setStyleSheet(
            "QPushButton { background: #ef4444; color: white; border: none; "
            f"border-radius: 4px; padding: 2px 10px; font-weight: bold; font-size: {scaled_px(11)}px; }}"
            "QPushButton:hover { background: #dc2626; }"
        )

        btn_layout.addStretch()
        btn_layout.addWidget(approve_btn)
        btn_layout.addWidget(deny_btn)
        btn_layout.addStretch()

        # Position buttons below the toast text
        toast_width = toast.width() if toast.width() > 0 else 300
        btn_container.setGeometry(10, toast.height() - 34, toast_width - 20, 30)
        btn_container.show()

        def on_approve():
            if self._claude_widget and request_id in self._claude_widget._pending_permission_widgets:
                widget = self._claude_widget._pending_permission_widgets[request_id]
                widget.allowed.emit(request_id, False)
            toast.hide()

        def on_deny():
            if self._claude_widget and request_id in self._claude_widget._pending_permission_widgets:
                widget = self._claude_widget._pending_permission_widgets[request_id]
                widget.denied.emit(request_id, "Denied via toast")
            toast.hide()

        approve_btn.clicked.connect(on_approve)
        deny_btn.clicked.connect(on_deny)

        logger.debug("Permission requested for tool: {}", tool_name)

    def _on_approval_resolved(self, request_id: str, was_allowed: bool) -> None:
        """Handle permission resolution.

        Args:
            request_id: Unique ID for this request.
            was_allowed: Whether the permission was granted.
        """
        # Don't show a second toast — the approval toast is already dismissed
        # and showing another immediately can cause C++ object lifecycle issues
        logger.debug(
            "Permission resolved: request_id={}, allowed={}", request_id, was_allowed
        )

    def _get_main_window(self) -> QWidget | None:
        """Get the main application window.

        Returns:
            The LFMainWindow or None.
        """
        # Walk up the parent chain to find the main window
        widget = self.parent()
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

    # ─────────────────────────────────────────────────────────────────────────
    # Sidebar icon state management
    # ─────────────────────────────────────────────────────────────────────────

    def _connect_icon_signals(self) -> None:
        """Connect agent signals to icon state changes."""
        if self._claude_widget is None or not hasattr(self._claude_widget, 'agent'):
            logger.warning("Cannot connect icon signals: widget={}, has_agent={}",
                           self._claude_widget is not None,
                           hasattr(self._claude_widget, 'agent') if self._claude_widget else False)
            return

        agent = self._claude_widget.agent
        agent.message_received.connect(self._icon_set_thinking)
        agent.thinking_received.connect(self._icon_set_thinking)
        agent.tool_called.connect(lambda *_: self._icon_set_thinking())
        agent.query_completed.connect(self._icon_set_idle)
        agent.query_cancelled.connect(self._icon_set_idle)
        agent.error_occurred.connect(self._icon_set_error)
        agent.result_received.connect(self._on_cockpit_result)
        agent.context_usage.connect(self._on_cockpit_context)
        agent.cockpit_reset.connect(self._reset_cockpit)
        logger.info("Connected Claude agent icon signals")

    @gui_thread_only
    def _on_cockpit_result(self, info: dict) -> None:
        self._cockpit.add_result(info)
        self._refresh_cockpit_label()

    @gui_thread_only
    def _on_cockpit_context(self, info: dict) -> None:
        self._cockpit.set_context(info)
        self._refresh_cockpit_label()

    @gui_thread_only
    def _reset_cockpit(self) -> None:
        self._cockpit.reset()
        self._refresh_cockpit_label()

    def _refresh_cockpit_label(self) -> None:
        if self._cost_label is not None:
            safe_call(self._cost_label, "setText", self._cockpit.format())
            safe_call(self._cost_label, "setToolTip", self._cockpit.tooltip())

    def _icon_set_idle(self) -> None:
        """Set sidebar icon to idle state (respects emotion override)."""
        self._stop_thinking_animation()
        self._stop_permission_animation()
        self.set_sidebar_icon(icon_name=self._idle_icon, color=self._idle_color)

    def _icon_set_thinking(self, _thinking: str = "") -> None:
        """Set sidebar icon to thinking state with animation."""
        self._stop_permission_animation()
        if self._thinking_timer is not None:
            return  # Already animating

        self._thinking_icon_toggle = False
        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._thinking_animation_tick)
        self._thinking_timer.start(1000)
        # Set initial icon immediately
        self._thinking_animation_tick()

    def _thinking_animation_tick(self) -> None:
        """Alternate between happy and excited robot icons."""
        if self._thinking_icon_toggle:
            self.set_sidebar_icon(icon_name="mdi6.robot-happy", color="#60a5fa")
        else:
            self.set_sidebar_icon(icon_name="mdi6.robot-excited", color="#a78bfa")
        self._thinking_icon_toggle = not self._thinking_icon_toggle

    def _stop_thinking_animation(self) -> None:
        """Stop the thinking animation timer."""
        if self._thinking_timer is not None:
            self._thinking_timer.stop()
            self._thinking_timer.deleteLater()
            self._thinking_timer = None

    def _icon_set_permission(self) -> None:
        """Set sidebar icon to permission-waiting state (flashing confused)."""
        self._stop_thinking_animation()
        self._permission_icon_toggle = False
        if self._permission_timer is None:
            self._permission_timer = QTimer(self)
            self._permission_timer.timeout.connect(self._permission_animation_tick)
            self._permission_timer.start(500)
        self._permission_animation_tick()

    def _permission_animation_tick(self) -> None:
        """Flash between yellow and brown for permission waiting."""
        if self._permission_icon_toggle:
            self.set_sidebar_icon(icon_name="mdi6.robot-confused", color="#f59e0b")
        else:
            self.set_sidebar_icon(icon_name="mdi6.robot-confused", color="#92400e")
        self._permission_icon_toggle = not self._permission_icon_toggle

    def _stop_permission_animation(self) -> None:
        """Stop the permission animation timer."""
        if self._permission_timer is not None:
            self._permission_timer.stop()
            self._permission_timer.deleteLater()
            self._permission_timer = None

    def _icon_set_error(self, _error: str = "") -> None:
        """Set sidebar icon to error/disconnected state."""
        self._stop_thinking_animation()
        self._stop_permission_animation()
        self.set_sidebar_icon(icon_name="mdi6.robot-dead", color="#ef4444")

    def _on_closing(self) -> None:
        """Cleanup when panel is closing."""
        self._stop_thinking_animation()
        self._stop_permission_animation()
        # AgentRegistry has no signals to disconnect

        # Disconnect from loader signals
        loader = self._get_plugin_loader()
        if loader is not None:
            try:
                loader.loading_complete.disconnect(self._on_plugin_loading_complete)
            except Exception:
                pass  # Ignore if not connected

        # Stop the agent
        if self._claude_widget and hasattr(self._claude_widget, 'agent'):
            try:
                self._claude_widget.agent.stop()
            except Exception as e:
                logger.debug("Error stopping Claude agent: {}", e)
        super()._on_closing()

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel.

        Returns:
            List of action descriptions.
        """
        actions = super()._get_available_actions()

        # Add panel-specific actions
        if self._claude_widget is not None:
            actions.extend([
                {
                    "name": "send_message",
                    "description": "Send a message to Claude",
                    "method": "action_send_message",
                    "parameters": {"message": "string"},
                },
                {
                    "name": "clear_chat",
                    "description": "Clear the chat history display",
                    "method": "action_clear_chat",
                },
            ])

        return actions

    def action_send_message(self, message: str) -> bool:
        """Send a message to Claude.

        Args:
            message: The message to send.

        Returns:
            True if message was sent.
        """
        if self._claude_widget is None:
            return False

        # Set the input field text and trigger send
        if hasattr(self._claude_widget, 'input_field'):
            self._claude_widget.input_field.setText(message)
            self._claude_widget._send_query()
            return True

        return False

    def action_clear_chat(self) -> bool:
        """Clear the chat display.

        Returns:
            True if cleared.
        """
        if self._claude_widget is None:
            return False

        if hasattr(self._claude_widget, 'chat_display'):
            self._claude_widget.chat_display.clear()
            return True

        return False

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data.

        Returns:
            Dictionary with Claude panel state.
        """
        data = {
            "claude_available": self._claude_widget is not None,
            "agent_ready": self._is_agent_ready,
            "pending_plugins": list(self._pending_plugins),
            "error": self._error_message,
        }

        if self._claude_widget is not None and hasattr(self._claude_widget, 'agent'):
            agent = self._claude_widget.agent
            data["agent_busy"] = agent.is_busy() if hasattr(agent, 'is_busy') else None

        return data

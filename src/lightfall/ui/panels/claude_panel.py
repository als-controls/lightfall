"""Claude Assistant Panel for Lightfall.

Provides an embedded Claude AI assistant with MCP tools for
interacting with the Lightfall application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QWidget,
)

from lightfall.agents.backends import info as backend_infos
from lightfall.claude.cockpit import CockpitState
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.claude.agent_session_tab import (
    MAIN_AGENT_NAME,
    AgentSessionTab,
    build_ncs_system_prompt,
    find_main_window,
    register_bus_endpoint,
    unregister_bus_endpoint,
)
from lightfall.ui.theme import scaled_px
from lightfall.ui.toast import ToastManager
from lightfall.utils.crash_diagnostics import gui_thread_only, safe_call
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    pass


def _voice_queue_pending(widget) -> bool:
    """True if ``widget`` has queued voice prompts that may still drain
    (non-empty, not paused after an error, not held across a rebuild)."""
    return _voice_queue_len(widget) > 0


def _voice_queue_len(widget) -> int:
    """Length of ``widget``'s voice queue if it can drain, else 0."""
    queue = getattr(widget, "voice_queue", None) if widget is not None else None
    if queue is None:
        return 0
    try:
        if queue.paused or getattr(queue, "held", False):
            return 0
        return len(queue)
    except TypeError:  # not a real queue (e.g. a test double)
        return 0


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
        message: str | None = None,
    ) -> None:
        """Initialize the reload banner.

        Args:
            plugin_names: Names of newly registered plugins.
            on_reload: Callback to invoke when Reload is clicked.
            parent: Parent widget.
            message: Optional fixed message (rich text) shown verbatim instead
                of the plugin-name summary -- used for non-plugin reload
                prompts such as a settings change.
        """
        super().__init__(parent)
        self._plugin_names = plugin_names
        self._on_reload = on_reload
        self._fixed_message = message
        self._setup_ui()
        self._apply_theme_style()

    def _compose_message(self) -> str:
        """Build the banner text from the fixed message or the plugin list."""
        if self._fixed_message is not None:
            return self._fixed_message
        count = len(self._plugin_names)
        if count == 1:
            return f"\U0001F504 New tool plugin: <b>{self._plugin_names[0]}</b>"
        names = ", ".join(self._plugin_names[:3])
        if count > 3:
            names += f" (+{count - 3} more)"
        return f"\U0001F504 {count} new tool plugins: <b>{names}</b>"

    def _setup_ui(self) -> None:
        """Setup the banner UI."""
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # Icon and message
        self.info_label = QLabel(self._compose_message())
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
        self.info_label.setText(self._compose_message())


class ClaudePanel(BasePanel):
    """Claude AI Assistant panel.

    Embeds a Claude chat interface with MCP tools for:
    - Qt widget inspection and interaction (from lightfall.claude)
    - NCS panel management and introspection
    - Plugin-provided tools (Bluesky, devices, etc.)

    The panel requires an
    Anthropic API key to be configured.

    Signals:
        current_session_changed(): the visible session tab changed; read it
            back with ``current_assistant_widget()``.
    """

    current_session_changed = Signal()

    panel_metadata = PanelMetadata(
        id="lightfall.panels.claude",
        name="Assistant",
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

    # Preference keys that determine which backend/model/behavior the agent
    # connects with (including the backend choice and its agy settings). A
    # change to any of these (e.g. from the Preferences dialog) must rebuild
    # the running agent instead of waiting for a lightfall restart.
    # After a reply ends, is_busy() can stay True briefly (the worker finishes
    # post-turn work such as the context-usage fetch). A deferred reload polls
    # for idleness at this interval, for as long as the reload stays pending (a
    # new turn just keeps it waiting -- a reply is never torn down).
    _DEFERRED_POLL_MS = 200
    # Idle polls without the voice queue shrinking before a deferred reload
    # stops waiting for it (it cannot drain, e.g. a permission card is up).
    _VOICE_STALL_POLLS = 3

    _WATCHED_CLAUDE_KEYS = (
        "claude_model",
        "claude_effort",
        "claude_endpoint",
        "claude_custom_url",
        "claude_api_key",
        "claude_max_turns",
        "claude_permission_mode",
        "claude_disable_betas",
        "agent_backend",
        "agy_path",
        "agy_gcp_project",
        "agy_gcp_location",
        "agy_effort",
        "agy_model",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Claude panel.

        Args:
            parent: Parent widget.
        """
        self._claude_widget = None
        self._agent = None
        self._error_message: str | None = None
        self._reload_banner: ReloadBannerWidget | None = None
        self._pending_plugins: list[str] = []  # Plugins registered after setup
        self._is_agent_ready = False

        # Claude-settings hot-reload state.
        #   _settings_reload_pending: a debounced reload is already scheduled,
        #     so a burst of pref writes (one Preferences-dialog OK calls
        #     save_settings() on every plugin, touching several keys) coalesces
        #     into a single rebuild.
        #   _active_agent_config: the resolved settings the live agent was built
        #     with. A pref change only rebuilds when the *effective* config
        #     differs from this -- so an unrelated Preferences OK (which
        #     re-writes Claude prefs to identical values) does not churn the
        #     agent, and the in-panel picker's live switch (which updates this)
        #     isn't immediately undone.
        self._settings_reload_pending = False
        self._active_agent_config: tuple | None = None
        # Tabs (main or "+") whose reload waits for their in-flight reply to end.
        self._deferred_reload: set = set()
        self._deferred_polling: set = set()  # tabs with a poll chain running
        # tab -> (voice queue length last seen, idle polls without it shrinking)
        self._voice_drain_watch: dict = {}

        # Session restore state
        self._pending_resume_session_id: str | None = None

        # Title-bar cockpit (cost / context% / tokens)
        self._cockpit = CockpitState()
        self._cost_label: QLabel | None = None
        self._sessions_menu = None

        # Title-bar toggle for thinking / tool-usage fragments (default hidden)
        self._verbose_action = None
        self._verbose_visible = False

        # Icon animation state
        self._thinking_timer: QTimer | None = None
        self._thinking_icon_toggle = False
        self._permission_timer: QTimer | None = None
        self._permission_icon_toggle = False
        self._idle_icon = "mdi6.robot"
        self._idle_color = ""

        # Tabbed multi-agent surface. ``_lightfall_tab`` is the uncloseable
        # main assistant; ``_tab_pending`` tracks per-tab unread agent-message
        # counts so the badge survives tab switches.
        self._tabs: QTabWidget | None = None
        self._lightfall_tab: AgentSessionTab | None = None
        self._tab_pending: dict[AgentSessionTab, int] = {}  # tab -> pending count

        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Setup the panel UI.

        If plugin loading is still in progress, shows a loading state and
        waits for completion before initializing the Claude widget.
        """
        # Subscribe to plugin registration signals for hot-reload
        self._subscribe_to_plugin_signals()

        # Subscribe to Claude settings so a Preferences change rebuilds the
        # agent without a lightfall restart.
        self._subscribe_to_claude_settings()

        # Tab surface + the always-present lightfall session
        self._setup_tabs()

        # Check if plugin loading is complete
        if self._is_plugin_loading_complete():
            self._initialize_claude_widget()
        else:
            # Show loading state and wait for completion
            self._setup_loading_ui()
            self._subscribe_to_loading_complete()

    # ─────────────────────────────────────────────────────────────────────────
    # Tabs
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_tabs(self) -> None:
        """Build the tab widget and the uncloseable lightfall session tab."""
        self._tabs = QTabWidget(self)
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tabs.currentChanged.connect(self._on_current_tab_changed)

        add_btn = QToolButton(self._tabs)
        add_btn.setText("+")
        add_btn.setToolTip("Open another agent in a new tab")
        add_btn.setAutoRaise(True)
        add_btn.clicked.connect(self._show_agent_picker)
        self._add_button = add_btn
        self._tabs.setCornerWidget(add_btn, Qt.Corner.TopRightCorner)

        self._layout.addWidget(self._tabs)

        # Register the bus-launch hook: mcp__bus__launch_agent (bus_tools.py)
        # calls AgentBus.launch(), which delegates here now that a tab surface
        # exists. Cleared in _on_closing.
        from lightfall.agents.bus import AgentBus

        AgentBus.get_instance().set_launcher(self._launch_agent_for)

        # The main assistant. Its spec comes from the registry; if the registry
        # has no "lightfall" entry (empty/unloaded), the tab falls back to the
        # legacy no-spec path, which is exactly today's behavior.
        spec = None
        try:
            from lightfall.agents.registry import AgentSpecRegistry

            spec = AgentSpecRegistry.get_instance().get(MAIN_AGENT_NAME)
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not resolve '{}' agent spec: {}", MAIN_AGENT_NAME, e)

        self._lightfall_tab = self._add_tab(AgentSessionTab(spec, parent=self._tabs))
        self._make_tab_uncloseable(self._tabs.indexOf(self._lightfall_tab))

    def _add_tab(self, tab: AgentSessionTab) -> AgentSessionTab:
        """Insert a session tab and wire its signals."""
        index = self._tabs.addTab(tab, tab.agent_name)
        self._tabs.setTabToolTip(
            index, tab.spec.description if tab.spec is not None else "Lightfall assistant"
        )
        tab.bus_pending_changed.connect(
            lambda count, t=tab: self._on_tab_pending_changed(t, count)
        )
        # New session widgets adopt the panel's current thinking/tool-usage
        # visibility (the widget itself defaults to hidden).
        tab.widget_created.connect(self._apply_verbose_state)
        # Every session's model/effort menu must reach the panel handlers --
        # not just the main tab's -- or picks on secondary tabs do nothing.
        tab.widget_created.connect(self._wire_session_controls)
        if tab is self._lightfall_tab or self._lightfall_tab is None:
            # Panel-scoped extras (cockpit, sidebar icon, permission toasts)
            # track the main assistant only.
            tab.widget_created.connect(self._on_main_widget_created)
            tab.widget_destroyed.connect(self._on_main_widget_destroyed)
        return tab

    def _make_tab_uncloseable(self, index: int) -> None:
        """Strip the close button from a tab (both button sides: the side used
        depends on the platform style)."""
        bar = self._tabs.tabBar()
        for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
            button = bar.tabButton(index, side)
            if button is not None:
                button.deleteLater()
            bar.setTabButton(index, side, None)

    def _open_agent_tab(self, spec, launched_by: str | None = None) -> AgentSessionTab:
        """Open (or focus) a tab running ``spec``.

        ``launched_by`` seeds an auto-accept whitelist on a freshly created
        tab's session (see ``AgentSessionTab`` / ``ClaudeSessionEndpoint``); it
        has no effect when focusing an already-running tab.
        """
        existing = self._find_tab(spec.name)
        if existing is not None:
            self._tabs.setCurrentWidget(existing)
            return existing
        tab = self._add_tab(AgentSessionTab(spec, launched_by=launched_by, parent=self._tabs))
        self._tabs.setCurrentWidget(tab)
        if self._is_plugin_loading_complete():
            tab.initialize()
        else:
            tab.show_loading()
        return tab

    def _show_agent_picker(self) -> None:
        from lightfall.agents.registry import AgentSpecRegistry
        from lightfall.ui.panels.claude.agent_picker import build_picker_menu

        menu = build_picker_menu(
            AgentSpecRegistry.get_instance(),
            self._open_tab_names(),
            self._open_agent_tab,
            parent=self,
        )
        menu.exec(self._add_button.mapToGlobal(self._add_button.rect().bottomLeft()))

    def _session_tabs(self) -> list[AgentSessionTab]:
        if self._tabs is None:
            return []
        return [self._tabs.widget(i) for i in range(self._tabs.count())]

    def current_assistant_widget(self):
        """The visible tab's ``ClaudeAssistantWidget``, or None (no tab, or the
        session has no live widget)."""
        if self._tabs is None:
            return None
        tab = self._tabs.currentWidget()
        return getattr(tab, "claude_widget", None) if tab is not None else None

    def assistant_widgets(self) -> list:
        """Every open session's live ``ClaudeAssistantWidget``, in tab order."""
        widgets = (getattr(t, "claude_widget", None) for t in self._session_tabs())
        return [w for w in widgets if w is not None]

    def _open_tab_names(self) -> set[str]:
        return {t.agent_name for t in self._session_tabs()}

    def _find_tab(self, agent_name: str) -> AgentSessionTab | None:
        for tab in self._session_tabs():
            if tab.agent_name == agent_name:
                return tab
        return None

    def _on_tab_close_requested(self, index: int) -> None:
        tab = self._tabs.widget(index)
        # Defense in depth: the lightfall tab has no close button, but never
        # honor a close request for it. Guard on identity, not position --
        # tabs are movable, so index 0 may hold any session.
        if tab is self._lightfall_tab:
            return
        queued = tab.queued_voice_count()
        if queued:
            from PySide6.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self,
                "Close session",
                f"Discard {queued} queued voice messages?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            tab.discard_voice_queue("tab closed")
        self._voice_drain_watch.pop(tab, None)
        self._deferred_reload.discard(tab)
        self._deferred_polling.discard(tab)
        tab.close_session()
        self._tab_pending.pop(tab, None)
        self._tabs.removeTab(index)
        tab.deleteLater()

    def _on_current_tab_changed(self, index: int) -> None:
        """Focusing a tab clears its pending badge."""
        self.current_session_changed.emit()
        if index < 0:
            return
        tab = self._tabs.widget(index)
        if tab is None:
            return
        self._tab_pending[tab] = 0
        self._refresh_tab_text(tab)

    def _on_tab_pending_changed(self, tab: AgentSessionTab, count: int) -> None:
        """A session's pending agent-message count changed -> badge it, unless
        the user is already looking at that tab."""
        if self._tabs.currentWidget() is tab:
            count = 0
        self._tab_pending[tab] = count
        self._refresh_tab_text(tab)

    def _refresh_tab_text(self, tab: AgentSessionTab) -> None:
        """Recompute a tab's label from its agent name + pending count.

        The tab's identity is ``tab.agent_name``; the label is only a display
        derived from it, so a badge never renames the session.
        """
        index = self._tabs.indexOf(tab)
        if index < 0:
            return
        count = self._tab_pending.get(tab, 0)
        text = f"{tab.agent_name} ({count})" if count > 0 else tab.agent_name
        self._tabs.setTabText(index, text)

    def _on_main_widget_created(self, widget: object) -> None:
        """Wire the panel-scoped extras onto a freshly built main session."""
        self._claude_widget = widget
        self._wire_main_widget(widget)

    def _on_main_widget_destroyed(self) -> None:
        self._claude_widget = None

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
        """Subscribe to plugin signals for hot-reload (no-op; ToolRegistry has no signal)."""
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

        # Remove loading UI and initialize every open session
        for tab in self._session_tabs():
            tab.clear_loading()
            if not tab.is_agent_ready:
                tab.initialize()
        self._sync_main_session_state()

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
        for tab in self._session_tabs():
            tab.show_loading()

    def _initialize_claude_widget(self) -> None:
        """Initialize the main Claude session (after plugins are loaded)."""
        tab = self._lightfall_tab
        if tab is None:
            return
        tab.initialize()
        self._sync_main_session_state()

    def _sync_main_session_state(self) -> None:
        """Mirror the main session's state onto the panel.

        ``_claude_widget`` / ``_is_agent_ready`` / ``_error_message`` are read
        by other components (logbook panel, tutorial, skill-trigger button) and
        by the Claude-settings hot-reload logic below, so the panel keeps them
        pointing at the lightfall session.
        """
        tab = self._lightfall_tab
        if tab is None:
            return
        self._claude_widget = tab.claude_widget
        self._is_agent_ready = tab.is_agent_ready
        self._error_message = tab.error_message

    def _reload_agent(self) -> None:
        """Reload the main Claude agent with new tools.

        This stops the current agent and re-initializes with all
        currently registered tools.
        """
        logger.info("Reloading Claude agent with new tools")
        self._deferred_reload.discard(None)
        self._deferred_polling.discard(None)
        self._reset_cockpit()

        # Clear pending plugins list
        self._pending_plugins.clear()

        # Dispose any reload banner -- dropping just the reference leaves the
        # QFrame parented and visible (a stale, possibly stacked banner).
        if self._reload_banner is not None:
            self._layout.removeWidget(self._reload_banner)
            self._reload_banner.deleteLater()
        self._reload_banner = None

        tab = self._lightfall_tab
        if tab is None:
            # No tab surface (e.g. a test harness that stubbed _setup_ui).
            self._error_message = None
            self._is_agent_ready = False
            self._initialize_claude_widget()
            return
        tab.reload_agent()
        self._sync_main_session_state()

    def _wire_main_widget(self, claude_widget) -> None:
        """Attach the panel-scoped extras to the main session's widget.

        Everything here used to live at the tail of ``_setup_claude_widget``;
        it stays on the panel because it drives panel chrome (title bar, the
        sidebar icon) rather than the session itself.
        """
        self._cockpit.show_cost = self._session_info(claude_widget).caps.cost
        self._refresh_cockpit_label()

        # Title-bar cockpit label (cost / context% / tokens)
        if self._cost_label is None:
            self._cost_label = QLabel(self._cockpit.format())
            self._cost_label.setObjectName("ClaudeCockpitLabel")
            self._apply_cockpit_label_style()
            self._cost_label.setToolTip(self._cockpit.tooltip())
            self.add_title_bar_widget(self._cost_label)

        # Thinking / tool-usage visibility toggle (default: hidden)
        if self._verbose_action is None:
            self._verbose_action = self.add_title_bar_button(
                "mdi6.thought-bubble-outline",
                "Show thinking and tool usage",
                on_triggered=self._on_toggle_verbose,
                checkable=True,
                checked=self._verbose_visible,
            )

        # Session history / restore (title-bar menu — per spec §4.3)
        if self._sessions_menu is None:
            from PySide6.QtWidgets import QMenu
            self._sessions_menu = QMenu()
            self._sessions_menu.aboutToShow.connect(self._populate_sessions_menu)
            self.add_title_bar_button("mdi6.history", "Restore a past session",
                                      menu=self._sessions_menu)

        # Connect permission signals to toast notifications and icon state
        claude_widget.approval_needed.connect(self._on_approval_needed)
        claude_widget.approval_needed.connect(
            lambda *_: self._icon_set_permission()
        )
        claude_widget.approval_resolved.connect(self._on_approval_resolved)
        claude_widget.approval_resolved.connect(
            lambda *_: self._icon_set_thinking()
        )

        # Connect icon state: query_started for immediate feedback
        claude_widget.query_started.connect(lambda: self._icon_set_thinking())

        # NOTE: model/effort menu signals are wired for every tab in
        # _wire_session_controls (via _add_tab), not here -- this method only
        # handles main-session-scoped chrome.

        # Connect agent signals to sidebar icon state
        self._connect_icon_signals()

        # Record the settings this agent was built with, so a later preference
        # change only rebuilds when something the agent cares about differs.
        self._active_agent_config = self._current_claude_config()

        logger.info("Claude assistant panel initialized")

    def _apply_verbose_state(self, widget: object) -> None:
        """Push the panel's thinking/tool-usage visibility onto a session widget.

        Guarded with hasattr: test harnesses stub the session widget with
        plain QWidgets that lack set_verbose_visible.
        """
        if widget is not None and hasattr(widget, "set_verbose_visible"):
            widget.set_verbose_visible(self._verbose_visible)

    def _on_toggle_verbose(self, checked: bool) -> None:
        """Show/hide thinking and tool-usage fragments in every open session."""
        self._verbose_visible = checked
        tabs = self._session_tabs()
        for tab in tabs:
            self._apply_verbose_state(tab.claude_widget)
        # No tab surface (test harness) -- fall back to the mirrored widget.
        if not tabs:
            self._apply_verbose_state(self._claude_widget)

    def _register_bus_endpoint(self) -> None:
        """Register the main session's bus endpoint (see agent_session_tab)."""
        register_bus_endpoint(self._claude_widget)

    def _unregister_bus_endpoint(self) -> None:
        """Unregister the main session's bus endpoint, if registered."""
        unregister_bus_endpoint(self._claude_widget)

    def _populate_sessions_menu(self) -> None:
        from lightfall.agents.backends.select import backend_kind, make_session_store
        from lightfall.claude.agent import lightfall_agent_cwd
        menu = self._sessions_menu
        menu.clear()
        infos = make_session_store(backend_kind(), lightfall_agent_cwd()).list_sessions(limit=15)
        if not infos:
            act = menu.addAction("No saved sessions")
            act.setEnabled(False)
            return
        for info in infos:
            act = menu.addAction(info.title)
            act.triggered.connect(
                lambda _checked=False, s=info.session_id: self.restore_session(s)
            )

    def restore_session(self, session_id: str) -> None:
        """Rebuild the main agent resuming ``session_id`` and repaint its chat."""
        from lightfall.agents.backends.select import backend_kind, make_session_store
        from lightfall.claude.agent import lightfall_agent_cwd
        tab = self._lightfall_tab
        if tab is None:
            return
        tab._pending_resume_session_id = session_id
        self._reload_agent()  # the tab passes resume= then clears it
        messages = make_session_store(backend_kind(), lightfall_agent_cwd()).load_transcript(
            session_id
        )
        if self._claude_widget is not None and messages:
            self._claude_widget.load_transcript(messages)

    def _build_ncs_system_prompt(self) -> str:
        """Build the NCS-specific system prompt addition.

        Lives in ``agent_session_tab`` now (every session needs it); kept here
        as a thin delegate.
        """
        return build_ncs_system_prompt()

    def _session_info(self, widget=None) -> backend_infos.BackendInfo:
        """The descriptor the main session (or ``widget``) was built with.

        During a pending reload the backend preference may already name a new
        backend while the old agent is still live; picks and toasts must follow
        the live agent. Falls back to the preference when the widget has none
        (not built yet, or a test stub).
        """
        widget = self._claude_widget if widget is None else widget
        info = getattr(widget, "backend_info", None)
        if info is not None:
            return info
        try:
            return backend_infos.backend_info()
        except Exception as exc:  # noqa: BLE001 - fall back like the widget does
            logger.warning("Assistant panel: backend lookup failed ({})", exc)
            return backend_infos.ClaudeCodeInfo()

    def _wire_session_controls(self, claude_widget) -> None:
        """Wire a session widget's model/effort menu to the panel handlers.

        Runs for every tab (via ``_add_tab`` -> ``widget_created``), not just the
        main one. Without it, a model/effort pick on a secondary session tab
        emitted a signal with no receiver, so the menu did nothing.
        """
        # Degraded or stub widgets may lack the menu signals.
        for name, slot in (("model_change_requested", self._on_pick_model),
                           ("effort_change_requested", self._on_pick_effort)):
            signal = getattr(claude_widget, name, None)
            if signal is not None:
                signal.connect(slot)

    def _picking_widget(self):
        """The session widget whose menu emitted the current pick.

        Resolved from ``sender()`` so a pick targets the tab it was made on.
        Falls back to the main widget when there is no signal sender (direct
        calls, e.g. tests) or the sender is not a session widget.
        """
        sender = self.sender()
        if sender is not None and hasattr(sender, "agent") \
                and hasattr(sender, "model_change_requested"):
            return sender
        return self._claude_widget

    def _tab_of_widget(self, widget):
        """The ``AgentSessionTab`` hosting ``widget``, or None."""
        if widget is None:
            return None
        for tab in self._session_tabs():
            if getattr(tab, "claude_widget", None) is widget:
                return tab
        return None

    def _on_pick_model(self, preset: str) -> None:
        from lightfall.ui.preferences.manager import PreferencesManager

        widget = self._picking_widget()
        info = self._session_info(widget)
        tab = self._tab_of_widget(widget)
        if tab is not None and tab is not self._lightfall_tab:
            # A secondary tab's pick stays on that tab: the global preference
            # (the main session's model and the default for new tabs) is not
            # touched, so the main tab's menu and live model keep agreeing.
            tab.set_model_preset(preset)
            if hasattr(widget, "agent"):
                widget.agent.set_model(info.live_model_id(preset))
            return
        # Main tab: persist the raw preset under the active backend's key (the
        # menu re-checks against it); send the backend's live id to the agent.
        PreferencesManager.get_instance().set(info.model_pref_key, preset)
        if widget is not None and hasattr(widget, "agent"):
            widget.agent.set_model(info.live_model_id(preset))
        # The agent now reflects the new model (live on Claude; next turn on
        # agy). Update the tracked config so the debounced subscription sees no
        # change and does not tear the conversation down with a full reload.
        self._active_agent_config = self._current_claude_config()

    def _on_pick_effort(self, level: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        from lightfall.ui.preferences.manager import PreferencesManager

        widget = self._picking_widget()
        info = self._session_info(widget)
        tab = self._tab_of_widget(widget)
        if tab is not None and tab is not self._lightfall_tab:
            self._pick_tab_effort(tab, widget, info, level)
            return
        if level == info.current_effort():
            return
        if info.caps.live_set_effort:
            # agy: effort is a spawn flag; the next turn respawns with
            # --conversation, so the conversation is kept -- no dialog, no rebuild.
            PreferencesManager.get_instance().set(info.effort_pref_key, level)
            if widget is not None and hasattr(widget, "agent"):
                widget.agent.set_effort(level or None)
            # As for a live model pick: the debounced subscription must see no change.
            self._active_agent_config = self._current_claude_config()
            return
        reply = QMessageBox.question(
            self, "Change effort",
            "Changing reasoning effort restarts the conversation "
            "(a fresh session). Continue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        PreferencesManager.get_instance().set(info.effort_pref_key, level)
        # _reload_agent() rebuilds the agent and refreshes _active_agent_config,
        # so the debounced subscription sees no further change and won't reload
        # a second time. Effort is read at construction.
        self._reload_agent()

    def _pick_tab_effort(self, tab, widget, info, level: str) -> None:
        """Effort pick on a secondary tab: kept on the tab, never the global pref."""
        from PySide6.QtWidgets import QMessageBox

        if level == tab.effective_effort():
            return
        if info.caps.live_set_effort:
            # agy: a spawn flag; the next turn picks it up, conversation kept.
            tab.set_effort_level(level)
            if hasattr(widget, "agent"):
                widget.agent.set_effort(level or None)
            return
        reply = QMessageBox.question(
            self, "Change effort",
            "Changing reasoning effort restarts this tab's conversation "
            "(a fresh session). Continue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Effort is read at construction: rebuild just this tab, which builds
        # its agent with the tab's own level.
        tab.set_effort_level(level)
        tab.reload_agent()

    # --- Claude-settings hot-reload ------------------------------------------

    def _subscribe_to_claude_settings(self) -> None:
        """Rebuild the agent when a watched Claude preference changes.

        The Preferences dialog only writes ``claude_*`` prefs; without this, a
        model/endpoint/key change would not reach the already-built agent until
        the next lightfall restart. The subscription closes that gap. Bound-
        method slots are held weakly by the preferences manager, so this
        auto-clears when the panel is destroyed.
        """
        from lightfall.ui.preferences.manager import PreferencesManager

        prefs = PreferencesManager.get_instance()
        for key in self._WATCHED_CLAUDE_KEYS:
            prefs.subscribe(key, self._on_claude_pref_changed)

    def _current_claude_config(self) -> tuple:
        """Snapshot the settings the agent is (re)built from.

        Used to decide whether a preference change actually changed anything the
        agent cares about. The model is resolved through ``resolve_model_alias``
        because that is the exact string handed to the agent (at construction
        and on a live switch); endpoint + custom URL collapse into the base URL.
        """
        from lightfall.ui.preferences.claude_settings import (
            ClaudeSettingsProvider,
            resolve_model_alias,
        )

        p = ClaudeSettingsProvider
        return (
            p.get_agent_backend(),
            resolve_model_alias(p.get_model()),
            p.get_effort(),
            p.get_base_url(),
            p.get_api_key(),
            p.get_max_turns(),
            p.get_permission_mode(),
            p.get_disable_betas(),
            p.get_agy_path(),
            p.get_agy_project(),
            p.get_agy_location(),
            # Model first: its read migrates a saved variant id into agy_effort,
            # which must happen before effort is read or the snapshot changes once.
            p.get_agy_model(),
            p.get_agy_effort(),
        )

    def _on_claude_pref_changed(self, value: object) -> None:
        """A watched Claude preference changed -> schedule one debounced reload.

        Coalesces a burst of writes (one Preferences-dialog OK calls
        save_settings() on every plugin) into a single rebuild via a 0-delay
        timer. Whether the rebuild actually happens is decided in
        ``_do_claude_settings_reload`` by comparing the effective config -- so
        a no-op save is cheap and harmless.
        """
        if self._settings_reload_pending:
            return
        self._settings_reload_pending = True
        QTimer.singleShot(0, self._do_claude_settings_reload)

    def _do_claude_settings_reload(self) -> None:
        """Apply pending Claude-settings changes by rebuilding the agent.

        Rebuilds only when the effective config actually changed (so an
        unrelated Preferences OK, or the picker's already-applied live switch,
        is a no-op). Idle tabs rebuild immediately. Busy tabs (main or extra)
        reload automatically when their reply ends; the main tab also shows
        the banner. No-op while the agent isn't built yet (construction reads
        fresh prefs).
        """
        self._settings_reload_pending = False
        if not self._is_agent_ready:
            # Two distinct not-ready states:
            #  * Still loading plugins (no prior build, _error_message is None):
            #    the agent will be built shortly and read fresh prefs -- nothing
            #    to do here.
            #  * A prior build FAILED (_error_message set): a settings change is
            #    the user fixing it (e.g. entering a valid key / endpoint). Re-
            #    attempt the build so the panel recovers without a restart --
            #    which is the whole point of this feature. Skip the config-diff
            #    gate: _active_agent_config is stale/None in the error state.
            if self._error_message is not None:
                self._reload_agent()
            return
        if self._current_claude_config() == self._active_agent_config:
            return  # nothing the agent cares about changed
        for tab in self._session_tabs():
            if tab is not self._lightfall_tab:
                self._reload_tab_when_idle(tab)
        widget = self._claude_widget
        agent = getattr(widget, "agent", None) if widget is not None else None
        if (agent is not None and agent.is_busy()) or _voice_queue_pending(widget):
            self._show_settings_reload_banner()
            self._reload_tab_when_idle(None)  # registers the auto-reload
        else:
            self._reload_agent()

    def _reload_tab_when_idle(self, tab) -> None:
        """Reload ``tab`` now if idle, else once its current reply ends.

        ``tab`` is an AgentSessionTab, or None for the main session. The reload
        runs from a zero-delay timer so the finishing turn's own slots complete
        before its agent is torn down.
        """
        widget = self._claude_widget if tab is None else tab.claude_widget
        agent = getattr(widget, "agent", None) if widget is not None else None
        self._voice_drain_watch.pop(tab, None)
        voice_pending = self._waiting_on_voice(tab, widget, agent)
        if (agent is None or not agent.is_busy()) and not voice_pending:
            self._deferred_reload.discard(tab)
            self._deferred_polling.discard(tab)
            if tab is None:
                self._reload_agent()
            else:
                tab.reload_agent()
            return
        if tab in self._deferred_reload:
            return
        self._deferred_reload.add(tab)
        if agent is None or not agent.is_busy():
            # Idle, but queued voice prompts go first: poll until they drain.
            self._deferred_polling.add(tab)
            QTimer.singleShot(
                self._DEFERRED_POLL_MS, self, lambda: self._poll_deferred_reload(tab)
            )
            return
        end_signals = (agent.query_completed, agent.query_cancelled, agent.error_occurred)

        def _fire(*_args, t=tab) -> None:
            # One-shot: the poll takes over from here, so drop all three hooks.
            for sig in end_signals:
                try:
                    sig.disconnect(_fire)
                except (RuntimeError, TypeError):  # already gone with the agent
                    pass
            if t in self._deferred_reload and t not in self._deferred_polling:
                self._deferred_polling.add(t)
                self._poll_deferred_reload(t)

        for sig in end_signals:
            sig.connect(_fire)

    def _waiting_on_voice(self, tab, widget, agent) -> bool:
        """Should ``tab``'s deferred reload keep waiting for its voice queue?

        Yes while the queue can drain. An idle session whose queue has not
        shrunk for ``_VOICE_STALL_POLLS`` polls is stuck (e.g. a permission card
        blocks sending), so the reload stops waiting rather than never running.
        """
        n = _voice_queue_len(widget)
        if n == 0:
            self._voice_drain_watch.pop(tab, None)
            return False
        busy = agent is not None and agent.is_busy()
        last = self._voice_drain_watch.get(tab)
        if busy or last is None or n < last[0]:
            self._voice_drain_watch[tab] = (n, 0)
            return True
        stalled = last[1] + 1
        if stalled >= self._VOICE_STALL_POLLS:
            self._voice_drain_watch.pop(tab, None)
            logger.info(
                "Deferred reload of {}: {} queued voice message(s) are not draining; "
                "reloading anyway",
                "lightfall" if tab is None else getattr(tab, "agent_name", tab), n,
            )
            return False
        self._voice_drain_watch[tab] = (n, stalled)
        return True

    def _deferred_tab_gone(self, tab) -> bool:
        """True (and the deferral dropped) if extra tab ``tab`` left the panel.

        Covers tabs removed other than through ``_on_tab_close_requested``.
        """
        if tab is None or tab in self._session_tabs():
            return False
        self._voice_drain_watch.pop(tab, None)
        self._deferred_reload.discard(tab)
        self._deferred_polling.discard(tab)
        return True

    def _poll_deferred_reload(self, tab) -> None:
        """After a turn ends, reload ``tab`` once it is idle.

        Polls for as long as the reload stays pending: a new turn started in
        the meantime keeps it waiting, so a reply is never torn down. Queued
        (unpaused) voice prompts also keep it waiting: the queue drains first.
        """
        if tab not in self._deferred_reload or tab not in self._deferred_polling:
            self._deferred_polling.discard(tab)  # closed / reloaded meanwhile
            return
        if self._deferred_tab_gone(tab):
            return
        widget = self._claude_widget if tab is None else tab.claude_widget
        agent = getattr(widget, "agent", None) if widget is not None else None
        waiting_on_voice = self._waiting_on_voice(tab, widget, agent)
        if (agent is not None and agent.is_busy()) or waiting_on_voice:
            QTimer.singleShot(
                self._DEFERRED_POLL_MS, self, lambda: self._poll_deferred_reload(tab)
            )
            return
        self._deferred_polling.discard(tab)
        self._deferred_reload.discard(tab)
        if tab is None:
            self._reload_agent()
        else:
            tab.reload_agent()

    def _show_settings_reload_banner(self) -> None:
        """Offer a reload (rather than interrupting a running query)."""
        if self._reload_banner is not None:
            self._reload_banner.show()
            return
        self._reload_banner = ReloadBannerWidget(
            plugin_names=[],
            on_reload=self._reload_agent,
            message="⚙ Assistant settings changed — applies after this reply",
            parent=self,
        )
        self._layout.insertWidget(0, self._reload_banner)

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
            f"{self._session_info().display_name} wants to use: {display_name}",
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
        """Get the main application window (see agent_session_tab)."""
        return find_main_window(self)

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

    def _apply_cockpit_label_style(self) -> None:
        """Style the title-bar cockpit label with a muted, theme-tracking color.

        Two things were wrong with the old ``color: palette(mid)`` stylesheet:
        ``Mid`` is a border/shade role (near-black in the dark theme), and the
        label's *own* palette is the unthemed Qt default (black window, dark
        text), so every ``palette(...)`` reference resolved against dark defaults
        — hence dark-on-dark. We instead resolve the muted-text role
        (``PlaceholderText``) from the *application* palette, which carries the
        active theme, and set a concrete color (the stylesheet ``palette(...)``
        function does not accept ``placeholder-text`` anyway).
        """
        label = self._cost_label
        if label is None:
            return
        app = QApplication.instance()
        palette = app.palette() if app is not None else label.palette()
        color = palette.color(QPalette.ColorRole.PlaceholderText)
        label.setStyleSheet(f"color: {color.name()}; padding: 0 6px;")

    def _refresh_cockpit_label(self) -> None:
        if self._cost_label is not None:
            # Re-resolve the color so the label follows live theme changes.
            self._apply_cockpit_label_style()
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
        from lightfall.agents.bus import AgentBus

        AgentBus.get_instance().set_launcher(None)

        self._stop_thinking_animation()
        self._stop_permission_animation()
        # ToolRegistry has no signals to disconnect

        # Disconnect from loader signals
        loader = self._get_plugin_loader()
        if loader is not None:
            try:
                loader.loading_complete.disconnect(self._on_plugin_loading_complete)
            except Exception:
                pass  # Ignore if not connected

        tabs = self._session_tabs()
        if tabs:
            # Every open session unregisters its bus endpoint and stops its
            # agent -- not just the main one. wait_for_worker=False: a
            # blocking worker join here runs on the GUI thread during app
            # shutdown; N tabs x 5s exceeds the exit watchdog, whose forced
            # os._exit() then crashes mid-teardown (0xC0000005). Signal the
            # workers and abandon them, as the pre-tab close path did.
            for tab in tabs:
                tab.discard_voice_queue("application closing")
                tab.close_session(wait_for_worker=False)
            self._claude_widget = None
        else:
            self._unregister_bus_endpoint()

            # Stop the agent (no join — see above)
            if self._claude_widget and hasattr(self._claude_widget, 'agent'):
                try:
                    self._claude_widget.agent.stop(wait_ms=0)
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
                    "description": "Send a message to the assistant",
                    "method": "action_send_message",
                    "parameters": {"message": "string"},
                },
                {
                    "name": "clear_chat",
                    "description": "Clear the chat history display",
                    "method": "action_clear_chat",
                },
                {
                    "name": "open_agent_tab",
                    "description": "Open (or focus) a session tab for a named agent; "
                                   "optionally send it a message once the session is ready",
                    "method": "action_open_agent_tab",
                    "parameters": {"agent": "string", "message": "string (optional)"},
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
        # Programmatic sends (logbook "send to Claude", skill triggers) go to
        # the main assistant, so bring its tab to the front -- otherwise the
        # message lands in a session the user isn't looking at.
        widget = self._focus_lightfall_session()
        if widget is None:
            return False

        # Set the input field text and trigger send
        if hasattr(widget, 'input_field'):
            widget.input_field.setPlainText(message)
            widget._send_query()
            return True

        return False

    def _validate_agent_open(self, agent: str) -> tuple[Any, dict[str, Any] | None]:
        """Run the preconditions shared by ``action_open_agent_tab`` and
        ``_launch_agent_for``: the agent must be defined, enabled, openable,
        and a tab surface must exist to host it.

        Returns ``(spec, None)`` on success, or ``(None, error_dict)`` on the
        first failing check.
        """
        from lightfall.agents.registry import AgentSpecRegistry

        registry = AgentSpecRegistry.get_instance()
        spec = registry.get(agent)
        if spec is None:
            available = sorted(
                s.name for s in registry.enabled_specs() if s.openable)
            return None, {"success": False,
                    "error": f"unknown agent '{agent}'; available: {available}"}
        if agent not in {s.name for s in registry.enabled_specs()}:
            return None, {"success": False,
                    "error": f"agent '{agent}' is disabled in settings"}
        if not spec.openable:
            return None, {"success": False,
                    "error": f"agent '{agent}' is not openable as a session "
                             f"(lightfall.openable: false)"}
        if self._tabs is None:
            return None, {"success": False, "error": "tab surface not available"}
        return spec, None

    def action_open_agent_tab(self, agent: str, message: str | None = None) -> dict[str, Any]:
        """Open (or focus) a tab for ``agent``; optionally queue a first message.

        Reuses the same code path as the new-tab picker (``_open_agent_tab``),
        so session assembly, singleton-per-agent focus behavior, and bus
        registration are identical to a UI-initiated open. This is the
        generic UI/introspection path: it never whitelists a launcher (see
        ``_launch_agent_for`` for the bus-launch path, which does).
        """
        spec, error = self._validate_agent_open(agent)
        if error is not None:
            return error

        focused_existing = self._find_tab(agent) is not None
        self._open_agent_tab(spec)  # focuses existing or creates + activates
        if message:
            self._deliver_when_ready(self._find_tab(agent), message)
        return {"success": True, "agent": agent,
                "focused_existing": focused_existing,
                "message_queued": bool(message)}

    def _launch_agent_for(
        self, name: str, launched_by: str, first_message: str | None
    ) -> dict[str, Any]:
        """Bus-invoked launcher hook (``AgentBus.set_launcher``): open/focus
        ``name``'s tab on behalf of ``launched_by``.

        On create, the new session whitelists ``launched_by`` for auto-accept
        (``AgentSessionTab`` -> ``ClaudeAssistantWidget`` ->
        ``ClaudeSessionEndpoint``). Focusing an already-running session grants
        no whitelist -- it isn't a fresh launch by this sender -- but
        ``first_message``, if given, is still delivered either way -- as a bus
        message from ``launched_by``, so it carries the sender's attribution
        and a running peer's accept policy applies (a fresh launch has the
        launcher whitelisted, so it lands immediately).
        """
        spec, error = self._validate_agent_open(name)
        if error is not None:
            return error

        focused_existing = self._find_tab(name) is not None
        tab = self._open_agent_tab(spec, launched_by=launched_by)
        if first_message:
            self._deliver_bus_when_ready(tab, launched_by, first_message)
        return {"success": True, "agent": name,
                "focused_existing": focused_existing,
                "message_queued": bool(first_message)}

    def _deliver_when_ready(self, tab, message: str) -> None:
        """Submit ``message`` to ``tab`` now, or once its session widget exists."""
        widget = tab.claude_widget
        if widget is not None:
            widget.input_field.setPlainText(message)
            widget._send_query()
            return

        def _once(w, _text=message):
            try:
                tab.widget_created.disconnect(_once)
            except (RuntimeError, TypeError):
                pass
            w.input_field.setPlainText(_text)
            w._send_query()

        tab.widget_created.connect(_once)

    def _deliver_bus_when_ready(self, tab, sender: str, message: str) -> None:
        """Deliver ``message`` from ``sender`` through ``tab``'s bus endpoint,
        now or once its session widget exists.

        Unlike ``_deliver_when_ready`` (a user-typed prompt), this goes through
        ``ClaudeSessionEndpoint.deliver``: the message is attributed to the
        sender and honors the session's accept policy / whitelist.
        """
        widget = tab.claude_widget
        if widget is not None:
            widget.bus_endpoint.deliver(sender, message)
            return

        def _once(w, _sender=sender, _text=message):
            try:
                tab.widget_created.disconnect(_once)
            except (RuntimeError, TypeError):
                pass
            w.bus_endpoint.deliver(_sender, _text)

        tab.widget_created.connect(_once)

    def _focus_lightfall_session(self):
        """Activate the lightfall tab and return its session widget (or None).

        Falls back to the mirrored ``_claude_widget`` when there is no tab
        surface (e.g. a test harness that stubbed ``_setup_ui``).
        """
        tab = self._lightfall_tab
        if tab is None:
            return self._claude_widget
        if self._tabs is not None:
            self._tabs.setCurrentWidget(tab)
        return tab.claude_widget

    def submit_external_prompt(self, text: str) -> bool:
        """Raise the Claude panel and submit a programmatic user prompt to
        the reactive agent. Returns False if the agent widget isn't built yet.

        Always targets the main lightfall session (and brings its tab to the
        front), whichever tab the user happens to be on.
        """
        win = self._get_main_window()
        if win is not None:
            win.activate_panel(self.panel_metadata.id)
        widget = self._focus_lightfall_session()
        if widget is None:
            return False
        widget.input_field.setPlainText(text)
        widget._send_query()  # auto-connects via agent.query_sync
        return True

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

        tabs = self._session_tabs()
        if tabs:
            current = self._tabs.currentWidget()
            data["open_tabs"] = [
                {
                    "agent": tab.agent_name,
                    "ready": tab.is_agent_ready,
                    "busy": tab.is_busy(),
                    "pending_messages": self._tab_pending.get(tab, 0),
                    "current": tab is current,
                    "error": tab.error_message,
                }
                for tab in tabs
            ]

        return data

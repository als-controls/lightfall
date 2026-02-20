"""Claude Assistant Panel for LUCID.

Provides an embedded Claude AI assistant with MCP tools for
interacting with the LUCID application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.toast import ToastManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class ReloadBannerWidget(QFrame):
    """Banner widget shown when new plugins are detected.

    Styled similar to the permission request widget from pyside-claude.
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
    - Qt widget inspection and interaction (from pyside-claude)
    - NCS panel management and introspection
    - Plugin-provided tools (Bluesky, devices, etc.)

    The panel requires pyside-claude to be installed and an
    Anthropic API key to be configured.
    """

    panel_metadata = PanelMetadata(
        id="lucid.panels.claude",
        name="Claude Assistant",
        description="AI assistant for interacting with the control system",
        icon="robot",
        category="Tools",
        singleton=True,
        closable=True,
        keywords=["claude", "ai", "assistant", "llm", "chat", "help"],
        # Docking preferences - bottom sidebar (auto-hide icons on bottom edge)
        default_area="bottom",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=0,
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
            from lucid.core.services import ServiceRegistry
            from lucid.plugins import PluginLoader

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
        """Subscribe to MCPToolRegistry signals for hot-reload."""
        try:
            from lucid.ui.panels.claude.tool_registry import MCPToolRegistry

            registry = MCPToolRegistry.get_instance()
            registry.signals.plugin_registered.connect(self._on_plugin_registered)
            logger.debug("Subscribed to MCPToolRegistry plugin_registered signal")
        except Exception as e:
            logger.debug("Could not subscribe to plugin_registered: {}", e)

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
        self._loading_label.setStyleSheet("""
            QLabel {
                color: #888;
                padding: 40px;
                font-size: 12pt;
            }
        """)
        self._layout.addWidget(self._loading_label)

    def _initialize_claude_widget(self) -> None:
        """Initialize the Claude widget (after plugins are loaded)."""
        try:
            self._setup_claude_widget()
            self._is_agent_ready = True
        except ImportError as e:
            self._error_message = f"pyside-claude not installed: {e}"
            logger.warning(self._error_message)
            self._setup_error_ui(self._error_message)
        except ValueError as e:
            # API key not configured
            self._error_message = str(e)
            logger.warning("Claude panel disabled: {}", self._error_message)
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
        from pyside_claude import ClaudeAssistantWidget

        from lucid.ui.preferences.claude_settings import ClaudeSettingsProvider

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

        # Collect all MCP tools
        all_tools = self._collect_mcp_tools(main_window)

        # Build additional system prompt for NCS
        ncs_system_prompt = self._build_ncs_system_prompt()

        # Create the Claude widget with the main window as target and additional tools
        self._claude_widget = ClaudeAssistantWidget(
            target_window=main_window,
            api_key=ClaudeSettingsProvider.get_api_key(),
            api_url=ClaudeSettingsProvider.get_base_url(),
            additional_tools=all_tools,
            additional_system_prompt=ncs_system_prompt,
            parent=self,
        )

        # Add to layout
        self._layout.addWidget(self._claude_widget)

        # Connect permission signals to toast notifications
        self._claude_widget.approval_needed.connect(self._on_approval_needed)
        self._claude_widget.approval_resolved.connect(self._on_approval_resolved)

        logger.info(
            "Claude assistant panel initialized with {} additional tools",
            len(all_tools)
        )

    def _collect_mcp_tools(self, main_window) -> list:
        """Collect all MCP tools from various sources.

        Tools are collected from:
        1. NCS core tools (always included, not registered as plugins)
        2. MCPToolRegistry - all enabled tool plugins (mcp_tool and skill types)

        Args:
            main_window: The main window reference.

        Returns:
            List of tool functions (deduplicated by name).
        """
        all_tools = []
        seen_names: set[str] = set()

        def add_tools(tools: list, source: str) -> int:
            """Add tools, skipping duplicates by name."""
            added = 0
            for tool_func in tools:
                # Get tool name
                if hasattr(tool_func, 'name'):
                    tool_name = tool_func.name
                elif hasattr(tool_func, '__name__'):
                    tool_name = tool_func.__name__
                else:
                    # Can't determine name, add anyway
                    all_tools.append(tool_func)
                    added += 1
                    continue

                if tool_name in seen_names:
                    logger.warning(
                        "Skipping duplicate tool '{}' from {}",
                        tool_name,
                        source,
                    )
                    continue

                seen_names.add(tool_name)
                all_tools.append(tool_func)
                added += 1
            return added

        # 1. Add NCS core tools (always included, not a plugin)
        try:
            from lucid.plugins.tools.ncs_tools import NCSCoreToolPlugin
            ncs_core = NCSCoreToolPlugin(main_window)
            core_tools = ncs_core.create_tools()
            added = add_tools(core_tools, "NCS core")
            logger.debug("Added {} NCS core tools", added)
        except Exception as e:
            logger.warning("Failed to create NCS core tools: {}", e)

        # 2. Add tools from enabled tool plugins (includes both mcp_tool and skill types)
        # MCPToolRegistry.get_enabled_tools() returns tools only from enabled plugins
        try:
            from lucid.ui.panels.claude.tool_registry import MCPToolRegistry
            registry = MCPToolRegistry.get_instance()
            plugin_tools = registry.get_enabled_tools()
            added = add_tools(plugin_tools, "enabled plugins")
            logger.debug("Added {} tools from enabled plugins", added)
        except Exception as e:
            logger.warning("Failed to get plugin tools: {}", e)

        logger.info("Collected {} unique MCP tools total", len(all_tools))
        return all_tools

    def _build_ncs_system_prompt(self) -> str:
        """Build the NCS-specific system prompt addition.

        Returns:
            System prompt text to append.
        """
        # Start with core NCS system prompt
        # Inject current user's name
        user_name = ""
        try:
            from lucid.auth.session import SessionManager
            user = SessionManager.get_instance().current_user
            if user and user.display_name and user.display_name != "Guest":
                user_name = user.display_name
            elif user and user.username and user.username != "anonymous":
                user_name = user.username
        except Exception:
            pass

        user_context = f"\nThe current logged-in user is: {user_name}\n" if user_name else ""

        base_prompt = f"""
You are integrated with NCS (New Control System), a scientific data acquisition application.
{user_context}

## NCS Domain Tools

These tools let you work with NCS directly - prefer these over generic Qt interaction:

**Panel Management:**
- ncs_list_panels - See available panels and what's currently open
- ncs_open_panel / ncs_close_panel / ncs_activate_panel - Manage panels
- ncs_get_panel_info - Get panel widgets and available actions
- ncs_invoke_panel_action - Trigger panel actions directly

**Application State:**
- ncs_get_application_info - Get overall application state

## Key Panels
- Bluesky panel: Controls data acquisition scans
- Device panel: Shows available hardware devices
- Logbook panel: Records experiment notes and actions

## Guidelines
- Use panel actions (ncs_invoke_panel_action) rather than clicking widgets when available - they're more reliable
- Only use ncs_list_panels or ncs_get_panel_info when you need to discover what's available
- If you know which panel/action to use, invoke it directly
"""

        # Append skill prompts from enabled skills
        try:
            from lucid.ui.panels.claude.skill_registry import SkillRegistry
            skill_registry = SkillRegistry.get_instance()
            skill_prompts = skill_registry.get_aggregated_system_prompt()
            if skill_prompts:
                base_prompt += "\n\n# Enabled Skills\n\n" + skill_prompts
                logger.debug(
                    "Added {} chars of skill prompts to system prompt",
                    len(skill_prompts)
                )

            # Add skill reminder so Claude knows skills are invocable
            skill_reminder = skill_registry.get_skill_reminder()
            if skill_reminder:
                base_prompt += "\n\n" + skill_reminder
                logger.debug("Added skill reminder to system prompt")
        except Exception as e:
            logger.warning("Failed to get skill prompts: {}", e)

        return base_prompt

    def _setup_error_ui(self, message: str) -> None:
        """Setup error UI when Claude is not available.

        Args:
            message: Error message to display.
        """
        error_label = QLabel(f"Claude Assistant Unavailable\n\n{message}")
        error_label.setWordWrap(True)
        error_label.setStyleSheet("""
            QLabel {
                color: #888;
                padding: 20px;
                font-size: 12pt;
            }
        """)
        self._layout.addWidget(error_label)

    def _on_approval_needed(
        self, request_id: str, tool_name: str, tool_input: dict
    ) -> None:
        """Handle permission request signal with a toast notification.

        Args:
            request_id: Unique ID for this request.
            tool_name: Name of the tool requesting permission.
            tool_input: Input parameters for the tool.
        """
        # Extract a human-friendly tool name (remove mcp__ prefix if present)
        display_name = tool_name
        if display_name.startswith("mcp__"):
            display_name = display_name[5:]  # Remove "mcp__"
        display_name = display_name.replace("_", " ").title()

        toast = ToastManager.get_instance()
        toast.warning(
            "Permission Required",
            f"Claude wants to use: {display_name}",
            duration=8000,
        )
        logger.debug("Permission requested for tool: {}", tool_name)

    def _on_approval_resolved(self, request_id: str, was_allowed: bool) -> None:
        """Handle permission resolution signal with a toast notification.

        Args:
            request_id: Unique ID for this request.
            was_allowed: Whether the permission was granted.
        """
        toast = ToastManager.get_instance()
        if was_allowed:
            toast.success("Permission Granted", "Tool execution approved")
        else:
            toast.info("Permission Denied", "Tool execution blocked")
        logger.debug(
            "Permission resolved: request_id={}, allowed={}", request_id, was_allowed
        )

    def _get_main_window(self) -> QWidget | None:
        """Get the main application window.

        Returns:
            The NCSMainWindow or None.
        """
        # Walk up the parent chain to find the main window
        widget = self.parent()
        while widget is not None:
            if widget.__class__.__name__ == "NCSMainWindow":
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
                if widget.__class__.__name__ == "NCSMainWindow":
                    return widget
                if hasattr(widget, "menuBar"):
                    return widget

        return None

    def _on_closing(self) -> None:
        """Cleanup when panel is closing."""
        # Disconnect from registry signals
        try:
            from lucid.ui.panels.claude.tool_registry import MCPToolRegistry

            registry = MCPToolRegistry.get_instance()
            registry.signals.plugin_registered.disconnect(self._on_plugin_registered)
        except Exception:
            pass  # Ignore disconnection errors

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

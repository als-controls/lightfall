"""Claude Assistant Panel for LUCID.

Provides an embedded Claude AI assistant with MCP tools for
interacting with the LUCID application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QLabel

from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


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
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Claude panel.

        Args:
            parent: Parent widget.
        """
        self._claude_widget = None
        self._agent = None
        self._error_message: str | None = None
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Try to import and create the Claude widget
        try:
            self._setup_claude_widget()
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

    def _setup_claude_widget(self) -> None:
        """Setup the Claude assistant widget with extended tools."""
        from pyside_claude import ClaudeAssistantWidget

        from lucid.ui.preferences.claude_settings import ClaudeSettingsProvider

        # Check if Claude is configured
        if not ClaudeSettingsProvider.is_configured():
            raise ValueError(
                "Claude API key not configured. Set it in Preferences > Claude Assistant "
                "or via ANTHROPIC_API_KEY environment variable."
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

        logger.info(
            "Claude assistant panel initialized with {} additional tools",
            len(all_tools)
        )

    def _collect_mcp_tools(self, main_window) -> list:
        """Collect all MCP tools from various sources.

        Args:
            main_window: The main window reference.

        Returns:
            List of tool functions.
        """
        all_tools = []

        # 1. Add NCS core tools
        try:
            from lucid.ui.panels.claude.ncs_tools import NCSCoreToolPlugin
            ncs_core = NCSCoreToolPlugin(main_window)
            core_tools = ncs_core.create_tools()
            all_tools.extend(core_tools)
            logger.debug("Added {} NCS core tools", len(core_tools))
        except Exception as e:
            logger.warning("Failed to create NCS core tools: {}", e)

        # 2. Add tools from MCP tool plugins
        try:
            from lucid.ui.panels.claude.tool_registry import MCPToolRegistry
            registry = MCPToolRegistry.get_instance()
            plugin_tools = registry.get_all_tools()
            all_tools.extend(plugin_tools)
            logger.debug("Added {} plugin tools", len(plugin_tools))
        except Exception as e:
            logger.warning("Failed to get plugin tools: {}", e)

        # 3. Add tools from enabled skill plugins
        try:
            from lucid.ui.panels.claude.skill_registry import SkillRegistry
            skill_registry = SkillRegistry.get_instance()
            skill_tools = skill_registry.get_aggregated_tools()
            all_tools.extend(skill_tools)
            logger.debug("Added {} skill tools", len(skill_tools))
        except Exception as e:
            logger.warning("Failed to get skill tools: {}", e)

        return all_tools

    def _build_ncs_system_prompt(self) -> str:
        """Build the NCS-specific system prompt addition.

        Returns:
            System prompt text to append.
        """
        # Start with core NCS system prompt
        base_prompt = """
You are also integrated with NCS (New Control System), a scientific data acquisition application.

Additional capabilities in NCS:
- Use ncs_list_panels to see available panels and what's currently open
- Use ncs_open_panel, ncs_close_panel, ncs_activate_panel to manage panels
- Use ncs_get_panel_info to get detailed information about a panel's widgets and state
- Use ncs_invoke_panel_action to trigger actions on panels
- Use ncs_get_application_info to get overall application state

When helping with NCS:
1. Use ncs_list_panels first to understand what panels are available
2. Check panel info to understand what actions are possible
3. Use panel actions when available instead of direct widget clicks for reliability
4. The Bluesky panel controls data acquisition scans
5. The Device panel shows available hardware devices
6. The Logbook panel records experiment notes and actions
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
            "error": self._error_message,
        }

        if self._claude_widget is not None and hasattr(self._claude_widget, 'agent'):
            agent = self._claude_widget.agent
            data["agent_busy"] = agent.is_busy() if hasattr(agent, 'is_busy') else None

        return data

"""Built-in LUCID MCP tools for Claude assistant.

Provides core tools for interacting with the LUCID application:
- Panel management (list, open, close, get info)
- Panel action invocation
- Application introspection
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lucid.plugins.mcp_tool import MCPToolPlugin
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.ui.mainwindow import NCSMainWindow


class NCSCoreToolPlugin(MCPToolPlugin):
    """Built-in tools for LUCID panel and window interaction.

    This plugin provides core tools that are always available:
    - list_panels: List available and open panels
    - open_panel: Open a panel by ID
    - close_panel: Close a panel by ID
    - get_panel_info: Get detailed panel introspection data
    - invoke_panel_action: Call an action on a panel
    - get_application_info: Get application state information

    These tools allow Claude to understand and interact with the
    LUCID application structure.
    """

    def __init__(self, main_window: NCSMainWindow) -> None:
        """Initialize with reference to main window.

        Args:
            main_window: The NCSMainWindow instance.
        """
        super().__init__()
        self._window = main_window

    @property
    def name(self) -> str:
        """Plugin name."""
        return "ncs_core"

    @property
    def tool_description(self) -> str:
        """Plugin description."""
        return "Core LUCID application interaction tools"

    def _get_panel_registry(self):
        """Get the panel registry."""
        from lucid.ui.panels.registry import PanelRegistry
        return PanelRegistry.get_instance()

    def _get_session_manager(self):
        """Get the session manager."""
        from lucid.auth.session import SessionManager
        return SessionManager.get_instance()

    def create_tools(self) -> list[Any]:
        """Create NCS core MCP tools.

        Returns:
            List of tool functions.
        """
        # Import here to avoid circular imports and ensure availability
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, NCS core tools disabled")
            return []

        @tool(
            name="ncs_list_panels",
            description="List all available panels that can be opened and currently open panels in NCS",
            input_schema={
                "type": "object",
                "properties": {},
            }
        )
        async def list_panels(args: dict) -> dict[str, Any]:
            """List available and open panels."""
            from pyside_claude._internal.threading import run_on_main_thread

            def _list():
                registry = self._get_panel_registry()
                session = self._get_session_manager()
                user = session.current_user

                # Get available panels (filtered by user permissions)
                available = []
                for meta in registry.list_available(user):
                    available.append({
                        "id": meta.id,
                        "name": meta.name,
                        "description": meta.description,
                        "category": meta.category,
                        "singleton": meta.singleton,
                    })

                # Get open panels
                open_panels = []
                for panel_id in self._window.list_open_panels():
                    panel = self._window.get_panel(panel_id)
                    if panel:
                        open_panels.append({
                            "id": panel_id,
                            "name": panel.panel_metadata.name,
                            "is_active": panel.is_active,
                        })

                return {
                    "available_panels": available,
                    "open_panels": open_panels,
                }

            return run_on_main_thread(_list)

        @tool(
            name="ncs_open_panel",
            description="Open a panel in NCS by its ID. Returns success status.",
            input_schema={
                "type": "object",
                "properties": {
                    "panel_id": {
                        "type": "string",
                        "description": "The panel ID to open (e.g., 'lucid.panels.devices')"
                    }
                },
                "required": ["panel_id"]
            }
        )
        async def open_panel(args: dict) -> dict[str, Any]:
            """Open a panel by ID."""
            from pyside_claude._internal.threading import run_on_main_thread

            panel_id = args["panel_id"]

            def _open():
                panel = self._window.add_panel(panel_id)
                if panel is not None:
                    return {"success": True, "panel_id": panel_id}
                return {
                    "success": False,
                    "panel_id": panel_id,
                    "error": "Failed to open panel (may not exist or permission denied)"
                }

            return run_on_main_thread(_open)

        @tool(
            name="ncs_close_panel",
            description="Close an open panel in NCS by its ID",
            input_schema={
                "type": "object",
                "properties": {
                    "panel_id": {
                        "type": "string",
                        "description": "The panel ID to close"
                    }
                },
                "required": ["panel_id"]
            }
        )
        async def close_panel(args: dict) -> dict[str, Any]:
            """Close a panel by ID."""
            from pyside_claude._internal.threading import run_on_main_thread

            panel_id = args["panel_id"]

            def _close():
                success = self._window.remove_panel(panel_id)
                return {
                    "success": success,
                    "panel_id": panel_id,
                    "error": None if success else "Panel not found or cannot be closed"
                }

            return run_on_main_thread(_close)

        @tool(
            name="ncs_activate_panel",
            description="Activate (focus) an open panel in NCS",
            input_schema={
                "type": "object",
                "properties": {
                    "panel_id": {
                        "type": "string",
                        "description": "The panel ID to activate"
                    }
                },
                "required": ["panel_id"]
            }
        )
        async def activate_panel(args: dict) -> dict[str, Any]:
            """Activate a panel by ID."""
            from pyside_claude._internal.threading import run_on_main_thread

            panel_id = args["panel_id"]

            def _activate():
                success = self._window.activate_panel(panel_id)
                return {
                    "success": success,
                    "panel_id": panel_id,
                    "error": None if success else "Panel not found"
                }

            return run_on_main_thread(_activate)

        @tool(
            name="ncs_get_panel_info",
            description="Get detailed information about an open panel including its widgets, state, and available actions",
            input_schema={
                "type": "object",
                "properties": {
                    "panel_id": {
                        "type": "string",
                        "description": "The panel ID to get info for"
                    }
                },
                "required": ["panel_id"]
            }
        )
        async def get_panel_info(args: dict) -> dict[str, Any]:
            """Get detailed panel introspection data."""
            from pyside_claude._internal.threading import run_on_main_thread

            panel_id = args["panel_id"]

            def _get_info():
                panel = self._window.get_panel(panel_id)
                if panel is None:
                    return {
                        "error": f"Panel '{panel_id}' not found or not open",
                        "panel_id": panel_id,
                    }
                return panel.get_introspection_data()

            return run_on_main_thread(_get_info)

        @tool(
            name="ncs_invoke_panel_action",
            description="Invoke an action on a panel. Use ncs_get_panel_info to see available actions.",
            input_schema={
                "type": "object",
                "properties": {
                    "panel_id": {
                        "type": "string",
                        "description": "The panel ID"
                    },
                    "action": {
                        "type": "string",
                        "description": "The action name to invoke"
                    },
                    "kwargs": {
                        "type": "object",
                        "description": "Optional keyword arguments for the action",
                        "default": {}
                    }
                },
                "required": ["panel_id", "action"]
            }
        )
        async def invoke_panel_action(args: dict) -> dict[str, Any]:
            """Invoke an action on a panel."""
            from pyside_claude._internal.threading import run_on_main_thread

            panel_id = args["panel_id"]
            action = args["action"]
            kwargs = args.get("kwargs", {})

            def _invoke():
                panel = self._window.get_panel(panel_id)
                if panel is None:
                    return {
                        "success": False,
                        "error": f"Panel '{panel_id}' not found",
                    }
                try:
                    result = panel.invoke_action(action, **kwargs)
                    return {
                        "success": True,
                        "result": result,
                    }
                except ValueError as e:
                    return {
                        "success": False,
                        "error": str(e),
                    }
                except Exception as e:
                    logger.error("Error invoking action {} on {}: {}", action, panel_id, e)
                    return {
                        "success": False,
                        "error": f"Action failed: {e}",
                    }

            return run_on_main_thread(_invoke)

        @tool(
            name="ncs_get_application_info",
            description="Get overall LUCID application state including window info, theme, user, and authentication state",
            input_schema={
                "type": "object",
                "properties": {},
            }
        )
        async def get_application_info(args: dict) -> dict[str, Any]:
            """Get application introspection data."""
            from pyside_claude._internal.threading import run_on_main_thread

            def _get_info():
                return self._window.get_introspection_data()

            return run_on_main_thread(_get_info)

        return [
            list_panels,
            open_panel,
            close_panel,
            activate_panel,
            get_panel_info,
            invoke_panel_action,
            get_application_info,
        ]

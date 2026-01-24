"""MCP Tool Registry for collecting tools from all sources.

The MCPToolRegistry is a singleton that collects MCP tools from:
1. Built-in Qt tools (from pyside-claude)
2. NCS core tools (panel interaction, etc.)
3. Plugin-provided tools (via mcp_tool plugin type)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from ncs.utils.logging import logger

if TYPE_CHECKING:
    from ncs.plugins.mcp_tool import MCPToolPlugin


class MCPToolRegistry:
    """Registry for collecting MCP tools from all sources.

    This singleton collects tools from MCP tool plugins and provides
    them to the Claude panel for registration with the agent.

    The registry supports both eager and lazy registration:
    - Plugins registered before initialize() are processed on init
    - Plugins registered after initialize() have tools created immediately

    Example::

        registry = MCPToolRegistry.get_instance()

        # Register a plugin
        registry.register_plugin(my_tool_plugin)

        # Initialize (creates tools from all registered plugins)
        registry.initialize()

        # Get all tools for the Claude agent
        tools = registry.get_all_tools()
    """

    _instance: MCPToolRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        """Initialize the registry.

        Use get_instance() to get the singleton instance.
        """
        self._tool_plugins: list[MCPToolPlugin] = []
        self._tools: list[Any] = []
        self._initialized = False

    @classmethod
    def get_instance(cls) -> MCPToolRegistry:
        """Get the singleton instance.

        Returns:
            The MCPToolRegistry instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    def register_plugin(self, plugin: MCPToolPlugin) -> None:
        """Register an MCP tool plugin.

        If already initialized, tools are created immediately.
        Otherwise, tools will be created on initialize().

        Args:
            plugin: The MCPToolPlugin instance to register.
        """
        self._tool_plugins.append(plugin)
        logger.debug("Registered MCP tool plugin: {}", plugin.name)

        if self._initialized:
            # Late registration - create tools immediately
            try:
                tools = plugin.create_tools()
                self._tools.extend(tools)
                logger.info(
                    "Created {} tools from late-registered plugin {}",
                    len(tools),
                    plugin.name,
                )
            except Exception as e:
                logger.error(
                    "Failed to create tools from plugin {}: {}",
                    plugin.name,
                    e,
                )

    def initialize(self) -> None:
        """Initialize all registered plugins and collect their tools.

        This method is idempotent - calling it multiple times has no effect
        after the first call.
        """
        if self._initialized:
            return

        logger.info("Initializing MCP tool registry with {} plugins", len(self._tool_plugins))

        for plugin in self._tool_plugins:
            try:
                tools = plugin.create_tools()
                self._tools.extend(tools)
                logger.info(
                    "Created {} tools from plugin {}",
                    len(tools),
                    plugin.name,
                )
            except Exception as e:
                logger.error(
                    "Failed to create tools from plugin {}: {}",
                    plugin.name,
                    e,
                )

        self._initialized = True
        logger.info("MCP tool registry initialized with {} total tools", len(self._tools))

    def get_all_tools(self) -> list[Any]:
        """Get all registered tools.

        Automatically initializes if not already done.

        Returns:
            List of tool functions.
        """
        self.initialize()
        return list(self._tools)

    def get_plugins(self) -> list[MCPToolPlugin]:
        """Get all registered plugins.

        Returns:
            List of MCPToolPlugin instances.
        """
        return list(self._tool_plugins)

    @property
    def is_initialized(self) -> bool:
        """Check if the registry has been initialized."""
        return self._initialized

    @property
    def tool_count(self) -> int:
        """Get the number of registered tools."""
        return len(self._tools)

    @property
    def plugin_count(self) -> int:
        """Get the number of registered plugins."""
        return len(self._tool_plugins)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry state and plugin information.
        """
        return {
            "initialized": self._initialized,
            "plugin_count": len(self._tool_plugins),
            "tool_count": len(self._tools),
            "plugins": [
                plugin.get_introspection_data()
                for plugin in self._tool_plugins
            ],
        }

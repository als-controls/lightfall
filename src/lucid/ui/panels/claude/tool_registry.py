"""MCP Tool Registry for collecting tools from all sources.

The MCPToolRegistry is a singleton that collects MCP tools from:
1. Built-in Qt tools (from pyside-claude)
2. NCS core tools (panel interaction, etc.)
3. Plugin-provided tools (via mcp_tool and skill plugin types)

Both mcp_tool and skill plugins are registered here, allowing unified
enable/disable management via the settings UI.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.mcp_tool import MCPToolPlugin


class MCPToolRegistrySignals(QObject):
    """Qt signals for MCPToolRegistry.

    Separate class because MCPToolRegistry is a singleton that may be
    created before QApplication exists.
    """

    # Emitted when a new plugin is registered (plugin_name)
    plugin_registered = Signal(str)
    # Emitted when a plugin is unregistered (plugin_name)
    plugin_unregistered = Signal(str)


class MCPToolRegistry:
    """Registry for collecting MCP tools from all sources.

    This singleton collects tools from MCP tool plugins (including skills)
    and provides them to the Claude panel for registration with the agent.

    The registry supports both eager and lazy registration:
    - Plugins registered before initialize() are processed on init
    - Plugins registered after initialize() have tools created immediately

    Enable/Disable Support:
    - Plugins can be enabled/disabled via user preferences
    - Use get_enabled_tools() to get only tools from enabled plugins
    - Use get_all_tools() to get all tools regardless of enabled state

    Example::

        registry = MCPToolRegistry.get_instance()

        # Register a plugin
        registry.register_plugin(my_tool_plugin)

        # Initialize (creates tools from all registered plugins)
        registry.initialize()

        # Get tools from enabled plugins only
        tools = registry.get_enabled_tools()

        # When preferences change
        registry.invalidate_cache()
    """

    _instance: MCPToolRegistry | None = None
    _lock = threading.Lock()

    # Preference key for enabled tool plugins list
    ENABLED_PLUGINS_PREF = "enabled_tool_plugins"

    def __init__(self) -> None:
        """Initialize the registry.

        Use get_instance() to get the singleton instance.
        """
        self._tool_plugins: list[MCPToolPlugin] = []
        self._tools: list[Any] = []
        self._tools_by_plugin: dict[str, list[Any]] = {}  # plugin_name -> tools
        self._initialized = False
        self._cached_enabled_tools: list[Any] | None = None
        self._signals: MCPToolRegistrySignals | None = None

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

    @property
    def signals(self) -> MCPToolRegistrySignals:
        """Get the Qt signals object (lazily created).

        Returns:
            MCPToolRegistrySignals instance for connecting to registry events.
        """
        if self._signals is None:
            self._signals = MCPToolRegistrySignals()
        return self._signals

    def register_plugin(self, plugin: MCPToolPlugin) -> None:
        """Register an MCP tool plugin.

        If already initialized, tools are created immediately.
        Otherwise, tools will be created on initialize().

        Args:
            plugin: The MCPToolPlugin instance to register.
        """
        # Check for duplicate registration by plugin name
        existing_names = {p.name for p in self._tool_plugins}
        if plugin.name in existing_names:
            logger.warning(
                "MCP tool plugin '{}' already registered, skipping duplicate",
                plugin.name,
            )
            return

        self._tool_plugins.append(plugin)
        logger.debug("Registered MCP tool plugin: {}", plugin.name)

        if self._initialized:
            # Late registration - create tools immediately
            try:
                tools = plugin.create_tools()
                self._tools.extend(tools)
                self._tools_by_plugin[plugin.name] = tools
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

        # Emit signal (even if not initialized - observers can track registrations)
        if self._signals is not None:
            self._signals.plugin_registered.emit(plugin.name)

    def unregister_plugin(self, name: str) -> bool:
        """Unregister an MCP tool plugin and its tools.

        Removes the plugin and all tools it created from the registry.
        This supports hot-reload of user plugins.

        Args:
            name: Plugin name to unregister.

        Returns:
            True if plugin was found and unregistered.
        """
        # Find and remove the plugin
        plugin_index = None
        for i, plugin in enumerate(self._tool_plugins):
            if plugin.name == name:
                plugin_index = i
                break

        if plugin_index is None:
            return False

        # Remove plugin from list
        self._tool_plugins.pop(plugin_index)

        # Remove its tools if initialized
        if name in self._tools_by_plugin:
            tools_to_remove = self._tools_by_plugin.pop(name)
            # Remove from main tools list
            for tool in tools_to_remove:
                try:
                    self._tools.remove(tool)
                except ValueError:
                    pass  # Tool not in list

            logger.info(
                "Unregistered MCP tool plugin '{}' ({} tools removed)",
                name,
                len(tools_to_remove),
            )
        else:
            logger.debug("Unregistered MCP tool plugin '{}' (no tools created)", name)

        # Emit signal
        if self._signals is not None:
            self._signals.plugin_unregistered.emit(name)

        return True

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
                self._tools_by_plugin[plugin.name] = tools
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
        """Get all registered tools (regardless of enabled state).

        Automatically initializes if not already done.

        Returns:
            List of tool functions from all plugins.
        """
        self.initialize()
        return list(self._tools)

    def get_enabled_tools(self) -> list[Any]:
        """Get tools from enabled plugins only.

        Uses caching for performance. Call invalidate_cache() when
        preferences change.

        Automatically initializes if not already done.

        Returns:
            List of tool functions from enabled plugins.
        """
        self.initialize()

        if self._cached_enabled_tools is not None:
            return list(self._cached_enabled_tools)

        enabled_names = self._get_enabled_plugin_names()
        enabled_tools = []

        for plugin in self._tool_plugins:
            if plugin.name in enabled_names:
                tools = self._tools_by_plugin.get(plugin.name, [])
                enabled_tools.extend(tools)

        self._cached_enabled_tools = enabled_tools
        logger.debug(
            "Collected {} tools from {} enabled plugins",
            len(enabled_tools),
            len(enabled_names),
        )

        return list(enabled_tools)

    def _get_enabled_plugin_names(self) -> set[str]:
        """Get the set of enabled plugin names from preferences.

        Returns:
            Set of plugin names that are enabled.
        """
        try:
            from lucid.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            enabled_list = prefs.get(self.ENABLED_PLUGINS_PREF)

            if enabled_list is None:
                # No preference set - use default enabled state from plugins
                return {
                    plugin.name
                    for plugin in self._tool_plugins
                    if plugin.enabled_by_default
                }

            if isinstance(enabled_list, list):
                return set(enabled_list)

        except Exception as e:
            logger.debug("Could not load enabled plugins preference: {}", e)

        return set()

    def is_plugin_enabled(self, name: str) -> bool:
        """Check if a plugin is enabled.

        Args:
            name: The plugin name.

        Returns:
            True if the plugin is enabled.
        """
        return name in self._get_enabled_plugin_names()

    def invalidate_cache(self) -> None:
        """Invalidate cached enabled tools.

        Call this when preferences change to force recalculation.
        """
        self._cached_enabled_tools = None
        logger.debug("MCP tool registry cache invalidated")

    def get_plugins(self) -> list[MCPToolPlugin]:
        """Get all registered plugins.

        Returns:
            List of MCPToolPlugin instances.
        """
        return list(self._tool_plugins)

    def get_plugin(self, name: str) -> MCPToolPlugin | None:
        """Get a plugin by name.

        Args:
            name: The plugin name.

        Returns:
            The MCPToolPlugin instance or None if not found.
        """
        for plugin in self._tool_plugins:
            if plugin.name == name:
                return plugin
        return None

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

    @property
    def enabled_plugin_count(self) -> int:
        """Get the number of enabled plugins."""
        return len(self._get_enabled_plugin_names())

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry state and plugin information.
        """
        enabled_names = self._get_enabled_plugin_names()

        return {
            "initialized": self._initialized,
            "plugin_count": len(self._tool_plugins),
            "enabled_count": len(enabled_names),
            "tool_count": len(self._tools),
            "plugins": [
                {
                    **plugin.get_introspection_data(),
                    "enabled": plugin.name in enabled_names,
                    "tool_count": len(self._tools_by_plugin.get(plugin.name, [])),
                }
                for plugin in self._tool_plugins
            ],
        }

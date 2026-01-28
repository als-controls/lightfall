"""Controller plugin registry.

Provides a singleton registry for ControllerPlugin instances,
enabling discovery and prioritized selection of device controllers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.controller_plugin import ControllerPlugin
    from lucid.ui.models.device_tree import DeviceTreeItem


class ControllerPluginRegistry:
    """Singleton registry for controller plugins.

    Maintains a collection of registered ControllerPlugin instances and
    provides methods to find applicable controllers for device selections.

    Example:
        >>> from lucid.ui.widgets.controller_registry import ControllerPluginRegistry
        >>> registry = ControllerPluginRegistry.get_instance()
        >>> registry.register(my_controller_plugin)
        >>> matches = registry.get_matching_controllers(selected_items)
        >>> # matches is a list of (plugin, priority) tuples
    """

    _instance: ControllerPluginRegistry | None = None

    def __init__(self) -> None:
        """Initialize the registry."""
        self._plugins: list[ControllerPlugin] = []

    @classmethod
    def get_instance(cls) -> ControllerPluginRegistry:
        """Get the singleton registry instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls._instance = None

    def register(self, plugin: ControllerPlugin) -> None:
        """Register a controller plugin.

        Args:
            plugin: The ControllerPlugin instance to register.
        """
        if plugin not in self._plugins:
            self._plugins.append(plugin)
            logger.debug(
                "Registered controller plugin: {} ({})",
                plugin.name,
                plugin.display_name,
            )

    def unregister(self, plugin: ControllerPlugin) -> None:
        """Unregister a controller plugin.

        Args:
            plugin: The ControllerPlugin instance to unregister.
        """
        if plugin in self._plugins:
            self._plugins.remove(plugin)
            logger.debug("Unregistered controller plugin: {}", plugin.name)

    def get_matching_controllers(
        self, items: list[DeviceTreeItem]
    ) -> list[tuple[ControllerPlugin, int]]:
        """Get all controller plugins that can handle the given items.

        Returns plugins with their priorities, sorted by priority (highest first).

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            List of (plugin, priority) tuples for matching controllers,
            sorted by priority (highest first).
        """
        if not items:
            return []

        matching: list[tuple[ControllerPlugin, int]] = []
        for plugin in self._plugins:
            try:
                priority = plugin.can_control(items)
                if priority is not None:
                    matching.append((plugin, priority))
            except Exception as e:
                logger.warning(
                    "Error checking controller plugin {}: {}",
                    plugin.name,
                    e,
                )

        # Sort by priority (highest first)
        matching.sort(key=lambda x: x[1], reverse=True)
        return matching

    def get_best_controller(
        self, items: list[DeviceTreeItem]
    ) -> ControllerPlugin | None:
        """Get the best (highest priority) matching controller.

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            The best matching controller plugin, or None if no match.
        """
        matching = self.get_matching_controllers(items)
        return matching[0][0] if matching else None

    @property
    def registered_plugins(self) -> list[ControllerPlugin]:
        """Get all registered controller plugins."""
        return list(self._plugins)

    def get_introspection_data(self) -> dict:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry information.
        """
        return {
            "plugin_count": len(self._plugins),
            "plugins": [
                {
                    "name": p.name,
                    "display_name": p.display_name,
                    "class": p.__class__.__name__,
                    "module": p.__class__.__module__,
                }
                for p in self._plugins
            ],
        }

"""Registry for visualization plugins.

VisualizationRegistry is a thread-safe singleton that manages
visualization plugin instances.
"""

from __future__ import annotations

import threading
from typing import Any

from loguru import logger


class VisualizationRegistry:
    """Thread-safe singleton registry for visualization plugins.

    Provides centralized access to all registered visualization plugin instances.

    Thread Safety:
        All methods are protected by an RLock for thread-safe access.

    Example:
        >>> registry = VisualizationRegistry.get_instance()
        >>> registry.register_visualization(my_viz_plugin)
    """

    _instance: VisualizationRegistry | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        """Initialize the registry."""
        self._visualizations: dict[str, Any] = {}
        self._viz_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> VisualizationRegistry:
        """Get the singleton VisualizationRegistry instance.

        Returns:
            The global VisualizationRegistry instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    # === Visualization Registration ===

    def register_visualization(
        self, plugin: Any, replace: bool = False
    ) -> None:
        """Register a visualization plugin.

        Args:
            plugin: The plugin instance to register (must have a .name attribute).
            replace: If True, replace existing plugin with same name.

        Raises:
            ValueError: If plugin already registered and replace=False.
        """
        with self._viz_lock:
            name = plugin.name
            if name in self._visualizations and not replace:
                raise ValueError(
                    f"Visualization '{name}' already registered. "
                    "Use replace=True to override."
                )

            self._visualizations[name] = plugin
            logger.debug("Registered visualization: {}", name)

    def unregister_visualization(self, name: str) -> bool:
        """Unregister a visualization plugin.

        Args:
            name: Name of the plugin to unregister.

        Returns:
            True if plugin was removed, False if not found.
        """
        with self._viz_lock:
            if name in self._visualizations:
                del self._visualizations[name]
                logger.debug("Unregistered visualization: {}", name)
                return True
            return False

    def get_visualization(self, name: str) -> Any | None:
        """Get a visualization plugin by name.

        Args:
            name: Plugin name.

        Returns:
            The plugin or None if not found.
        """
        with self._viz_lock:
            return self._visualizations.get(name)

    def get_all_visualizations(self) -> list[Any]:
        """Get all registered visualization plugins.

        Returns:
            List of all registered plugin instances.
        """
        with self._viz_lock:
            return list(self._visualizations.values())

    def has_visualization(self, name: str) -> bool:
        """Check if a visualization is registered.

        Args:
            name: Plugin name.

        Returns:
            True if registered.
        """
        with self._viz_lock:
            return name in self._visualizations

    # === Introspection ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry information.
        """
        with self._viz_lock:
            return {
                "visualizations": {
                    name: plugin.get_introspection_data()
                    for name, plugin in self._visualizations.items()
                },
                "visualization_count": len(self._visualizations),
            }

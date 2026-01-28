"""Registry for engine plugins.

Provides a central registry for available engine plugins, enabling
engine discovery and selection based on user preferences.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.engine_plugin import EnginePlugin


class EngineRegistry:
    """Registry for available engine plugins.

    Manages engine plugin discovery and provides engine selection
    based on user preferences. This is a singleton that tracks all
    registered engine plugins.

    Example:
        >>> registry = EngineRegistry.get_instance()
        >>> registry.register(BlueskyEnginePlugin())
        >>> engine_plugin = registry.get("bluesky")
        >>> engine = engine_plugin.create_engine()
    """

    _instance: EngineRegistry | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        """Initialize the engine registry."""
        self._engines: dict[str, EnginePlugin] = {}
        self._default_engine: str = "bluesky"

    @classmethod
    def get_instance(cls) -> EngineRegistry:
        """Get the singleton instance.

        Returns:
            The global EngineRegistry instance.
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

    def register(self, plugin: EnginePlugin) -> None:
        """Register an engine plugin.

        Args:
            plugin: The engine plugin to register.
        """
        if plugin.name in self._engines:
            logger.warning(f"Engine '{plugin.name}' already registered, replacing")
        self._engines[plugin.name] = plugin
        logger.debug(f"Registered engine plugin: {plugin.name}")

    def unregister(self, name: str) -> bool:
        """Unregister an engine plugin.

        Args:
            name: Name of the engine to unregister.

        Returns:
            True if the engine was removed, False if not found.
        """
        if name in self._engines:
            del self._engines[name]
            logger.debug(f"Unregistered engine plugin: {name}")
            return True
        return False

    def get(self, name: str) -> EnginePlugin | None:
        """Get an engine plugin by name.

        Args:
            name: The engine identifier.

        Returns:
            The engine plugin or None if not found.
        """
        return self._engines.get(name)

    def get_all(self) -> list[EnginePlugin]:
        """Get all registered engine plugins.

        Returns:
            List of all registered engine plugins.
        """
        return list(self._engines.values())

    def get_names(self) -> list[str]:
        """Get all registered engine names.

        Returns:
            List of engine names.
        """
        return list(self._engines.keys())

    def has(self, name: str) -> bool:
        """Check if an engine is registered.

        Args:
            name: The engine identifier.

        Returns:
            True if the engine is registered.
        """
        return name in self._engines

    @property
    def default_engine(self) -> str:
        """Default engine name.

        This is used when no preference is set.
        """
        return self._default_engine

    @default_engine.setter
    def default_engine(self, name: str) -> None:
        """Set the default engine name.

        Args:
            name: The engine identifier to use as default.
        """
        self._default_engine = name

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry information.
        """
        return {
            "default_engine": self._default_engine,
            "engines": {
                name: plugin.get_introspection_data()
                for name, plugin in self._engines.items()
            },
        }

"""Theme registry for managing registered theme plugins.

ThemeRegistry is a singleton that manages all registered theme plugins.
It provides lookup by name and returns available themes for the UI.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.theme_plugin import ThemeDefinition, ThemePlugin


class ThemeRegistry:
    """Singleton registry for theme plugins.

    The registry manages registered theme plugins and provides:
    - Registration of theme plugins
    - Lookup by theme name
    - List of available themes for UI
    - Default theme selection based on system preference

    Example::

        registry = ThemeRegistry.get_instance()

        # Register a theme
        registry.register(my_theme_plugin)

        # Get a theme by name
        theme = registry.get("slate")

        # Get all themes for UI dropdown
        themes = registry.get_all()

        # Get default theme based on system
        theme = registry.get_theme_for_system(is_dark=True)
    """

    _instance: ThemeRegistry | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        """Initialize the theme registry."""
        self._themes: dict[str, ThemePlugin] = {}

    @classmethod
    def get_instance(cls) -> ThemeRegistry:
        """Get the singleton ThemeRegistry instance."""
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

    def register(self, plugin: ThemePlugin) -> bool:
        """Register a theme plugin.

        Args:
            plugin: The theme plugin to register.

        Returns:
            True if registered successfully, False if name already exists.
        """
        name = plugin.name
        if name in self._themes:
            logger.warning("Theme '{}' already registered, skipping", name)
            return False

        self._themes[name] = plugin
        logger.debug("Registered theme: {} ({})", name, plugin.display_name)
        return True

    def unregister(self, name: str) -> bool:
        """Unregister a theme by name.

        Args:
            name: Theme name to unregister.

        Returns:
            True if unregistered, False if not found.
        """
        if name in self._themes:
            del self._themes[name]
            logger.debug("Unregistered theme: {}", name)
            return True
        return False

    def get(self, name: str) -> ThemePlugin | None:
        """Get a theme plugin by name.

        Args:
            name: The theme name (e.g., "light", "slate").

        Returns:
            The theme plugin or None if not found.
        """
        return self._themes.get(name)

    def get_definition(self, name: str) -> ThemeDefinition | None:
        """Get a theme definition by name.

        Convenience method that returns the ThemeDefinition directly.

        Args:
            name: The theme name.

        Returns:
            The theme definition or None if not found.
        """
        plugin = self.get(name)
        if plugin:
            return plugin.get_theme_definition()
        return None

    def get_all(self) -> list[ThemePlugin]:
        """Get all registered themes.

        Returns:
            List of theme plugins, sorted by display name.
        """
        return sorted(self._themes.values(), key=lambda t: t.display_name)

    def get_names(self) -> list[str]:
        """Get all registered theme names.

        Returns:
            List of theme names.
        """
        return list(self._themes.keys())

    def get_theme_for_system(self, is_dark: bool) -> ThemePlugin | None:
        """Get the default theme based on system preference.

        Args:
            is_dark: True if system is in dark mode.

        Returns:
            The first matching theme (dark or light), or None if no themes registered.
        """
        # Find themes matching the requested mode
        matching = [t for t in self._themes.values() if t.is_dark == is_dark]

        if matching:
            # Prefer "light" or "slate" as defaults, otherwise first match
            for preferred in ("light", "slate"):
                for theme in matching:
                    if theme.name == preferred:
                        return theme
            return matching[0]

        # Fall back to any theme
        if self._themes:
            return next(iter(self._themes.values()))

        return None

    def has_theme(self, name: str) -> bool:
        """Check if a theme is registered.

        Args:
            name: Theme name to check.

        Returns:
            True if the theme exists.
        """
        return name in self._themes

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry information.
        """
        return {
            "registered_themes": [
                {
                    "name": t.name,
                    "display_name": t.display_name,
                    "is_dark": t.is_dark,
                }
                for t in self.get_all()
            ],
            "count": len(self._themes),
        }

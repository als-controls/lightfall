"""Unified controller matcher.

Provides unified matching across ControllerPlugin instances (new plugin system)
and legacy ControlWidgetRegistry classes (decorator-based registration),
maintaining backward compatibility while enabling plugin-based controllers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QWidget

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.controller_plugin import ControllerPlugin
    from lightfall.ui.models.device_tree import DeviceTreeItem
    from lightfall.ui.widgets.base_control import BaseControlWidget


@dataclass
class ControllerMatch:
    """Represents a matching controller for a device selection.

    Encapsulates either a ControllerPlugin or a legacy BaseControlWidget
    class, providing a unified interface for widget creation.

    Attributes:
        name: Unique identifier for this controller.
        display_name: Human-readable name shown in widget selector.
        priority: Priority value (higher = preferred).
        source: Source of this match - "plugin" or "legacy".
        plugin: The ControllerPlugin instance (if source is "plugin").
        widget_class: The widget class (if source is "legacy").
    """

    name: str
    display_name: str
    priority: int
    source: str  # "plugin" or "legacy"
    plugin: ControllerPlugin | None = field(default=None)
    widget_class: type[BaseControlWidget] | None = field(default=None)

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create a widget instance for this controller.

        Args:
            parent: Parent widget.

        Returns:
            A new widget instance.

        Raises:
            ValueError: If neither plugin nor widget_class is set.
        """
        if self.plugin is not None:
            return self.plugin.create_widget(parent)
        elif self.widget_class is not None:
            return self.widget_class(parent)
        else:
            raise ValueError(
                f"ControllerMatch '{self.name}' has no plugin or widget_class"
            )

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with match information.
        """
        data = {
            "name": self.name,
            "display_name": self.display_name,
            "priority": self.priority,
            "source": self.source,
        }
        if self.plugin:
            data["plugin_class"] = self.plugin.__class__.__name__
            data["plugin_module"] = self.plugin.__class__.__module__
        if self.widget_class:
            data["widget_class"] = self.widget_class.__name__
            data["widget_module"] = self.widget_class.__module__
        return data


class ControllerMatcher:
    """Unified matching across plugin and legacy controllers.

    Queries both the ControllerPluginRegistry (new plugin system) and
    ControlWidgetRegistry (legacy decorator-based system), returning
    ControllerMatch objects that provide a unified interface.

    Example:
        >>> matcher = ControllerMatcher.get_instance()
        >>> matches = matcher.get_matching_controllers(selected_items)
        >>> for match in matches:
        ...     print(f"{match.display_name} (priority={match.priority})")
        >>> # Create widget from best match
        >>> if matches:
        ...     widget = matches[0].create_widget(parent)
    """

    _instance: ControllerMatcher | None = None

    def __init__(self) -> None:
        """Initialize the matcher."""
        pass

    @classmethod
    def get_instance(cls) -> ControllerMatcher:
        """Get the singleton matcher instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls._instance = None

    def get_matching_controllers(
        self, items: list[DeviceTreeItem]
    ) -> list[ControllerMatch]:
        """Get all matching controllers from both plugin and legacy systems.

        Returns ControllerMatch objects sorted by priority (highest first).

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            List of ControllerMatch objects for all matching controllers,
            sorted by priority (highest first).
        """
        if not items:
            return []

        matches: list[ControllerMatch] = []

        # Query plugin registry
        matches.extend(self._get_plugin_matches(items))

        # Query legacy registry
        matches.extend(self._get_legacy_matches(items))

        # Sort by priority (highest first)
        matches.sort(key=lambda m: m.priority, reverse=True)

        logger.debug(
            "Found {} matching controllers for {} item(s)",
            len(matches),
            len(items),
        )

        return matches

    def _get_plugin_matches(
        self, items: list[DeviceTreeItem]
    ) -> list[ControllerMatch]:
        """Get matches from ControllerPluginRegistry.

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            List of ControllerMatch objects from plugin registry.
        """
        try:
            from lightfall.ui.widgets.controller_registry import ControllerPluginRegistry

            registry = ControllerPluginRegistry.get_instance()
            plugin_matches = registry.get_matching_controllers(items)

            return [
                ControllerMatch(
                    name=plugin.name,
                    display_name=plugin.display_name,
                    priority=priority,
                    source="plugin",
                    plugin=plugin,
                )
                for plugin, priority in plugin_matches
            ]
        except ImportError:
            logger.debug("ControllerPluginRegistry not available")
            return []

    def _get_legacy_matches(
        self, items: list[DeviceTreeItem]
    ) -> list[ControllerMatch]:
        """Get matches from legacy ControlWidgetRegistry.

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            List of ControllerMatch objects from legacy registry.
        """
        try:
            from lightfall.ui.widgets.base_control import ControlWidgetRegistry

            registry = ControlWidgetRegistry.get_instance()
            widget_classes = registry.get_matching_widgets(items)

            return [
                ControllerMatch(
                    name=widget_class.__name__,
                    display_name=widget_class.display_name,
                    priority=widget_class.priority,
                    source="legacy",
                    widget_class=widget_class,
                )
                for widget_class in widget_classes
            ]
        except ImportError:
            logger.debug("ControlWidgetRegistry not available")
            return []

    def get_best_controller(
        self, items: list[DeviceTreeItem]
    ) -> ControllerMatch | None:
        """Get the best (highest priority) matching controller.

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            The best matching ControllerMatch, or None if no match.
        """
        matches = self.get_matching_controllers(items)
        return matches[0] if matches else None

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with matcher information.
        """
        # Get info from both registries
        plugin_info: dict[str, Any] = {"available": False}
        legacy_info: dict[str, Any] = {"available": False}

        try:
            from lightfall.ui.widgets.controller_registry import ControllerPluginRegistry

            registry = ControllerPluginRegistry.get_instance()
            plugin_info = {
                "available": True,
                **registry.get_introspection_data(),
            }
        except ImportError:
            pass

        try:
            from lightfall.ui.widgets.base_control import ControlWidgetRegistry

            registry = ControlWidgetRegistry.get_instance()
            legacy_info = {
                "available": True,
                "widget_count": len(registry.registered_widgets),
                "widgets": [
                    {
                        "class": w.__name__,
                        "display_name": w.display_name,
                        "priority": w.priority,
                    }
                    for w in registry.registered_widgets
                ],
            }
        except ImportError:
            pass

        return {
            "plugin_registry": plugin_info,
            "legacy_registry": legacy_info,
        }

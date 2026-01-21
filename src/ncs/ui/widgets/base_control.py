"""Base classes for device control widgets.

Provides the foundation for device-specific control UIs with:
- Abstract base class defining the control widget interface
- Registry for discovering and selecting appropriate control widgets
- Priority-based widget selection for handling device types
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ncs.utils.logging import logger

if TYPE_CHECKING:
    from ncs.ui.models.device_tree import DeviceTreeItem


class BaseControlWidget(QWidget):
    """Abstract base class for device control widgets.

    Control widgets provide interactive UIs for controlling devices.
    They are shown in the DevicePanel when matching device(s) are selected.

    Subclasses must implement:
    - `can_control()`: Class method to determine if widget handles given items
    - `set_items()`: Update the controlled devices
    - `display_name`: Human-readable name for widget selector

    Class Attributes:
        display_name: Name shown in widget selector dropdown.
        priority: Higher priority widgets are preferred when multiple match.
            Default priorities:
            - 100: Exact device class match
            - 50: Device category match
            - 10: Generic fallback

    Signals:
        control_error: Emitted when a control action fails (error message).
        motion_started: Emitted when device motion begins (device name).
        motion_finished: Emitted when device motion completes (device name).

    Example:
        >>> class MyMotorControl(BaseControlWidget):
        ...     display_name = "Motor Control"
        ...     priority = 100
        ...
        ...     @classmethod
        ...     def can_control(cls, items):
        ...         return all(is_motor(item) for item in items)
        ...
        ...     def set_items(self, items):
        ...         self._motors = items
        ...         self._update_ui()
    """

    display_name: ClassVar[str] = "Device Control"
    priority: ClassVar[int] = 10

    # Signals
    control_error = Signal(str)  # error message
    motion_started = Signal(str)  # device name
    motion_finished = Signal(str)  # device name

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the control widget.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._items: list[DeviceTreeItem] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the widget UI.

        Override in subclasses to build the control UI.
        """
        pass

    @classmethod
    def can_control(cls, items: list[DeviceTreeItem]) -> bool:
        """Check if this widget can control the given items.

        This is called by the factory to determine which widgets
        are applicable for the current selection.

        Subclasses must override this method.

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            True if this widget can control all the given items.
        """
        raise NotImplementedError("Subclasses must implement can_control()")

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the items to control.

        Called when the selection changes. The widget should update
        its UI to reflect the new items.

        Subclasses must override this method.

        Args:
            items: List of DeviceTreeItems to control.
        """
        raise NotImplementedError("Subclasses must implement set_items()")

    @property
    def items(self) -> list[DeviceTreeItem]:
        """Get the currently controlled items."""
        return self._items

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with control widget information.
        """
        return {
            "widget_type": self.__class__.__name__,
            "display_name": self.display_name,
            "priority": self.priority,
            "item_count": len(self._items),
            "items": [
                {"name": item.name, "type": item.node_type.value}
                for item in self._items
            ],
        }


class ControlWidgetRegistry:
    """Registry for device control widgets.

    Maintains a collection of available control widget classes and
    provides methods to find appropriate widgets for device selections.

    The registry uses a singleton pattern for global access.

    Example:
        >>> registry = ControlWidgetRegistry.get_instance()
        >>> registry.register(MotorControlWidget)
        >>> widgets = registry.get_matching_widgets(selected_items)
        >>> best_widget_class = widgets[0] if widgets else None
    """

    _instance: ControlWidgetRegistry | None = None

    def __init__(self) -> None:
        """Initialize the registry."""
        self._widgets: list[type[BaseControlWidget]] = []

    @classmethod
    def get_instance(cls) -> ControlWidgetRegistry:
        """Get the singleton registry instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls._instance = None

    def register(self, widget_class: type[BaseControlWidget]) -> None:
        """Register a control widget class.

        Args:
            widget_class: The widget class to register.
        """
        if widget_class not in self._widgets:
            self._widgets.append(widget_class)
            logger.debug(
                "Registered control widget: {} (priority={})",
                widget_class.display_name,
                widget_class.priority,
            )

    def unregister(self, widget_class: type[BaseControlWidget]) -> None:
        """Unregister a control widget class.

        Args:
            widget_class: The widget class to unregister.
        """
        if widget_class in self._widgets:
            self._widgets.remove(widget_class)
            logger.debug("Unregistered control widget: {}", widget_class.display_name)

    def get_matching_widgets(
        self, items: list[DeviceTreeItem]
    ) -> list[type[BaseControlWidget]]:
        """Get all widget classes that can control the given items.

        Returns widgets sorted by priority (highest first).

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            List of matching widget classes, sorted by priority.
        """
        if not items:
            return []

        matching = []
        for widget_class in self._widgets:
            try:
                if widget_class.can_control(items):
                    matching.append(widget_class)
            except Exception as e:
                logger.warning(
                    "Error checking widget {}: {}",
                    widget_class.display_name,
                    e,
                )

        # Sort by priority (highest first)
        matching.sort(key=lambda w: w.priority, reverse=True)
        return matching

    def get_best_widget(
        self, items: list[DeviceTreeItem]
    ) -> type[BaseControlWidget] | None:
        """Get the best (highest priority) matching widget.

        Args:
            items: List of selected DeviceTreeItems.

        Returns:
            The best matching widget class, or None if no match.
        """
        matching = self.get_matching_widgets(items)
        return matching[0] if matching else None

    @property
    def registered_widgets(self) -> list[type[BaseControlWidget]]:
        """Get all registered widget classes."""
        return list(self._widgets)


def register_control_widget(cls: type[BaseControlWidget]) -> type[BaseControlWidget]:
    """Decorator to register a control widget class.

    Example:
        >>> @register_control_widget
        ... class MyControlWidget(BaseControlWidget):
        ...     display_name = "My Control"
        ...     ...
    """
    ControlWidgetRegistry.get_instance().register(cls)
    return cls

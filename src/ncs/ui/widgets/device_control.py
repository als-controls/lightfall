"""Device control container widget.

Provides the main container that displays appropriate control widgets
based on the current device selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ncs.ui.widgets.base_control import BaseControlWidget, ControlWidgetRegistry
from ncs.utils.logging import logger

if TYPE_CHECKING:
    from ncs.ui.models.device_tree import DeviceTreeItem


class NoSelectionWidget(QWidget):
    """Placeholder widget shown when no devices are selected."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch()

        label = QLabel("Select a device to see controls")
        label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(label)

        layout.addStretch()


class NoControlWidget(QWidget):
    """Widget shown when no control is available for the selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch()

        self._message = QLabel("No control available for this selection")
        self._message.setStyleSheet("color: #888; font-style: italic;")
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        layout.addStretch()

    def set_message(self, message: str) -> None:
        """Set the message to display."""
        self._message.setText(message)


class DeviceControlWidget(QWidget):
    """Container widget that shows appropriate controls for device selection.

    DeviceControlWidget manages:
    - Widget selection based on device type(s)
    - Widget selector dropdown when multiple options available
    - Dynamic widget switching as selection changes

    Signals:
        control_error: Propagated from child control widgets.
        widget_changed: Emitted when the active control widget changes.
    """

    control_error = Signal(str)
    widget_changed = Signal(str)  # widget display name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[DeviceTreeItem] = []
        self._current_widget: BaseControlWidget | None = None
        self._matching_widgets: list[type[BaseControlWidget]] = []
        self._widget_instances: dict[str, BaseControlWidget] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the container UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Widget selector (hidden when only one option)
        self._selector_layout = QHBoxLayout()
        self._selector_label = QLabel("Control:")
        self._selector_combo = QComboBox()
        self._selector_combo.currentIndexChanged.connect(self._on_widget_selected)
        self._selector_layout.addWidget(self._selector_label)
        self._selector_layout.addWidget(self._selector_combo)
        self._selector_layout.addStretch()
        layout.addLayout(self._selector_layout)

        # Hide selector initially
        self._selector_label.hide()
        self._selector_combo.hide()

        # Stacked widget to hold control widgets
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Add default widgets
        self._no_selection = NoSelectionWidget()
        self._stack.addWidget(self._no_selection)

        self._no_control = NoControlWidget()
        self._stack.addWidget(self._no_control)

        # Start with no selection
        self._stack.setCurrentWidget(self._no_selection)

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the selected items and update the control widget.

        Args:
            items: List of selected DeviceTreeItems.
        """
        self._items = items

        if not items:
            self._show_no_selection()
            return

        # Find matching widgets
        registry = ControlWidgetRegistry.get_instance()
        self._matching_widgets = registry.get_matching_widgets(items)

        if not self._matching_widgets:
            self._show_no_control(items)
            return

        # Update selector combo
        self._update_selector()

        # Show the best (highest priority) widget
        self._activate_widget(self._matching_widgets[0])

    def _show_no_selection(self) -> None:
        """Show the no-selection placeholder."""
        self._matching_widgets = []
        self._current_widget = None
        self._hide_selector()
        self._stack.setCurrentWidget(self._no_selection)

    def _show_no_control(self, items: list[DeviceTreeItem]) -> None:
        """Show the no-control message."""
        self._matching_widgets = []
        self._current_widget = None
        self._hide_selector()

        # Build informative message
        if len(items) == 1:
            item = items[0]
            msg = f"No control available for {item.name} ({item.node_type.value})"
        else:
            names = ", ".join(item.name for item in items[:3])
            if len(items) > 3:
                names += f", ... ({len(items)} total)"
            msg = f"No common control available for: {names}"

        self._no_control.set_message(msg)
        self._stack.setCurrentWidget(self._no_control)

    def _update_selector(self) -> None:
        """Update the widget selector combo box."""
        if len(self._matching_widgets) <= 1:
            self._hide_selector()
            return

        # Show selector with options
        self._selector_combo.blockSignals(True)
        self._selector_combo.clear()

        for widget_class in self._matching_widgets:
            self._selector_combo.addItem(
                widget_class.display_name,
                widget_class,
            )

        self._selector_combo.blockSignals(False)
        self._show_selector()

    def _show_selector(self) -> None:
        """Show the widget selector."""
        self._selector_label.show()
        self._selector_combo.show()

    def _hide_selector(self) -> None:
        """Hide the widget selector."""
        self._selector_label.hide()
        self._selector_combo.hide()

    def _activate_widget(self, widget_class: type[BaseControlWidget]) -> None:
        """Activate a control widget for the current selection.

        Args:
            widget_class: The widget class to activate.
        """
        # Get or create widget instance
        class_name = widget_class.__name__
        if class_name not in self._widget_instances:
            widget = widget_class(self)
            widget.control_error.connect(self.control_error)
            self._widget_instances[class_name] = widget
            self._stack.addWidget(widget)

        widget = self._widget_instances[class_name]

        # Set items and show
        widget.set_items(self._items)
        self._current_widget = widget
        self._stack.setCurrentWidget(widget)

        logger.debug(
            "Activated control widget: {} for {} item(s)",
            widget_class.display_name,
            len(self._items),
        )
        self.widget_changed.emit(widget_class.display_name)

    @Slot(int)
    def _on_widget_selected(self, index: int) -> None:
        """Handle widget selection from combo box."""
        if index < 0 or index >= len(self._matching_widgets):
            return

        widget_class = self._matching_widgets[index]
        self._activate_widget(widget_class)

    @property
    def current_widget(self) -> BaseControlWidget | None:
        """Get the currently active control widget."""
        return self._current_widget

    @property
    def items(self) -> list[DeviceTreeItem]:
        """Get the currently controlled items."""
        return self._items

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        data = {
            "item_count": len(self._items),
            "matching_widget_count": len(self._matching_widgets),
            "matching_widgets": [w.display_name for w in self._matching_widgets],
            "current_widget": None,
        }

        if self._current_widget:
            data["current_widget"] = self._current_widget.get_introspection_data()

        return data


class ControlWidgetFactory:
    """Factory for creating control widgets.

    Provides convenience methods for creating appropriate control
    widgets based on device selection.

    This is mainly useful for programmatic widget creation outside
    of the DeviceControlWidget container.
    """

    @staticmethod
    def create_for_items(
        items: list[DeviceTreeItem],
        parent: QWidget | None = None,
    ) -> BaseControlWidget | None:
        """Create the best control widget for the given items.

        Args:
            items: List of DeviceTreeItems to control.
            parent: Parent widget.

        Returns:
            A control widget instance, or None if no widget matches.
        """
        registry = ControlWidgetRegistry.get_instance()
        widget_class = registry.get_best_widget(items)

        if widget_class is None:
            return None

        widget = widget_class(parent)
        widget.set_items(items)
        return widget

    @staticmethod
    def get_available_widgets(
        items: list[DeviceTreeItem],
    ) -> list[type[BaseControlWidget]]:
        """Get all control widgets that can handle the given items.

        Args:
            items: List of DeviceTreeItems.

        Returns:
            List of matching widget classes, sorted by priority.
        """
        registry = ControlWidgetRegistry.get_instance()
        return registry.get_matching_widgets(items)

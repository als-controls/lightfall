"""Status bar plugin type for status bar indicators.

StatusBarPlugin is the plugin type for status bar indicators. Plugins
implementing this interface provide widgets that appear in the main
window's status bar.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from lucid.plugins.types import PluginType

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


@dataclass
class StatusBarPluginMetadata:
    """Metadata for a status bar plugin.

    Attributes:
        id: Unique identifier (e.g., "lucid.statusbar.user").
        name: Human-readable display name.
        description: Description of what the indicator shows.
        priority: Sort order (lower = further left in status bar).
        position: Where to add the widget ("left", "right", "permanent").
        tooltip: Default tooltip text.
    """

    id: str
    name: str
    description: str = ""
    priority: int = 100
    position: str = "permanent"  # left, right, permanent
    tooltip: str = ""


class StatusBarPlugin(PluginType):
    """Abstract base for status bar indicator plugins.

    Status bar plugins provide indicator widgets that can be discovered
    and displayed in the main window's status bar. Each plugin creates
    a widget showing some aspect of application state.

    Class Attributes:
        type_name: "statusbar" - identifies this as a statusbar plugin.
        is_singleton: True - status bar plugins are singletons.
        metadata: Class-level metadata about this indicator.

    Lifecycle:
        1. Plugin is instantiated on load
        2. StatusBarManager calls create_widget() to get the widget
        3. connect_signals() is called to wire up state change handlers
        4. update() is called to set initial state
        5. During runtime, signals trigger update() calls
        6. On cleanup, disconnect_signals() is called

    Example implementation::

        class MyStatusPlugin(StatusBarPlugin):
            metadata = StatusBarPluginMetadata(
                id="lucid.statusbar.my_status",
                name="My Status",
                priority=50,
            )

            @property
            def name(self) -> str:
                return "my_status"

            def create_widget(self, parent=None) -> QWidget:
                self._label = QLabel(parent)
                return self._label

            def update(self) -> None:
                self._label.setText("Status: OK")

            def connect_signals(self) -> None:
                self._service.changed.connect(self.update)

            def disconnect_signals(self) -> None:
                self._service.changed.disconnect(self.update)
    """

    type_name: ClassVar[str] = "statusbar"
    description: ClassVar[str] = "Status bar indicator plugin"
    is_singleton: ClassVar[bool] = True

    # Subclasses must define this
    metadata: ClassVar[StatusBarPluginMetadata]

    def __init__(self) -> None:
        """Initialize the status bar plugin."""
        self._widget: QWidget | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this status bar plugin.

        This should be unique within the statusbar type and is used to
        identify the plugin in the registry.
        """
        ...

    @abstractmethod
    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the status bar indicator widget.

        This is called once when the StatusBarManager initializes.
        The returned widget is added to the status bar.

        Args:
            parent: Parent widget (the status bar).

        Returns:
            A QWidget to display in the status bar.
        """
        ...

    @abstractmethod
    def update(self) -> None:
        """Update the widget display based on current state.

        Called after create_widget() and whenever the underlying
        state changes. Should read current state and update the
        widget accordingly.
        """
        ...

    @abstractmethod
    def connect_signals(self) -> None:
        """Connect to service signals for state change notifications.

        Called after create_widget(). Should connect to relevant
        service signals that will trigger update() calls.
        """
        ...

    @abstractmethod
    def disconnect_signals(self) -> None:
        """Disconnect from service signals.

        Called during cleanup. Should disconnect all signals that
        were connected in connect_signals().
        """
        ...

    @property
    def widget(self) -> QWidget | None:
        """Get the created widget, if any.

        Returns:
            The widget created by create_widget(), or None.
        """
        return self._widget

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with status bar plugin information.
        """
        meta = self.metadata
        data = {
            "type": self.type_name,
            "name": self.name,
            "id": meta.id,
            "display_name": meta.name,
            "description": meta.description,
            "priority": meta.priority,
            "position": meta.position,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

        # Include current widget state if available
        if self._widget is not None:
            from PySide6.QtWidgets import QLabel

            if isinstance(self._widget, QLabel):
                data["current_text"] = self._widget.text()
                data["tooltip"] = self._widget.toolTip()

        return data

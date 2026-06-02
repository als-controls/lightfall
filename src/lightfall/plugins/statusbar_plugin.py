"""Status bar plugin type for status bar indicators.

StatusBarPlugin is the plugin type for status bar indicators. Plugins
implementing this interface provide widgets that appear in the main
window's status bar.

The base class provides a default flat-button widget and small helpers
for the common cases (set text/tooltip/color, hide self) so subclasses
typically only implement ``update()``, ``connect_signals()``,
``disconnect_signals()`` and optionally ``on_clicked()``.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QToolButton

from lightfall.plugins.types import PluginType

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


@dataclass
class StatusBarPluginMetadata:
    """Metadata for a status bar plugin.

    Attributes:
        id: Unique identifier (e.g., "lightfall.statusbar.user").
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


class _StatusBarSignals(QObject):
    """QObject host for plugin signals (StatusBarPlugin is not a QObject)."""

    visibility_changed = Signal(bool)


class StatusBarPlugin(PluginType):
    """Abstract base for status bar indicator plugins.

    Default behaviour: each plugin renders as a flat ``QToolButton`` so
    every entry looks consistent and signals clickability. Subclasses
    drive their display by overriding ``update()`` and calling the
    ``set_text`` / ``set_tooltip`` / ``set_color`` helpers. Override
    ``on_clicked()`` to react to button clicks.

    Subclasses that need a more complex widget (e.g. anchoring an
    overlay) can override ``create_widget()`` — they must then also
    assign ``self._widget`` themselves.

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

    Example::

        class MyStatusPlugin(StatusBarPlugin):
            metadata = StatusBarPluginMetadata(
                id="lightfall.statusbar.my_status",
                name="My Status",
                priority=50,
            )

            @property
            def name(self) -> str:
                return "my_status"

            def update(self) -> None:
                self.set_text("OK")
                self.set_tooltip("Everything is fine")

            def connect_signals(self) -> None:
                self._service.changed.connect(self.update)

            def disconnect_signals(self) -> None:
                self._service.changed.disconnect(self.update)

            def on_clicked(self) -> None:
                open_my_status_dialog()
    """

    type_name: ClassVar[str] = "statusbar"
    is_singleton: ClassVar[bool] = True

    # Subclasses must define this
    metadata: ClassVar[StatusBarPluginMetadata]

    def __init__(self) -> None:
        """Initialize the status bar plugin."""
        self._widget: QWidget | None = None
        self._button: QToolButton | None = None
        self._signals = _StatusBarSignals()
        self._visible: bool = True

    @property
    def description(self) -> str:
        """Human-readable description of this status bar plugin."""
        return "Status bar indicator plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this status bar plugin.

        This should be unique within the statusbar type and is used to
        identify the plugin in the registry.
        """
        ...

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the status bar indicator widget.

        Default implementation returns a flat ``QToolButton`` whose
        ``clicked`` signal is wired to :meth:`on_clicked`. Subclasses
        that want a different widget should override this and assign
        ``self._widget`` themselves; they are responsible for any click
        wiring in that case.

        Args:
            parent: Parent widget (the status bar container).

        Returns:
            A QWidget to display in the status bar.
        """
        button = QToolButton(parent)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.metadata.tooltip:
            button.setToolTip(self.metadata.tooltip)
        button.clicked.connect(self.on_clicked)
        self._button = button
        self._widget = button
        return button

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

    def on_clicked(self) -> None:
        """Handle a button click. Default is a no-op."""

    @property
    def widget(self) -> QWidget | None:
        """Get the created widget, if any.

        Returns:
            The widget created by create_widget(), or None.
        """
        return self._widget

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    @property
    def visibility_changed(self) -> Signal:
        """Signal emitted with the new visibility (bool) when toggled."""
        return self._signals.visibility_changed

    @property
    def is_visible(self) -> bool:
        """Whether the plugin's widget is currently shown."""
        return self._visible

    def set_visible(self, visible: bool) -> None:
        """Show or hide this plugin's widget in the status bar.

        Hidden widgets are removed from the layout flow (no reserved
        slot, no extra separator), so neighbouring plugins close up.
        """
        if self._visible == visible:
            return
        self._visible = visible
        if self._widget is not None:
            self._widget.setVisible(visible)
        self._signals.visibility_changed.emit(visible)

    # ------------------------------------------------------------------
    # Display helpers for subclasses (default-widget path)
    # ------------------------------------------------------------------

    def set_text(self, text: str) -> None:
        """Set the displayed text on the default button widget."""
        if self._button is not None:
            self._button.setText(text)

    def set_tooltip(self, tooltip: str) -> None:
        """Set the tooltip on the default button widget."""
        if self._button is not None:
            self._button.setToolTip(tooltip)
        elif self._widget is not None:
            self._widget.setToolTip(tooltip)

    def set_color(self, color: str | None) -> None:
        """Apply a foreground color to the default button widget.

        Pass a CSS color string (e.g. theme ``colors.success``) or
        ``None`` to clear styling.
        """
        if self._button is None:
            return
        if color:
            self._button.setStyleSheet(f"QToolButton {{ color: {color}; }}")
        else:
            self._button.setStyleSheet("")

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with status bar plugin information.
        """
        meta = self.metadata
        data: dict[str, Any] = {
            "type": self.type_name,
            "name": self.name,
            "id": meta.id,
            "display_name": meta.name,
            "description": meta.description,
            "priority": meta.priority,
            "position": meta.position,
            "is_visible": self._visible,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

        if self._button is not None:
            data["current_text"] = self._button.text()
            data["tooltip"] = self._button.toolTip()
        elif self._widget is not None:
            from PySide6.QtWidgets import QLabel

            if isinstance(self._widget, QLabel):
                data["current_text"] = self._widget.text()
                data["tooltip"] = self._widget.toolTip()

        return data

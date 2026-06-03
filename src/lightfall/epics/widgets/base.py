"""
Base class for all EPICS PySide6 widgets.

Provides common functionality for PV connection, value display/editing,
and introspection support for Claude MCP tools.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from PySide6.QtCore import Property, QEvent, Signal, Slot
from PySide6.QtWidgets import QWidget

from lightfall.epics.widgets.style import WidgetStyles


class EpicsWidget(QWidget):
    """
    Abstract base class for widgets that interact with EPICS PVs.

    This class provides:
    - Automatic PV connection management
    - Common signal/slot interface for value updates
    - Introspection API for Claude MCP tools
    - Consistent styling for connection states

    Subclasses must implement:
    - _update_display(): Update the widget display from current PV value
    - _get_widget_value(): Get the current value from the widget
    - _set_widget_value(): Set the widget to display a specific value

    Attributes:
        pv_name: The EPICS PV name this widget is connected to.
        connected: Whether the PV is currently connected.
        readonly: If True, widget only displays values (no editing).

    Signals:
        value_changed: Emitted when the displayed value changes.
        connection_changed: Emitted when PV connection state changes.
        pv_name_changed: Emitted when the PV name is changed.

    Class Attributes:
        widget_type: A human-readable type name for introspection.
        widget_description: A description of what this widget does.

    Example:
        >>> class MyWidget(EpicsWidget):
        ...     widget_type = "MyWidget"
        ...     widget_description = "Displays a PV value as text"
    """

    # Class-level metadata for introspection
    widget_type: ClassVar[str] = "EpicsWidget"
    widget_description: ClassVar[str] = "Base class for EPICS widgets"

    # Signals
    value_changed = Signal(object)
    connection_changed = Signal(bool)
    pv_name_changed = Signal(str)

    def __init__(
        self,
        pv_name: str = "",
        parent: QWidget | None = None,
        readonly: bool = False,
    ) -> None:
        """
        Initialize the EPICS widget.

        Args:
            pv_name: The EPICS PV name to connect to.
            parent: Optional Qt parent widget.
            readonly: If True, the widget only displays values.
        """
        super().__init__(parent)
        self._pv_name = pv_name
        self._readonly = readonly
        self._pv = None
        self._value: Any = None
        self._connected = False

        # Set object name for easier identification in widget tree
        if pv_name:
            self.setObjectName(f"{self.widget_type}_{pv_name}")
        self.setToolTip(pv_name)

        # Apply base styling
        self._update_connection_style()

    # Qt Properties for Designer integration and introspection

    @Property(str, notify=pv_name_changed)
    def pv_name(self) -> str:
        """The EPICS PV name this widget is bound to."""
        return self._pv_name

    @pv_name.setter
    def pv_name(self, name: str) -> None:
        if name != self._pv_name:
            self._disconnect_pv()
            self._pv_name = name
            self.setObjectName(f"{self.widget_type}_{name}")
            self.setToolTip(name)
            self.pv_name_changed.emit(name)
            if name:
                self._connect_pv()

    @Property(bool)
    def readonly(self) -> bool:
        """Whether this widget is read-only."""
        return self._readonly

    @readonly.setter
    def readonly(self, value: bool) -> None:
        self._readonly = value
        self._update_readonly_state()

    @Property(bool, notify=connection_changed)
    def connected(self) -> bool:
        """Whether the PV is currently connected."""
        return self._connected

    # PV Connection Management

    def _connect_pv(self) -> None:
        """
        Establish connection to the configured PV.
        """
        if not self._pv_name:
            return

        from lightfall.epics.ca.pv import PV

        self._pv = PV(self._pv_name, parent=self)
        self._pv.value_changed.connect(self._on_pv_value_changed)
        self._pv.connection_changed.connect(self._on_pv_connection_changed)
        self._pv.metadata_changed.connect(self._on_pv_metadata_changed)
        self._pv.connect_pv()

    def _disconnect_pv(self) -> None:
        """
        Disconnect from the current PV.
        """
        if self._pv is not None:
            self._pv.disconnect_pv()
            self._pv.deleteLater()
            self._pv = None
            self._connected = False
            self._update_connection_style()

    @Slot(object)
    def _on_pv_value_changed(self, value: Any) -> None:
        """
        Handle PV value change from subscription.

        Args:
            value: The new PV value.
        """
        self._value = value
        self._update_display()
        self.value_changed.emit(value)

    @Slot(bool)
    def _on_pv_connection_changed(self, connected: bool) -> None:
        """
        Handle PV connection state change.

        Args:
            connected: The new connection state.
        """
        self._connected = connected
        self._update_connection_style()
        self._update_readonly_state()
        self.connection_changed.emit(connected)

    @Slot(dict)
    def _on_pv_metadata_changed(self, metadata: dict[str, Any]) -> None:
        """
        Handle PV metadata change.

        Subclasses can override this to use metadata (units, limits, etc).

        Args:
            metadata: Dictionary of PV metadata.
        """
        pass

    def _update_connection_style(self) -> None:
        """
        Update widget styling based on connection state.

        Override in subclasses for custom connection styling.
        """
        if self._connected:
            self.setStyleSheet(WidgetStyles.connected())
        else:
            self.setStyleSheet(WidgetStyles.disconnected())

    def _update_readonly_state(self) -> None:
        """
        Update widget state based on readonly property.

        Override in subclasses to disable editing when readonly.
        """
        pass

    # Abstract methods for subclasses

    @abstractmethod
    def _update_display(self) -> None:
        """
        Update the widget display to reflect the current PV value.

        Subclasses must implement this to update their specific UI elements.
        """
        pass

    @abstractmethod
    def _get_widget_value(self) -> Any:
        """
        Get the current value from the widget's UI.

        Returns:
            The value as displayed/entered in the widget.
        """
        pass

    @abstractmethod
    def _set_widget_value(self, value: Any) -> None:
        """
        Set the widget's UI to display a specific value.

        Args:
            value: The value to display.
        """
        pass

    # Value editing

    def write_value(self, value: Any | None = None) -> None:
        """
        Write a value to the PV.

        Args:
            value: The value to write. If None, uses the current widget value.

        Raises:
            RuntimeError: If the widget is readonly or PV is not connected.
        """
        if self._readonly:
            raise RuntimeError("Widget is readonly")
        if self._pv is None or not self._connected:
            raise RuntimeError("PV is not connected")

        if value is None:
            value = self._get_widget_value()
        self._pv.put(value)

    # Introspection API for Claude MCP tools

    def get_introspection_data(self) -> dict[str, Any]:
        """
        Get comprehensive introspection data for this widget.

        This method is specifically designed to support Claude MCP tools
        that inspect the application's widget tree. It provides all
        relevant information about the widget in a structured format
        that is easy for Claude to understand and reason about.

        The returned data includes:
        - Widget type and description
        - PV name and connection status
        - Current value
        - Widget state (readonly, enabled, visible)
        - PV metadata if available
        - Child widget information

        Returns:
            Dictionary with comprehensive widget information.

        Example:
            >>> widget = PVLabel("MY:PV")
            >>> data = widget.get_introspection_data()
            >>> print(data["widget_type"])
            "PVLabel"
        """
        data = {
            # Widget identity
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "object_name": self.objectName(),
            "class_name": self.__class__.__name__,

            # PV information
            "pv_name": self._pv_name,
            "connected": self._connected,
            "current_value": self._value,
            "value_type": type(self._value).__name__ if self._value is not None else None,

            # Widget state
            "readonly": self._readonly,
            "enabled": self.isEnabled(),
            "visible": self.isVisible(),

            # Geometry
            "geometry": {
                "x": self.x(),
                "y": self.y(),
                "width": self.width(),
                "height": self.height(),
            },
        }

        # Add PV metadata if available
        if self._pv is not None:
            data["pv_metadata"] = self._pv.metadata

        # Add widget-specific data from subclasses
        data.update(self._get_specific_introspection_data())

        return data

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """
        Get widget-specific introspection data.

        Override in subclasses to add additional information specific
        to that widget type.

        Returns:
            Dictionary with widget-specific information.
        """
        return {}

    @classmethod
    def get_class_introspection_data(cls) -> dict[str, Any]:
        """
        Get class-level introspection data.

        This provides information about the widget class itself,
        useful for understanding what types of widgets are available.

        Returns:
            Dictionary with class information.
        """
        return {
            "widget_type": cls.widget_type,
            "widget_description": cls.widget_description,
            "class_name": cls.__name__,
            "module": cls.__module__,
        }

    # Tooltip forwarding from children
    #
    # Qt shows a tooltip only on the hovered widget. Inner children
    # (QLabel, QLineEdit, ...) sit on top of us and do not always
    # propagate their (empty) ToolTip events back to us, so we install
    # ourselves as an event filter on every child and answer with the
    # PV-name tooltip when the child has none of its own.

    def childEvent(self, event) -> None:
        super().childEvent(event)
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if (
            event.type() == QEvent.Type.ToolTip
            and isinstance(obj, QWidget)
            and obj is not self
            and not obj.toolTip()
        ):
            tip = self.toolTip()
            if tip:
                from PySide6.QtWidgets import QToolTip

                try:
                    pos = event.globalPos()
                except AttributeError:
                    pos = event.globalPosition().toPoint()
                QToolTip.showText(pos, tip, obj)
                return True
        return super().eventFilter(obj, event)

    # Lifecycle

    def showEvent(self, event) -> None:
        """Connect to PV when widget is shown."""
        super().showEvent(event)
        if self._pv is None and self._pv_name:
            self._connect_pv()

    def hideEvent(self, event) -> None:
        """Optionally disconnect when hidden (can be overridden)."""
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        """Clean up PV connection on close."""
        self._disconnect_pv()
        super().closeEvent(event)

"""Custom Qt events for cross-panel communication.

This module defines custom QEvent subclasses that enable decoupled
communication between panels without direct signal connections.

Usage:
    # Post an event to a specific widget
    from PySide6.QtCore import QCoreApplication
    from lucid.ui.events import DeviceFocusEvent

    event = DeviceFocusEvent(device_id="motor1")
    QCoreApplication.postEvent(target_widget, event)

    # Handle in receiving widget by overriding event()
    def event(self, event: QEvent) -> bool:
        if event.type() == DeviceFocusEvent.EventType:
            self._handle_device_focus(event.device_id)
            return True
        return super().event(event)
"""

from __future__ import annotations

from PySide6.QtCore import QEvent


class DeviceFocusEvent(QEvent):
    """Event requesting a panel to focus on a specific device.

    Posted when a device is selected in one panel and should be
    highlighted/focused in other panels (e.g., Devices -> Synoptic).

    Attributes:
        device_id: The unique identifier of the device to focus.
        device_name: Optional human-readable name for logging.
    """

    # Register a unique event type ID
    EventType: int = QEvent.registerEventType()

    def __init__(self, device_id: str, device_name: str | None = None) -> None:
        """Initialize the device focus event.

        Args:
            device_id: Unique device identifier.
            device_name: Optional device name for display/logging.
        """
        super().__init__(QEvent.Type(self.EventType))
        self.device_id = device_id
        self.device_name = device_name


class DeviceSelectEvent(QEvent):
    """Event requesting a panel to select (but not focus) a device.

    Similar to DeviceFocusEvent but doesn't imply camera movement
    or view changes - just selection state update.

    Attributes:
        device_id: The unique identifier of the device to select.
        add_to_selection: Whether to add to existing selection or replace.
    """

    EventType: int = QEvent.registerEventType()

    def __init__(self, device_id: str, add_to_selection: bool = False) -> None:
        """Initialize the device select event.

        Args:
            device_id: Unique device identifier.
            add_to_selection: If True, add to selection; otherwise replace.
        """
        super().__init__(QEvent.Type(self.EventType))
        self.device_id = device_id
        self.add_to_selection = add_to_selection

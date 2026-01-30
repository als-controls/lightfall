# StatusBarPlugin

Status bar plugins add indicator widgets to the main window's status bar.

## Purpose

Use `StatusBarPlugin` when you want to:
- Display real-time status information
- Show connection states or system health
- Provide quick access to state changes

## Base Class

```python
from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"statusbar"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |
| `metadata` | `StatusBarPluginMetadata` | Plugin metadata (required) |

## StatusBarPluginMetadata

Every status bar plugin must define class-level metadata:

```python
@dataclass
class StatusBarPluginMetadata:
    id: str              # Unique identifier (e.g., "lucid.statusbar.user")
    name: str            # Human-readable display name
    description: str = ""  # Description of what it shows
    priority: int = 100    # Sort order (lower = further left)
    position: str = "permanent"  # "left", "right", or "permanent"
    tooltip: str = ""      # Default tooltip text
```

## Required Methods

### name (property)

Unique identifier for this plugin.

```python
@property
def name(self) -> str:
    return "my_status"
```

### create_widget(parent)

Create the status bar widget.

```python
def create_widget(self, parent: QWidget | None = None) -> QWidget:
    """Create the status bar indicator widget.

    Args:
        parent: Parent widget (the status bar).

    Returns:
        QWidget to display in the status bar.
    """
    label = QLabel("Status", parent)
    self._widget = label
    return label
```

### update()

Update the widget based on current state.

```python
def update(self) -> None:
    """Update the widget display."""
    if self._widget:
        self._widget.setText(self._get_current_status())
```

### connect_signals()

Connect to service signals for state changes.

```python
def connect_signals(self) -> None:
    """Connect to state change signals."""
    self._service.state_changed.connect(self.update)
```

### disconnect_signals()

Disconnect signals during cleanup.

```python
def disconnect_signals(self) -> None:
    """Disconnect from signals."""
    self._service.state_changed.disconnect(self.update)
```

## Lifecycle

1. Plugin is instantiated on load
2. `StatusBarManager` calls `create_widget()` to get the widget
3. `connect_signals()` is called to wire up state handlers
4. `update()` is called to set initial state
5. During runtime, signals trigger `update()` calls
6. On cleanup, `disconnect_signals()` is called

## Complete Example

```python
"""Device connection status indicator."""

from PySide6.QtWidgets import QLabel, QWidget

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


class DeviceConnectionStatus(StatusBarPlugin):
    """Status bar indicator showing device connection state."""

    metadata = StatusBarPluginMetadata(
        id="my_package.statusbar.device_connection",
        name="Device Connection",
        description="Shows whether devices are connected",
        priority=50,  # Appears towards the left
        position="permanent",
    )

    def __init__(self) -> None:
        super().__init__()
        self._label: QLabel | None = None
        self._device_manager = None

    @property
    def name(self) -> str:
        return "device_connection"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        self._label = QLabel(parent)
        self._label.setMinimumWidth(100)
        self._widget = self._label
        return self._label

    def update(self) -> None:
        """Update the connection status display."""
        if not self._label or not self._device_manager:
            return

        connected = self._device_manager.connected_count
        total = self._device_manager.total_count

        if connected == total:
            self._label.setText(f"Devices: {total} connected")
            self._label.setStyleSheet("color: green;")
        elif connected == 0:
            self._label.setText(f"Devices: {total} disconnected")
            self._label.setStyleSheet("color: red;")
        else:
            self._label.setText(f"Devices: {connected}/{total}")
            self._label.setStyleSheet("color: orange;")

        self._label.setToolTip(
            f"Connected: {connected}\n"
            f"Disconnected: {total - connected}"
        )

    def connect_signals(self) -> None:
        """Connect to device manager signals."""
        from lucid.devices.manager import DeviceManager

        self._device_manager = DeviceManager.get_instance()
        self._device_manager.connection_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        """Disconnect from device manager."""
        if self._device_manager:
            try:
                self._device_manager.connection_changed.disconnect(self.update)
            except RuntimeError:
                pass  # Already disconnected
```

## Clickable Status Indicator

```python
"""Clickable status indicator that opens a dialog."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


class ClickableStatusPlugin(StatusBarPlugin):
    """Status indicator that shows details on click."""

    metadata = StatusBarPluginMetadata(
        id="my_package.statusbar.clickable",
        name="System Status",
        priority=10,
    )

    def __init__(self) -> None:
        super().__init__()
        self._label: QLabel | None = None

    @property
    def name(self) -> str:
        return "clickable_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        self._label = QLabel("Status: OK", parent)
        self._label.setCursor(Qt.PointingHandCursor)
        self._label.mousePressEvent = self._on_click
        self._widget = self._label
        return self._label

    def _on_click(self, event):
        """Handle click to show details dialog."""
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            self._label,
            "System Status",
            "All systems operational.",
        )

    def update(self) -> None:
        pass  # Static display

    def connect_signals(self) -> None:
        pass  # No dynamic updates

    def disconnect_signals(self) -> None:
        pass
```

## Icon-based Status

```python
"""Status indicator using icons."""

from PySide6.QtWidgets import QLabel, QWidget
import qtawesome as qta

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


class IconStatusPlugin(StatusBarPlugin):
    """Status indicator with icon."""

    metadata = StatusBarPluginMetadata(
        id="my_package.statusbar.icon_status",
        name="Network Status",
        priority=30,
    )

    def __init__(self) -> None:
        super().__init__()
        self._label: QLabel | None = None

    @property
    def name(self) -> str:
        return "icon_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        self._label = QLabel(parent)
        self._widget = self._label
        return self._label

    def update(self) -> None:
        if not self._label:
            return

        # Use qtawesome icons
        connected = self._check_connection()

        if connected:
            icon = qta.icon("mdi.wifi", color="green")
            tooltip = "Network: Connected"
        else:
            icon = qta.icon("mdi.wifi-off", color="red")
            tooltip = "Network: Disconnected"

        self._label.setPixmap(icon.pixmap(16, 16))
        self._label.setToolTip(tooltip)

    def _check_connection(self) -> bool:
        # Check network connection
        return True

    def connect_signals(self) -> None:
        pass  # Could connect to network monitor

    def disconnect_signals(self) -> None:
        pass
```

## Registration

```python
PluginEntry(
    type_name="statusbar",
    name="device_connection",
    import_path="my_package.statusbar:DeviceConnectionStatus",
),
```

## Position Options

| Position | Description |
|----------|-------------|
| `"left"` | Added to the left side of status bar |
| `"right"` | Added to the right side |
| `"permanent"` | Permanent widget (always visible) |

## Priority Guidelines

Lower priority values appear further left:

| Priority Range | Typical Use |
|----------------|-------------|
| 0-25 | Critical system status |
| 25-50 | Connection/authentication |
| 50-75 | Feature status |
| 75-100 | Informational |
| 100+ | Low priority |

## Built-in Status Plugins

LUCID includes these status bar plugins:

| Plugin | Description |
|--------|-------------|
| `user_status` | Current user information |
| `auth_status` | Authentication state |
| `connection_status` | Network connection |
| `tiled_status` | Tiled data service status |

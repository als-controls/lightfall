# StatusBarPlugin

Status bar plugins add indicator widgets to the main window's status bar.

## Purpose

Use `StatusBarPlugin` when you want to:
- Display real-time status information
- Show connection states or system health
- Provide a quick click-through to detail dialogs / external pages

## Base Class

```python
from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
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
    id: str              # Unique identifier (e.g., "lightfall.statusbar.user")
    name: str            # Human-readable display name
    description: str = ""  # Description of what it shows
    priority: int = 100    # Sort order (lower = further left)
    position: str = "permanent"  # "left", "right", or "permanent"
    tooltip: str = ""      # Default tooltip text
```

## Default Widget

The base class provides a default widget: a flat ``QToolButton`` whose
``clicked`` signal is wired to ``on_clicked()``. The button shares a
single horizontal layout with neighbouring plugins (no Qt separator
notches between items), so every entry looks consistent and signals
clickability. Plugins typically just override ``update()``,
``connect_signals()``, ``disconnect_signals()``, and (optionally)
``on_clicked()``.

For complex needs (anchoring a popup, embedding non-button widgets),
override ``create_widget()``; you are then responsible for setting
``self._widget`` and any click wiring.

## Required Methods

### name (property)

Unique identifier for this plugin.

```python
@property
def name(self) -> str:
    return "my_status"
```

### update()

Update the displayed state. Use the inherited helpers to drive the
default button:

```python
def update(self) -> None:
    self.set_text("OK")
    self.set_tooltip("Everything is fine")
    self.set_color(self._theme.colors.success)
```

### connect_signals() / disconnect_signals()

Wire up (and tear down) the service signals that drive ``update()``.

```python
def connect_signals(self) -> None:
    self._service.state_changed.connect(self.update)

def disconnect_signals(self) -> None:
    try:
        self._service.state_changed.disconnect(self.update)
    except RuntimeError:
        pass
```

## Optional Methods

### on_clicked()

Override to react to a button click. Default is a no-op.

```python
def on_clicked(self) -> None:
    QDesktopServices.openUrl(QUrl("https://example.com"))
```

### create_widget(parent)

Override only when the default button is not enough (e.g., custom popup
anchor). You must assign ``self._widget`` and ``self._button`` (if
applicable) yourself.

## Helpers

| Helper | Purpose |
|--------|---------|
| `set_text(str)` | Set the button label text |
| `set_tooltip(str)` | Set the tooltip |
| `set_color(str \| None)` | Apply a CSS color string (`None` clears) |
| `set_visible(bool)` | Show or hide the plugin's widget in the status bar |
| `is_visible` | Current visibility flag |
| `visibility_changed` | Qt signal emitted with the new visibility |

When a plugin calls ``set_visible(False)`` the widget is hidden and the
side container's layout collapses around it — no leftover gap, no
trailing separator.

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

from typing import ClassVar

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.ui.theme import ThemeManager


class DeviceConnectionStatus(StatusBarPlugin):
    """Status bar indicator showing device connection state."""

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="my_package.statusbar.device_connection",
        name="Device Connection",
        description="Shows whether devices are connected",
        priority=50,
        position="permanent",
    )

    def __init__(self) -> None:
        super().__init__()
        self._device_manager = None
        self._theme: ThemeManager | None = None

    @property
    def name(self) -> str:
        return "device_connection"

    def update(self) -> None:
        if not self._device_manager:
            return
        if self._theme is None:
            self._theme = ThemeManager.get_instance()
        colors = self._theme.colors

        connected = self._device_manager.connected_count
        total = self._device_manager.total_count

        if connected == total:
            self.set_text(f"Devices: {total} connected")
            self.set_color(colors.success)
        elif connected == 0:
            self.set_text(f"Devices: {total} disconnected")
            self.set_color(colors.error)
        else:
            self.set_text(f"Devices: {connected}/{total}")
            self.set_color(colors.warning)

        self.set_tooltip(
            f"Connected: {connected}\n"
            f"Disconnected: {total - connected}"
        )

    def connect_signals(self) -> None:
        from lightfall.devices.manager import DeviceManager

        self._device_manager = DeviceManager.get_instance()
        self._device_manager.connection_changed.connect(self.update)

    def disconnect_signals(self) -> None:
        if self._device_manager:
            try:
                self._device_manager.connection_changed.disconnect(self.update)
            except RuntimeError:
                pass
```

## Clickable Indicator

```python
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


class ExternalLinkStatus(StatusBarPlugin):
    metadata = StatusBarPluginMetadata(
        id="my_package.statusbar.docs",
        name="Docs",
        priority=80,
    )

    @property
    def name(self) -> str:
        return "docs_link"

    def update(self) -> None:
        self.set_text("Docs")
        self.set_tooltip("Open documentation")

    def connect_signals(self) -> None:
        pass

    def disconnect_signals(self) -> None:
        pass

    def on_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl("https://example.com/docs"))
```

## Custom Widget (icon-based, popup anchor, etc.)

Override ``create_widget`` only when the default button is not enough.

```python
import qtawesome as qta
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QToolButton, QWidget

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


class IconStatusPlugin(StatusBarPlugin):
    metadata = StatusBarPluginMetadata(
        id="my_package.statusbar.icon",
        name="Network",
        priority=30,
    )

    @property
    def name(self) -> str:
        return "icon_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        button = QToolButton(parent)
        button.setAutoRaise(True)
        button.setIcon(qta.icon("mdi.wifi"))
        button.setIconSize(QSize(14, 14))
        button.clicked.connect(self.on_clicked)
        self._button = button
        self._widget = button
        return button

    def update(self) -> None:
        ...

    def connect_signals(self) -> None:
        ...

    def disconnect_signals(self) -> None:
        ...
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
| `"left"` | Routed to the left-side container (`QStatusBar.addWidget`) |
| `"right"` | Routed to the right-side container (`QStatusBar.addPermanentWidget`) |
| `"permanent"` | Same as `"right"` (default) |

Within each side, plugins are sorted by ``priority`` (lower = further
left). A small uniform gap separates adjacent items; there are no
inter-item separator notches and no trailing separator.

## Priority Guidelines

Lower priority values appear further left:

| Priority Range | Typical Use |
|----------------|-------------|
| 0-25 | Critical system status |
| 25-50 | Connection/authentication |
| 50-75 | Feature status |
| 75-100 | Informational |
| 100+ | Low priority |

## Hiding a Plugin

Plugins can hide themselves when there is nothing to show:

```python
def update(self) -> None:
    if not self._has_anything_to_say():
        self.set_visible(False)
        return
    self.set_visible(True)
    self.set_text(self._render())
```

Hidden plugins are removed from the layout flow — the surrounding items
close up cleanly, with no leftover gap or trailing separator.

## Built-in Status Plugins

Lightfall includes these status bar plugins:

| Plugin | Description |
|--------|-------------|
| `thread_status` | Background tasks and scan progress (hides when idle) |
| `user_status` | Current user information |
| `auth_status` | Authentication state |
| `connection_status` | Network connection |
| `tiled_status` | Tiled data service status |
| `als_beam_status` | ALS synchrotron beam current and availability |

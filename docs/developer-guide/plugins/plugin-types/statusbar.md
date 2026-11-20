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

## Style Rules

Every indicator in the bar looks the same way: a **qtawesome icon** plus a
short label, both tinted with a **theme color role**. One call does all of it:

```python
self.set_state("3 queued", icon="mdi6.snail", role="warning", tooltip="...")
```

- **Icons are qtawesome names** passed as `icon=`. Never put unicode symbols
  or emoji in the label (no `⬆ 2 updates`, `⏳ 3 tasks`, `✓ done`). They
  render inconsistently across fonts, can't be tinted, and ignore the theme.
- **Colors are theme roles** passed as `role=`, never hex/CSS literals. A raw
  color stays fixed when the user switches theme; a role is re-applied.
- **Never touch `ThemeManager` for the indicator itself.** The base class
  re-renders the last `set_state()` when the theme changes.

| Role | Use for |
|------|---------|
| `"success"` | Connected / healthy / active |
| `"warning"` | Degraded, pending, needs attention soon |
| `"error"` | Down, failed, blocked |
| `"info"` | Neutral news (updates available, work in progress) |
| `"text_secondary"` | Idle, off, not configured |
| `None` (default) | Plain text color |
| `"text"`, `"primary"`, `"secondary"` | Rarely needed |

Browse icon names with `qta-browser` (installed with qtawesome); Lightfall
uses the `mdi6.` set.

Enforcement: at runtime, a label containing symbol characters, a raw
`set_color()`, an unknown role, or a bad icon name logs a warning once per
plugin; the bar is never broken by it. For plugins in this repo,
`tests/ui/statusbar/test_statusbar_style.py` fails on the same patterns.

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
``clicked`` signal is wired to ``on_clicked()``. It shows icon-only, text-only
or icon-beside-text depending on what ``set_state()`` was given. The button
shares a single horizontal layout with neighbouring plugins, so every entry
looks consistent and signals clickability. Plugins typically just override
``update()``, ``connect_signals()``, ``disconnect_signals()``, and
(optionally) ``on_clicked()``.

## Required Methods

### name (property)

Unique identifier for this plugin.

```python
@property
def name(self) -> str:
    return "my_status"
```

### update()

Read the current state and show it with ``set_state()``:

```python
def update(self) -> None:
    if self._service.ok:
        self.set_state("", icon="mdi6.database", role="success",
                       tooltip="Connected")
    else:
        self.set_state("Error", icon="mdi6.database", role="error",
                       tooltip=self._service.error)
```

An empty label gives an icon-only indicator; let the icon and color carry
the meaning when the state is the normal one.

### connect_signals() / disconnect_signals()

Wire up (and tear down) the service signals that drive ``update()``. Do not
connect to the theme here; the base class handles theme changes.

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

### on_theme_changed()

Called when the theme's colors change. The default re-renders the last
``set_state()``, which is all a default-widget plugin needs. Override it only
to restyle a custom widget (call ``super().on_theme_changed()`` too if you
also use ``set_state()``).

### create_widget(parent)

Override only when the default button is not enough (e.g. embedding
non-button widgets). Prefer calling ``super().create_widget(parent)`` and
adding to it (as ``thread_status`` does for its popup overlay). If you build
your own widget, assign ``self._widget`` (and ``self._button`` if it is a
button) yourself, read colors from ``self.theme_colors`` /
``self.theme_color(role)``, and override ``on_theme_changed()`` to restyle.

## Helpers

| Helper | Purpose |
|--------|---------|
| `set_state(text, icon=, role=, tooltip=)` | Show a label with a qtawesome icon, tinted with a theme role. **Use this.** |
| `set_tooltip(str)` | Set the tooltip on its own |
| `set_visible(bool)` | Show or hide the plugin's widget in the status bar |
| `theme_colors` | The current theme's `ThemeColors` (for custom widgets) |
| `theme_color(role)` | The current color for a role (for custom widgets) |
| `is_visible` | Current visibility flag |
| `visibility_changed` | Qt signal emitted with the new visibility |
| `set_text(str)` | Low-level: label only. Prefer `set_state` |
| `set_icon(QIcon)` | Low-level: not re-tinted on theme change. Prefer `set_state` |
| `set_color(str)` | **Deprecated**: raw CSS color, logs a warning. Use `role=` |

When a plugin calls ``set_visible(False)`` the widget is hidden and the
side container's layout collapses around it — no leftover gap, no
trailing separator.

## Lifecycle

1. Plugin is instantiated on load
2. `StatusBarManager` calls `create_widget()` to get the widget, and
   subscribes the plugin to theme changes
3. `connect_signals()` is called to wire up state handlers
4. `update()` is called to set initial state
5. During runtime, signals trigger `update()` calls; theme changes call
   `on_theme_changed()`
6. On cleanup, `disconnect_signals()` is called and the theme subscription
   is dropped

## Complete Example

```python
"""Device connection status indicator."""

from typing import ClassVar

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


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

    @property
    def name(self) -> str:
        return "device_connection"

    def update(self) -> None:
        if not self._device_manager:
            return
        connected = self._device_manager.connected_count
        total = self._device_manager.total_count
        tooltip = f"Connected: {connected}\nDisconnected: {total - connected}"

        if connected == total:
            self.set_state("", icon="mdi6.lan-connect", role="success", tooltip=tooltip)
        elif connected == 0:
            self.set_state(f"{total} down", icon="mdi6.lan-disconnect", role="error",
                           tooltip=tooltip)
        else:
            self.set_state(f"{connected}/{total}", icon="mdi6.lan-pending", role="warning",
                           tooltip=tooltip)

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
        self.set_state("Docs", icon="mdi6.book-open-variant",
                       tooltip="Open documentation")

    def connect_signals(self) -> None:
        pass

    def disconnect_signals(self) -> None:
        pass

    def on_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl("https://example.com/docs"))
```

## Custom Widget (popup anchor, etc.)

Extend the default button rather than replacing it, so ``set_state()`` and
theming keep working:

```python
from PySide6.QtWidgets import QFrame, QWidget

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata


class PopupStatusPlugin(StatusBarPlugin):
    metadata = StatusBarPluginMetadata(id="my_package.statusbar.popup", name="Jobs")

    @property
    def name(self) -> str:
        return "popup_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        button = super().create_widget(parent)
        self._popup = QFrame()
        self._restyle_popup()
        return button

    def on_theme_changed(self) -> None:
        super().on_theme_changed()  # re-render the button's set_state
        self._restyle_popup()

    def _restyle_popup(self) -> None:
        self._popup.setStyleSheet(
            f"QFrame {{ border: 1px solid {self.theme_color('text_secondary')}; }}")

    def update(self) -> None:
        self.set_state("2 jobs", icon="mdi6.progress-clock", role="info")

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
    self.set_state(self._render(), icon="mdi6.bell", role="info")
```

Hidden plugins are removed from the layout flow — the surrounding items
close up cleanly, with no leftover gap or trailing separator.

## Built-in Status Plugins

Lightfall includes these status bar plugins:

| Plugin | Description |
|--------|-------------|
| `thread_status` | Background tasks and scan progress (hides when idle) |
| `user_status` | Current user and authentication state |
| `nats_status` | NATS IPC bus connection and peers |
| `tiled_status` | Tiled data service status |
| `caproxy_lease_status` | Caproxy attested-lease state |
| `als_beam_status` | ALS synchrotron beam current and availability |
| `shussebora_status` | Data-movement daemon health |
| `git_state_status` | Source checkouts on a feature branch or with local changes |
| `updates_status` | Available Lightfall updates / restart pending |
| `memory_status` | Developer indicator: process memory and pending garbage |

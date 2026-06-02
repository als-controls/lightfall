# Lightfall Panel Design API Reference

This document provides the full API reference for designing BasePanel subclasses for the Lightfall application.

## Required Imports

```python
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QSplitter, QScrollArea, QFrame, QSizePolicy,
)
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry
from lightfall.utils.logging import logger
```

## user-plugin-architecture

Lightfall has two distinct ways to create panel plugins. Understanding which to use is critical.

### User Plugins (~/lightfall/plugins/)

**This is what YOU should use.** User plugins directly subclass `BasePanel`:

```python
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry

class MyPanel(BasePanel):
    panel_metadata = PanelMetadata(id="lightfall.panels.user.my_panel", ...)
    def _setup_ui(self) -> None: ...

# Self-register at module load time
PanelRegistry.get_instance().register(MyPanel, replace=True)
```

Key points:
- Direct `BasePanel` subclass
- Self-registers with `PanelRegistry.register()`
- Tracked by `UserPluginService` via `RegistrationTracker`
- Hot-reload supported

### Built-in Plugins (via manifest)

Built-in plugins use a **two-layer pattern** you should NOT copy:

```python
# File: lightfall/ui/panels/plugins/device_plugin.py
class DevicePanelPlugin(PanelPlugin):  # <-- Plugin wrapper
    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.device_panel import DevicePanel
        return DevicePanel

# File: lightfall/ui/panels/device_panel.py
class DevicePanel(BasePanel):  # <-- Actual panel
    panel_metadata = PanelMetadata(...)
```

Why the two layers?
- **Lazy loading**: `PanelPlugin` is instantiated at startup, but the heavy `BasePanel` import is deferred until `get_panel_class()` is called
- **Plugin system integration**: `PanelPlugin` goes through `PluginLoader` → `PluginRegistry` → `PanelRegistry`
- **Qt independence**: The plugin system doesn't depend on Qt

### Do NOT Use PanelPlugin for User Plugins

❌ **Wrong** - Using PanelPlugin for user plugins:
```python
class MyPanelPlugin(PanelPlugin):  # DON'T DO THIS
    def get_panel_class(self):
        return MyPanel
```

✅ **Correct** - Direct BasePanel with self-registration:
```python
class MyPanel(BasePanel):  # DO THIS
    panel_metadata = PanelMetadata(...)

PanelRegistry.get_instance().register(MyPanel, replace=True)
```

`PanelPlugin` is NOT tracked by `UserPluginService` and will NOT work correctly for user plugins.

## panel-metadata

```python
@dataclass
class PanelMetadata:
    id: str                              # Unique ID, e.g., "lightfall.panels.user.my_panel"
    name: str                            # Display name in menus
    description: str = ""                # Tooltip/help text
    icon: str = ""                       # Icon name (mdi.icon-name or path)
    category: str = "General"            # Menu grouping: "User", "Data", "Devices", etc.
    required_permission: Permission | None = None  # Access control (usually None)
    singleton: bool = True               # Only one instance allowed
    closable: bool = True                # User can close the panel
    keywords: list[str] = field(default_factory=list)  # Search keywords

    # Docking preferences
    default_area: str = "left"           # "left" or "bottom" (gets sidebar button + title bar)
                                         # "center" (always visible, no sidebar button)
    sidebar_group: str = "top"           # "top", "bottom" within sidebar
    auto_hide: bool = True               # Start in auto-hide sidebar
    sidebar_order: int = 0               # Order within group (lower = higher)
```

## lifecycle-methods

### Required: `_setup_ui()`

Build your UI here. Called during `__init__` after layout is created.

```python
def _setup_ui(self) -> None:
    """Build the panel's user interface."""
    # self._layout is already a QVBoxLayout with no margins
    self.label = QLabel("Hello!")
    self._layout.addWidget(self.label)
```

### Optional Hooks

```python
def _on_activated(self) -> None:
    """Called when panel becomes active/focused."""
    pass

def _on_deactivated(self) -> None:
    """Called when panel loses focus."""
    pass

def _on_closing(self) -> None:
    """Called when panel is about to close."""
    pass

def can_close(self, force: bool = False) -> bool:
    """Return False to prevent closing (e.g., unsaved work)."""
    return True
```

## signals

```python
class BasePanel(QWidget):
    activated = Signal()           # Panel became active
    deactivated = Signal()         # Panel lost focus
    state_changed = Signal(str, object)  # key, value - state changed
    closing = Signal()             # Panel is closing
```

## state-management

```python
# Get/set panel state (survives restart if persisted)
value = self.get_state("key", default_value)
self.set_state("key", value)

# Get all state as dict
all_state = self.get_all_state()

# Restore from dict
self.restore_state(state_dict)
```

## mcp-introspection-api

Override these to expose panel-specific data and actions to Claude:

```python
def _get_specific_introspection_data(self) -> dict[str, Any]:
    """Return panel-specific data for MCP tools."""
    return {
        "current_value": self._value,
        "item_count": len(self._items),
    }

def _get_available_actions(self) -> list[dict[str, Any]]:
    """Return actions Claude can invoke."""
    actions = super()._get_available_actions()
    actions.extend([
        {
            "name": "refresh",
            "description": "Refresh the data",
            "method": "action_refresh",
        },
        {
            "name": "set_filter",
            "description": "Set the filter text",
            "method": "action_set_filter",
            "parameters": {"filter_text": "string"},
        },
    ])
    return actions

def action_refresh(self, **kwargs) -> bool:
    """Custom action handler for 'refresh'."""
    self._load_data()
    return True

def action_set_filter(self, filter_text: str = "", **kwargs) -> bool:
    """Custom action handler for 'set_filter'."""
    self._filter_input.setText(filter_text)
    return True
```

## self-registration-pattern

User plugins MUST self-register at module load time:

```python
# At the END of your plugin file:
PanelRegistry.get_instance().register(MyPanel, replace=True)
```

The `replace=True` is REQUIRED for hot-reload support.

## complete-example

```python
"""My custom panel for Lightfall."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout, QWidget,
)
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry
from lightfall.utils.logging import logger


class MyCustomPanel(BasePanel):
    """A custom panel that shows a greeting."""

    panel_metadata = PanelMetadata(
        id="lightfall.panels.user.my_custom",
        name="My Custom Panel",
        description="A simple panel showing a personalized greeting",
        icon="mdi.hand-wave",
        category="User",
        keywords=["greeting", "hello", "custom"],
    )

    # Custom signal
    name_changed = Signal(str)

    def _setup_ui(self) -> None:
        """Build the panel UI."""
        # Name input row
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("Name:"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Enter your name")
        self._name_input.textChanged.connect(self._update_greeting)
        input_row.addWidget(self._name_input)

        # Greet button
        self._greet_btn = QPushButton("Greet")
        self._greet_btn.clicked.connect(self._on_greet_clicked)
        input_row.addWidget(self._greet_btn)

        # Greeting label
        self._greeting_label = QLabel("Hello!")
        self._greeting_label.setStyleSheet("font-size: 18px; padding: 10px;")

        # Add to layout
        self._layout.addLayout(input_row)
        self._layout.addWidget(self._greeting_label)
        self._layout.addStretch()

        # Restore state
        saved_name = self.get_state("name", "")
        if saved_name:
            self._name_input.setText(saved_name)

    def _update_greeting(self, name: str) -> None:
        """Update greeting when name changes."""
        if name:
            self._greeting_label.setText(f"Hello, {name}!")
        else:
            self._greeting_label.setText("Hello!")
        self.set_state("name", name)
        self.name_changed.emit(name)

    def _on_greet_clicked(self) -> None:
        """Handle greet button click."""
        name = self._name_input.text() or "World"
        logger.info("Greeting: {}", name)

    def _on_activated(self) -> None:
        """Focus the name input when panel is activated."""
        self._name_input.setFocus()

    # MCP introspection

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Return panel-specific data."""
        return {
            "current_name": self._name_input.text(),
            "greeting_text": self._greeting_label.text(),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Return available actions."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "set_name",
                "description": "Set the name for greeting",
                "method": "action_set_name",
                "parameters": {"name": "string"},
            },
            {
                "name": "greet",
                "description": "Trigger the greet button",
                "method": "action_greet",
            },
        ])
        return actions

    def action_set_name(self, name: str = "", **kwargs) -> bool:
        """MCP action: Set the name."""
        self._name_input.setText(name)
        return True

    def action_greet(self, **kwargs) -> bool:
        """MCP action: Click the greet button."""
        self._on_greet_clicked()
        return True


# Self-register with the panel registry (REQUIRED for user plugins)
PanelRegistry.get_instance().register(MyCustomPanel, replace=True)
```

## file-conventions

User plugins are stored in: `~/lightfall/plugins/`

Naming conventions:
- Filename: lowercase with underscores (e.g., `my_panel.py`)
- Panel ID: `lightfall.panels.user.<name>` (e.g., `lightfall.panels.user.my_panel`)
- Class name: PascalCase ending in Panel (e.g., `MyPanel`)

## qt-layout-tips

```python
# Vertical layout (stack widgets top to bottom)
layout = QVBoxLayout()

# Horizontal layout (stack widgets left to right)
layout = QHBoxLayout()

# Add stretch to push widgets together
layout.addStretch()

# Set margins (left, top, right, bottom)
layout.setContentsMargins(10, 10, 10, 10)

# Set spacing between widgets
layout.setSpacing(5)

# Group widgets in a titled box
group = QGroupBox("Settings")
group_layout = QVBoxLayout(group)
group_layout.addWidget(widget)

# Splitter for resizable sections
splitter = QSplitter(Qt.Orientation.Horizontal)
splitter.addWidget(left_panel)
splitter.addWidget(right_panel)

# Scrollable area
scroll = QScrollArea()
scroll.setWidgetResizable(True)
scroll.setWidget(content_widget)
```

## common-widgets

```python
# Label with text
label = QLabel("Text")

# Single-line text input
line_edit = QLineEdit()
line_edit.setPlaceholderText("Hint...")
line_edit.textChanged.connect(handler)

# Integer spinner
spin = QSpinBox()
spin.setRange(0, 100)
spin.setValue(10)
spin.valueChanged.connect(handler)

# Float spinner
double_spin = QDoubleSpinBox()
double_spin.setRange(0.0, 10.0)
double_spin.setDecimals(3)
double_spin.setValue(1.0)

# Dropdown
combo = QComboBox()
combo.addItems(["Option 1", "Option 2"])
combo.currentTextChanged.connect(handler)

# Checkbox
check = QCheckBox("Enable feature")
check.setChecked(True)
check.stateChanged.connect(handler)

# Button
btn = QPushButton("Click Me")
btn.clicked.connect(handler)
```

## compact-motor-widget

For simple motor controls in a panel, use `CompactMotorWidget` — a single-row
widget with status dot, readback, jog/abs toggle, setpoint entry, go, and stop.
It handles ophyd subscription, units, and motion state internally.

```python
from lightfall.ui.widgets.compact_motor import CompactMotorWidget
from lightfall.devices import DeviceCatalog

catalog = DeviceCatalog.get_instance()
info = catalog.get_device_by_name("sample_x")
widget = CompactMotorWidget(device_info=info, ophyd_obj=info.ophyd_device)
self._layout.addWidget(widget)
```

The `ophyd_obj` may be `None` initially; call `widget.set_motor(...)` once
the device finishes connecting (e.g. from `DeviceCatalog.device_connected`).

## lightfall-services

```python
# Toast notifications
from lightfall.ui.toast import ToastManager
toast = ToastManager.get_instance()
toast.success("Title", "Message")
toast.error("Title", "Message")

# Theme awareness
from lightfall.ui.theme import ThemeManager
theme_mgr = ThemeManager.get_instance()
is_dark = theme_mgr.is_dark

# Preferences
from lightfall.ui.preferences.manager import PreferencesManager
prefs = PreferencesManager.get_instance()
value = prefs.get("key", default)
prefs.set("key", value)

# Device catalog
from lightfall.devices import DeviceCatalog
from lightfall.devices.model import DeviceCategory

catalog = DeviceCatalog.get_instance()
# list_devices returns DeviceInfo objects
motor_infos = catalog.list_devices(category=DeviceCategory.MOTOR)
# Access ophyd device via .ophyd_device property
motors = [info.ophyd_device for info in motor_infos if info.ophyd_device]
# Or get ophyd device directly by name
motor = catalog.get_ophyd_device("sample_x")
```

## device-catalog-api

The DeviceCatalog provides device discovery and access. It manages DeviceInfo
objects which wrap ophyd devices with metadata and state tracking.

### Two-Layer Architecture

- **DeviceInfo**: Metadata wrapper with name, category, state, etc.
- **ophyd device**: The actual hardware interface (accessed via `.ophyd_device`)

### Listing and Searching Devices

```python
from lightfall.devices import DeviceCatalog
from lightfall.devices.model import DeviceCategory, DeviceInfo

catalog = DeviceCatalog.get_instance()

# List with filters (returns list[DeviceInfo])
devices = catalog.list_devices(category=DeviceCategory.MOTOR, beamline="BL1", active_only=True)
devices = catalog.search_devices("sample")  # Search by name/description/tags
devices = catalog.get_all_devices()         # Get all devices
```

### Getting a Single Device

```python
# By name (returns DeviceInfo | None)
device_info = catalog.get_device_by_name("sample_x")

# By EPICS prefix
device_info = catalog.get_device_by_prefix("IOC:m1")
```

### Accessing the Ophyd Device

```python
# Direct access (returns ophyd device or None)
ophyd_dev = catalog.get_ophyd_device("sample_x")

# Via DeviceInfo (may be None if not connected)
ophyd_dev = device_info.ophyd_device

# Control the device
if ophyd_dev:
    position = ophyd_dev.position       # Read position
    ophyd_dev.set(10.0).wait()          # Move and wait
    ophyd_dev.stop()                    # Stop motion
```

### DeviceInfo Properties

```python
device_info.id           # UUID - unique identifier
device_info.name         # str - human-readable name
device_info.category     # DeviceCategory enum
device_info.description  # str - device description
device_info.prefix       # str - EPICS PV prefix
device_info.beamline     # str | None - beamline assignment
device_info.active       # bool - is device active
device_info.state        # DeviceState - current state (position, status, alarms)
device_info.ophyd_device # The actual ophyd device instance (may be None)
```

### DeviceCategory Enum

Available categories (independent vs dependent variable classification):
- `DeviceCategory.MOTOR` - Physical read/write (motors, positioners, slits) — independent variable
- `DeviceCategory.DETECTOR` - Measures something (detectors, cameras, sensors, diodes, signals) — dependent variable
- `DeviceCategory.CONTROLLER` - Non-physical read/write (temperature controllers, delay generators, power supplies) — independent variable, catch-all default

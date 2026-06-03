# ControllerPlugin

Controller plugins provide device-specific control widgets.

## Purpose

Use `ControllerPlugin` when you want to:
- Create custom UIs for specific device types
- Provide optimized controls for beamline hardware
- Implement device-pattern matching for automatic widget selection

## Base Class

```python
from lightfall.plugins.controller_plugin import ControllerPlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"controller"` | Plugin type identifier |
| `is_singleton` | `True` | Plugin is singleton (creates widgets on demand) |

## Required Methods

### name (property)

Unique identifier for this controller.

```python
@property
def name(self) -> str:
    return "my_controller"
```

### can_control(items)

Check if this controller handles the given device items.

```python
def can_control(self, items: list[DeviceTreeItem]) -> int | None:
    """Check if this controller can handle the selected items.

    Args:
        items: List of selected DeviceTreeItems.

    Returns:
        Priority value (higher = preferred) or None if not applicable.
    """
    if len(items) != 1:
        return None

    item = items[0]
    if self._matches_device(item):
        return 150  # Higher than default
    return None
```

### create_widget(parent)

Create a new widget instance for controlling devices.

```python
def create_widget(self, parent: QWidget | None = None) -> QWidget:
    """Create the control widget.

    Args:
        parent: Parent widget.

    Returns:
        A new widget instance.
    """
    return MyControlWidget(parent)
```

## Optional Methods

### display_name (property)

Human-readable name for the widget selector. Defaults to title-cased `name`.

```python
@property
def display_name(self) -> str:
    return "My Custom Controller"
```

## Priority Guidelines

`can_control()` returns a priority value indicating how well this controller matches:

| Priority Range | Meaning |
|----------------|---------|
| `200+` | Exact device/prefix match |
| `100-199` | Device class match |
| `50-99` | Category match |
| `1-49` | Generic fallback |
| `None` | Cannot control |

Higher priorities take precedence when multiple controllers match.

## Lifecycle

1. Plugin is instantiated on load
2. Plugin is registered with `ControllerPluginRegistry`
3. When user selects devices:
   - All registered controllers' `can_control()` is called
   - Controller with highest priority is selected
   - `create_widget()` creates the control widget
4. Widget is configured with selected devices via `set_items()`

## Complete Example

### Motor Controller Plugin

```python
"""Custom motor controller plugin."""

from PySide6.QtWidgets import QWidget

from lightfall.plugins.controller_plugin import ControllerPlugin


class MotorControllerPlugin(ControllerPlugin):
    """Controller for motor devices."""

    @property
    def name(self) -> str:
        return "motor_controller"

    @property
    def display_name(self) -> str:
        return "Motor Control"

    def can_control(self, items) -> int | None:
        """Match motor devices."""
        if len(items) != 1:
            return None

        item = items[0]
        if not item.device_info:
            return None

        # Check if it's a motor
        from lightfall.devices.types import DeviceCategory

        if item.device_info.category == DeviceCategory.MOTOR:
            return 100  # Device class match

        return None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        from my_package.widgets.motor_widget import MotorControlWidget
        return MotorControlWidget(parent)
```

### Motor Control Widget

```python
"""Motor control widget implementation."""

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class MotorControlWidget(QWidget):
    """Widget for controlling a single motor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._device = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Position group
        pos_group = QGroupBox("Position")
        pos_layout = QFormLayout(pos_group)

        self._position_spin = QDoubleSpinBox()
        self._position_spin.setDecimals(4)
        self._position_spin.setRange(-1000, 1000)
        pos_layout.addRow("Target:", self._position_spin)

        # Movement buttons
        btn_layout = QHBoxLayout()
        self._move_btn = QPushButton("Move")
        self._move_btn.clicked.connect(self._on_move)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        btn_layout.addWidget(self._move_btn)
        btn_layout.addWidget(self._stop_btn)
        pos_layout.addRow(btn_layout)

        layout.addWidget(pos_group)

    def set_items(self, items):
        """Configure the widget with device items.

        Args:
            items: List of DeviceTreeItems (should be single motor).
        """
        if items and len(items) == 1:
            self._device = items[0].device_info
            self._update_from_device()

    def _update_from_device(self):
        """Update widget from device state."""
        if self._device:
            # Read current position
            # self._position_spin.setValue(self._device.position)
            pass

    def _on_move(self):
        """Handle move button click."""
        if self._device:
            target = self._position_spin.value()
            # self._device.move(target)

    def _on_stop(self):
        """Handle stop button click."""
        if self._device:
            # self._device.stop()
            pass
```

## Prefix-based Matching

Match devices by PV prefix:

```python
class SlitControllerPlugin(ControllerPlugin):
    """Controller for slit devices by prefix."""

    @property
    def name(self) -> str:
        return "slit_controller"

    def can_control(self, items) -> int | None:
        if len(items) != 1:
            return None

        item = items[0]
        if not item.device_info:
            return None

        # Match by prefix
        prefix = item.device_info.prefix
        if prefix and prefix.startswith("BL701:SLIT"):
            return 200  # Exact match - high priority

        return None

    def create_widget(self, parent=None):
        from my_package.widgets import SlitWidget
        return SlitWidget(parent)
```

## Multi-device Controller

Handle multiple devices together:

```python
class MultiMotorControllerPlugin(ControllerPlugin):
    """Controller for coordinated multi-motor control."""

    @property
    def name(self) -> str:
        return "multi_motor"

    @property
    def display_name(self) -> str:
        return "Coordinated Motors"

    def can_control(self, items) -> int | None:
        """Match when 2-4 motors are selected."""
        if not (2 <= len(items) <= 4):
            return None

        from lightfall.devices.types import DeviceCategory

        # All items must be motors
        for item in items:
            if not item.device_info:
                return None
            if item.device_info.category != DeviceCategory.MOTOR:
                return None

        return 80  # Category match for multiple

    def create_widget(self, parent=None):
        from my_package.widgets import CoordinatedMotorWidget
        return CoordinatedMotorWidget(parent)
```

## Registration

```python
PluginEntry(
    type_name="controller",
    name="motor_controller",
    import_path="my_package.plugins:MotorControllerPlugin",
),
```

## Widget Interface

Control widgets should implement `set_items()` to receive selected devices:

```python
class MyControlWidget(QWidget):
    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Configure widget with selected device items.

        Args:
            items: List of selected DeviceTreeItems.
        """
        self._items = items
        self._update_display()
```

## Controller Selection Flow

1. User selects device(s) in the device tree
2. System calls `can_control(items)` on all registered controllers
3. Controllers return priority (int) or `None`
4. Controller with highest priority is selected
5. `create_widget()` creates a new widget instance
6. `widget.set_items(items)` configures the widget

"""Device control widgets for NCS.

This module provides widgets for direct device control:
- Base classes for creating control widgets
- Motor control widgets (single and multi-motor)
- Container widget that selects appropriate controls

Usage:
    from ncs.ui.widgets import DeviceControlWidget

    # In a panel
    control = DeviceControlWidget()
    control.set_items(selected_tree_items)
"""

from ncs.ui.widgets.base_control import (
    BaseControlWidget,
    ControlWidgetRegistry,
    register_control_widget,
)
from ncs.ui.widgets.device_control import (
    ControlWidgetFactory,
    DeviceControlWidget,
)
from ncs.ui.widgets.motor_control import (
    MotorControlWidget,
    MultiMotorControlWidget,
)

__all__ = [
    # Base classes
    "BaseControlWidget",
    "ControlWidgetRegistry",
    "register_control_widget",
    # Container
    "DeviceControlWidget",
    "ControlWidgetFactory",
    # Motor widgets
    "MotorControlWidget",
    "MultiMotorControlWidget",
]

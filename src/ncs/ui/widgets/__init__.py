"""Device control widgets for NCS.

This module provides widgets for direct device control:
- Base classes for creating control widgets
- Motor control widgets (single and multi-motor)
- Container widget that selects appropriate controls
- RunEngine control and plan execution widgets
- Document stream viewer

Usage:
    from ncs.ui.widgets import DeviceControlWidget, RunEngineControlWidget

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
from ncs.ui.widgets.document_stream import (
    DocumentStreamModel,
    DocumentStreamWidget,
)
from ncs.ui.widgets.motor_control import (
    MotorControlWidget,
    MultiMotorControlWidget,
)
from ncs.ui.widgets.plan_config import (
    PlanConfigWidget,
    PlanExecutionWidget,
)
from ncs.ui.widgets.plan_selector import (
    PlanFilterProxyModel,
    PlanListModel,
    PlanSelectorWidget,
)
from ncs.ui.widgets.runengine_control import (
    RunEngineControlWidget,
    RunEngineStatusBar,
    StatusIndicator,
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
    # RunEngine control
    "RunEngineControlWidget",
    "RunEngineStatusBar",
    "StatusIndicator",
    # Plan widgets
    "PlanSelectorWidget",
    "PlanListModel",
    "PlanFilterProxyModel",
    "PlanConfigWidget",
    "PlanExecutionWidget",
    # Document stream
    "DocumentStreamWidget",
    "DocumentStreamModel",
]

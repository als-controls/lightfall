"""Device control widgets for NCS.

This module provides widgets for direct device control:
- Base classes for creating control widgets
- Motor control widgets (single and multi-motor)
- Container widget that selects appropriate controls
- RunEngine control and plan execution widgets
- Document stream viewer

Usage:
    from lucid.ui.widgets import DeviceControlWidget, RunEngineControlWidget

    # In a panel
    control = DeviceControlWidget()
    control.set_items(selected_tree_items)
"""

from lucid.ui.widgets.base_control import (
    BaseControlWidget,
    ControlWidgetRegistry,
    register_control_widget,
)
from lucid.ui.widgets.device_control import (
    ControlWidgetFactory,
    DeviceControlWidget,
)
from lucid.ui.widgets.device_selector import (
    DeviceSelectorDialog,
)

# Import conditionally since DeviceParameter requires pyqtgraph
try:
    from lucid.ui.widgets.device_selector import (
        DeviceParameter,
        DeviceParameterItem,
    )
except ImportError:
    DeviceParameter = None  # type: ignore
    DeviceParameterItem = None  # type: ignore
from lucid.ui.widgets.camera import (
    AndorCameraControlWidget,
    CameraControlWidget,
    PIMTECameraControlWidget,
)
from lucid.ui.widgets.document_stream import (
    DocumentStreamModel,
    DocumentStreamWidget,
)
from lucid.ui.widgets.motor_control import (
    MotorControlWidget,
    MultiMotorControlWidget,
)
from lucid.ui.widgets.plan_config import (
    PlanConfigWidget,
    PlanExecutionWidget,
)
from lucid.ui.widgets.plan_selector import (
    PlanFilterProxyModel,
    PlanListModel,
    PlanSelectorWidget,
)
from lucid.ui.widgets.runengine_control import (
    RunEngineControlWidget,
    RunEngineStatusBar,
    StatusIndicator,
)
from lucid.ui.widgets.tiled_filter_widget import TiledFilters, TiledFilterWidget
from lucid.ui.widgets.tiled_status import TiledStatusWidget

__all__ = [
    # Base classes
    "BaseControlWidget",
    "ControlWidgetRegistry",
    "register_control_widget",
    # Container
    "DeviceControlWidget",
    "ControlWidgetFactory",
    # Device selector (for ParameterTree integration)
    "DeviceSelectorDialog",
    "DeviceParameter",
    "DeviceParameterItem",
    # Motor widgets
    "MotorControlWidget",
    "MultiMotorControlWidget",
    # Camera widgets
    "CameraControlWidget",
    "AndorCameraControlWidget",
    "PIMTECameraControlWidget",
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
    # Tiled filter
    "TiledFilterWidget",
    "TiledFilters",
    # Tiled status
    "TiledStatusWidget",
]

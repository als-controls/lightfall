"""Device control widgets for NCS.

This module provides widgets for direct device control:
- Base classes for creating control widgets
- Motor control widgets (single and multi-motor)
- Container widget that selects appropriate controls
- RunEngine control and plan execution widgets
- Document stream viewer

Usage:
    from lightfall.ui.widgets import DeviceControlWidget, RunEngineControlWidget

    # In a panel
    control = DeviceControlWidget()
    control.set_items(selected_tree_items)
"""

from lightfall.ui.widgets.base_control import (
    BaseControlWidget,
    ControlWidgetRegistry,
    register_control_widget,
)
from lightfall.ui.widgets.device_control import (
    ControlWidgetFactory,
    DeviceControlWidget,
)
from lightfall.ui.widgets.device_selector import (
    DeviceSelectorDialog,
)

# Import conditionally since DeviceParameter requires pyqtgraph
try:
    from lightfall.ui.widgets.device_selector import (
        DeviceParameter,
        DeviceParameterItem,
    )
except ImportError:
    DeviceParameter = None  # type: ignore
    DeviceParameterItem = None  # type: ignore
from lightfall.ui.widgets.camera import (
    CameraControlWidget,
)
from lightfall.ui.widgets.document_stream import (
    DocumentStreamModel,
    DocumentStreamWidget,
)
from lightfall.ui.widgets.motor_control import (
    MotorControlWidget,
    MultiMotorControlWidget,
)
from lightfall.ui.widgets.plan_config import (
    PlanConfigWidget,
    PlanExecutionWidget,
)
from lightfall.ui.widgets.plan_selector import (
    PlanFilterProxyModel,
    PlanListModel,
    PlanSelectorWidget,
)
from lightfall.ui.widgets.runengine_control import (
    RunEngineControlWidget,
    SpinnerIndicator,
)
from lightfall.ui.widgets.signal_control import (
    MultiSignalControlWidget,
    SignalControlWidget,
)
from lightfall.ui.widgets.tiled_filter_widget import TiledFilters, TiledFilterWidget

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
    # Signal widgets
    "SignalControlWidget",
    "MultiSignalControlWidget",
    # Camera widgets
    "CameraControlWidget",
    # RunEngine control
    "RunEngineControlWidget",
    "SpinnerIndicator",
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
]

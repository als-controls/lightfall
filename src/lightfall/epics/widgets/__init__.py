"""
EPICS PySide6 Widgets.

Provides Qt widgets that display and edit EPICS PV values and ophyd signals.
"""

from lightfall.epics.widgets.areadetector import (
    PVAreaDetector,
    PVAreaDetectorControls,
    PVImageView,
)
from lightfall.epics.widgets.auto import PVAutoWidget
from lightfall.epics.widgets.base import EpicsWidget
from lightfall.epics.widgets.checkbox import PVCheckBox
from lightfall.epics.widgets.combobox import PVComboBox
from lightfall.epics.widgets.label import PVLabel
from lightfall.epics.widgets.lineedit import PVLineEdit
from lightfall.epics.widgets.motor import PVMotor

# Ophyd signal widgets
from lightfall.epics.widgets.ophyd_base import OphydWidget
from lightfall.epics.widgets.ophyd_combobox import OphydComboBox
from lightfall.epics.widgets.ophyd_label import OphydLabel
from lightfall.epics.widgets.ophyd_lineedit import OphydLineEdit
from lightfall.epics.widgets.ophyd_spinbox import OphydSpinBox
from lightfall.epics.widgets.slider import PVSlider
from lightfall.epics.widgets.status_indicator import StatusIndicator

__all__ = [
    "EpicsWidget",
    "PVLabel",
    "PVLineEdit",
    "PVComboBox",
    "PVCheckBox",
    "PVSlider",
    "PVAutoWidget",
    "PVMotor",
    "StatusIndicator",
    # AreaDetector widgets
    "PVImageView",
    "PVAreaDetectorControls",
    "PVAreaDetector",
    # Ophyd signal widgets
    "OphydWidget",
    "OphydLineEdit",
    "OphydLabel",
    "OphydComboBox",
    "OphydSpinBox",
]

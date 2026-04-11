"""
EPICS PySide6 Widgets.

Provides Qt widgets that display and edit EPICS PV values and ophyd signals.
"""

from lucid.epics.widgets.base import EpicsWidget
from lucid.epics.widgets.label import PVLabel
from lucid.epics.widgets.lineedit import PVLineEdit
from lucid.epics.widgets.combobox import PVComboBox
from lucid.epics.widgets.checkbox import PVCheckBox
from lucid.epics.widgets.slider import PVSlider
from lucid.epics.widgets.auto import PVAutoWidget
from lucid.epics.widgets.motor import PVMotor
from lucid.epics.widgets.areadetector import (
    PVImageView,
    PVAreaDetectorControls,
    PVAreaDetector,
)

# Ophyd signal widgets
from lucid.epics.widgets.ophyd_base import OphydWidget
from lucid.epics.widgets.ophyd_lineedit import OphydLineEdit
from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.epics.widgets.ophyd_combobox import OphydComboBox
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox

__all__ = [
    "EpicsWidget",
    "PVLabel",
    "PVLineEdit",
    "PVComboBox",
    "PVCheckBox",
    "PVSlider",
    "PVAutoWidget",
    "PVMotor",
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

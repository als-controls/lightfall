"""Device-specific panels for camera control widgets.

Panels:
    CoolerPanel: Andor-style cooler controls
    TemperaturePanel: PIMTE-style temperature display
"""

from lucid.ui.widgets.camera.panels.cooler import CoolerPanel
from lucid.ui.widgets.camera.panels.temperature import TemperaturePanel

__all__ = [
    "CoolerPanel",
    "TemperaturePanel",
]

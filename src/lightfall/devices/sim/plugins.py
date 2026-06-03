"""Plugin components for SimDetector."""

from __future__ import annotations

from typing import Any

from ophyd import Component, Device, Signal
from ophyd.signal import SignalRO


class UnitSignal(Signal):
    """Signal subclass that carries engineering units."""

    def __init__(self, *args: Any, units: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._units = units

    @property
    def metadata(self) -> dict[str, Any]:
        md = super().metadata
        md["units"] = self._units
        return md

    def describe(self) -> dict[str, dict[str, Any]]:
        desc = super().describe()
        for info in desc.values():
            info["units"] = self._units
        return desc


class SimCam(Device):
    """Simulated camera component.

    Provides acquisition control and image settings without EPICS.

    Shutter modes (matching EPICS AreaDetector):
        0 = None (no shutter control)
        1 = EPICS PV (external shutter via PV)
        2 = Detector output (detector controls shutter)

    Shutter control:
        0 = Close
        1 = Open
    """

    # Acquisition control
    acquire = Component(Signal, value=0, kind="config")
    acquire_time = Component(UnitSignal, value=0.1, units="s", kind="config")
    acquire_period = Component(UnitSignal, value=0.2, units="s", kind="config")
    num_images = Component(Signal, value=1, kind="config")
    image_mode = Component(Signal, value=0, kind="config")  # 0=Single, 1=Multiple, 2=Continuous

    # Shutter control
    shutter_mode = Component(Signal, value=0, kind="config")  # 0=None, 1=EPICS PV, 2=Detector
    shutter_control = Component(Signal, value=1, kind="config")  # 0=Close, 1=Open
    shutter_open_delay = Component(Signal, value=0.0, kind="config")
    shutter_close_delay = Component(Signal, value=0.0, kind="config")

    # Image settings
    size_x = Component(Signal, value=256, kind="config")
    size_y = Component(Signal, value=256, kind="config")
    bin_x = Component(Signal, value=1, kind="config")
    bin_y = Component(Signal, value=1, kind="config")
    data_type = Component(Signal, value="uint8", kind="config")
    gain = Component(Signal, value=1.0, kind="config")

    # Read-only status
    detector_state = Component(SignalRO, value=0, kind="normal")  # 0=Idle, 1=Acquire, etc.
    array_counter = Component(SignalRO, value=0, kind="normal")

    # Pattern control
    pattern_mode = Component(Signal, value="animated", kind="config")
    pattern_type = Component(Signal, value="sine", kind="config")


class SimImagePlugin(Device):
    """Simulated image plugin - provides image data output."""

    enable = Component(Signal, value=1, kind="config")
    array_data = Component(SignalRO, value=None, kind="normal")
    array_size_x = Component(SignalRO, value=256, kind="normal")
    array_size_y = Component(SignalRO, value=256, kind="normal")
    unique_id = Component(SignalRO, value=0, kind="normal")


class SimStatsPlugin(Device):
    """Simulated statistics plugin - computes image statistics."""

    enable = Component(Signal, value=1, kind="config")
    min_value = Component(SignalRO, value=0, kind="normal")
    max_value = Component(SignalRO, value=0, kind="normal")
    mean_value = Component(SignalRO, value=0.0, kind="normal")
    sigma = Component(SignalRO, value=0.0, kind="normal")
    total = Component(SignalRO, value=0, kind="normal")
    centroid_x = Component(SignalRO, value=0.0, kind="normal")
    centroid_y = Component(SignalRO, value=0.0, kind="normal")


class SimROIPlugin(Device):
    """Simulated ROI plugin - extracts region of interest."""

    enable = Component(Signal, value=0, kind="config")
    min_x = Component(Signal, value=0, kind="config")
    min_y = Component(Signal, value=0, kind="config")
    size_x = Component(Signal, value=256, kind="config")
    size_y = Component(Signal, value=256, kind="config")
    array_data = Component(SignalRO, value=None, kind="normal")


class SimTransformPlugin(Device):
    """Simulated transform plugin - image rotation and flipping."""

    enable = Component(Signal, value=0, kind="config")
    rotation = Component(Signal, value=0, kind="config")  # 0, 90, 180, 270
    flip_x = Component(Signal, value=0, kind="config")
    flip_y = Component(Signal, value=0, kind="config")

"""
PVAreaDetector - Composite widget for EPICS AreaDetector devices.

Combines PVImageView and PVAreaDetectorControls into a single integrated
widget for complete AreaDetector operation.
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
from PySide6.QtCore import Property, Signal, Slot, QTimer, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
)

from lucid.epics.widgets.areadetector.image_view import PVImageView
from lucid.epics.widgets.areadetector.controls import PVAreaDetectorControls


class PVAreaDetector(QWidget):
    """
    Composite AreaDetector widget combining image view and acquisition controls.

    This widget provides a complete interface for EPICS AreaDetector devices,
    following the ophyd naming convention with configurable suffixes for
    camera (cam) and image array (image) plugins.

    Layout:
    +----------------------------------+
    |  [Image View with Histogram]     |
    +----------------------------------+
    |  [Acquisition Controls]          |
    +----------------------------------+

    Attributes:
        prefix: The base detector prefix (e.g., "13SIM1:").
        cam_suffix: The camera plugin suffix (default "cam1:").
        image_suffix: The StdArrays plugin suffix (default "image1:").

    Signals:
        connection_changed: Emitted when overall connection state changes.
        frame_received: Emitted when a new frame is displayed.
        acquisition_started: Emitted when acquisition begins.
        acquisition_stopped: Emitted when acquisition ends.

    Example:
        >>> detector = PVAreaDetector(
        ...     prefix="13SIM1:",
        ...     cam_suffix="cam1:",
        ...     image_suffix="image1:",
        ... )
        >>> detector.show()
    """

    widget_type: ClassVar[str] = "PVAreaDetector"
    widget_description: ClassVar[str] = "Complete AreaDetector viewer and controls"

    connection_changed = Signal(bool)
    frame_received = Signal(int)
    acquisition_started = Signal()
    acquisition_stopped = Signal()
    cursor_moved = Signal(float, float, float)

    def __init__(
        self,
        prefix: str = "",
        cam_suffix: str = "cam1:",
        image_suffix: str = "image1:",
        max_fps: float = 30.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._prefix = prefix
        self._cam_suffix = cam_suffix
        self._image_suffix = image_suffix
        self._max_fps = max_fps

        self._setup_ui()

        self._image_view.connection_changed.connect(self._on_image_connection_changed)
        self._image_view.frame_received.connect(self.frame_received)
        self._image_view.cursor_moved.connect(self.cursor_moved)

        self._controls.connection_changed.connect(self._on_controls_connection_changed)
        self._controls.acquisition_started.connect(self.acquisition_started)
        self._controls.acquisition_stopped.connect(self.acquisition_stopped)

    @Property(str)
    def prefix(self) -> str:
        return self._prefix

    @prefix.setter
    def prefix(self, value: str) -> None:
        if value != self._prefix:
            self._prefix = value
            self._image_view.prefix = value
            self._controls.prefix = value

    @Property(str)
    def cam_suffix(self) -> str:
        return self._cam_suffix

    @cam_suffix.setter
    def cam_suffix(self, value: str) -> None:
        if value != self._cam_suffix:
            self._cam_suffix = value
            self._controls.cam_suffix = value

    @Property(str)
    def image_suffix(self) -> str:
        return self._image_suffix

    @image_suffix.setter
    def image_suffix(self, value: str) -> None:
        if value != self._image_suffix:
            self._image_suffix = value
            self._image_view.image_suffix = value

    @Property(float)
    def max_fps(self) -> float:
        return self._max_fps

    @max_fps.setter
    def max_fps(self, value: float) -> None:
        self._max_fps = value
        self._image_view.max_fps = value

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._image_view = PVImageView(
            prefix=self._prefix,
            image_suffix=self._image_suffix,
            max_fps=self._max_fps,
        )
        splitter.addWidget(self._image_view)

        self._controls = PVAreaDetectorControls(
            prefix=self._prefix,
            cam_suffix=self._cam_suffix,
        )
        splitter.addWidget(self._controls)

        splitter.setSizes([400, 200])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)

    def _on_image_connection_changed(self, connected: bool) -> None:
        self._emit_connection_changed()

    def _on_controls_connection_changed(self, connected: bool) -> None:
        self._emit_connection_changed()

    def _emit_connection_changed(self) -> None:
        connected = self._image_view.is_connected and self._controls.is_connected
        self.connection_changed.emit(connected)

    def acquire(self) -> None:
        self._controls.acquire()

    def abort(self) -> None:
        self._controls.abort()

    def set_acquire_time(self, seconds: float) -> None:
        self._controls.set_acquire_time(seconds)

    def set_acquire_period(self, seconds: float) -> None:
        self._controls.set_acquire_period(seconds)

    def set_num_images(self, count: int) -> None:
        self._controls.set_num_images(count)

    def set_image_mode(self, mode: str) -> None:
        self._controls.set_image_mode(mode)

    def set_colormap(self, name: str) -> None:
        self._image_view.set_colormap(name)

    def auto_scale_intensity(self) -> None:
        self._image_view.auto_scale_intensity()

    def set_levels(self, min_val: float, max_val: float) -> None:
        self._image_view.set_levels(min_val, max_val)

    @property
    def is_connected(self) -> bool:
        return self._image_view.is_connected and self._controls.is_connected

    @property
    def is_acquiring(self) -> bool:
        return self._controls.is_acquiring

    @property
    def detector_state(self) -> str:
        return self._controls.detector_state

    @property
    def frame_count(self) -> int:
        return self._image_view.frame_count

    @property
    def image_size(self) -> tuple[int, int]:
        return self._image_view.image_size

    @property
    def current_image(self) -> np.ndarray | None:
        return self._image_view.current_image

    @property
    def acquire_time(self) -> float | None:
        return self._controls.acquire_time

    @property
    def acquire_period(self) -> float | None:
        return self._controls.acquire_period

    @property
    def num_images(self) -> int | None:
        return self._controls.num_images

    @property
    def image_mode(self) -> str | None:
        return self._controls.image_mode

    @property
    def image_view(self) -> PVImageView:
        return self._image_view

    @property
    def controls(self) -> PVAreaDetectorControls:
        return self._controls

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "prefix": self._prefix,
            "cam_suffix": self._cam_suffix,
            "image_suffix": self._image_suffix,
            "connected": self.is_connected,
            "detector_state": self.detector_state,
            "is_acquiring": self.is_acquiring,
            "acquire_time": self.acquire_time,
            "acquire_period": self.acquire_period,
            "num_images": self.num_images,
            "image_mode": self.image_mode,
            "image_size": {"width": self.image_size[0], "height": self.image_size[1]},
            "frame_count": self.frame_count,
            "has_image": self.current_image is not None,
            "max_fps": self._max_fps,
            "image_view": self._image_view.get_introspection_data(),
            "controls": self._controls.get_introspection_data(),
            "available_actions": [
                {"name": "acquire", "description": "Start acquisition"},
                {"name": "abort", "description": "Stop acquisition"},
                {"name": "set_acquire_time", "args": ["seconds"], "description": "Set exposure time"},
                {"name": "set_acquire_period", "args": ["seconds"], "description": "Set period between images"},
                {"name": "set_num_images", "args": ["count"], "description": "Set number of images"},
                {"name": "set_image_mode", "args": ["mode"], "description": "Set image mode"},
                {"name": "set_colormap", "args": ["name"], "description": "Set colormap"},
                {"name": "auto_scale_intensity", "description": "Auto-scale intensity"},
                {"name": "set_levels", "args": ["min", "max"], "description": "Set intensity levels"},
            ],
        }

    def closeEvent(self, event) -> None:
        self._image_view.close()
        self._controls.close()
        super().closeEvent(event)

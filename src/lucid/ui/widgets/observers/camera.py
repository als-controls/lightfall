"""Observer-camera abstraction for non-ophyd hardware (e.g., GigE Vision).

For ophyd-backed area detectors, see lucid.ui.widgets.camera (the ophyd-flavored peer).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

import numpy as np


class CameraBase(ABC):
    """Abstract observer-camera contract used by CameraImageView and similar consumers.

    Concrete implementations own the transport details (GVCP, USB3 Vision, etc).
    The base only specifies the lifecycle methods the consumer needs.
    """

    @abstractmethod
    def open(self) -> None:
        """Acquire exclusive control of the camera. Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Release control. Idempotent."""

    @abstractmethod
    def start_stream(
        self,
        on_frame: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        """Begin delivering frames. Callback fires from a background thread."""

    @abstractmethod
    def stop_stream(self) -> None:
        """Stop delivering frames and release stream resources."""

    @abstractmethod
    def get_latest_frame(self) -> np.ndarray | None:
        """Most-recently-decoded frame, or None if no frame yet. Shared, read-only."""

    def __enter__(self) -> "CameraBase":
        self.open()
        return self

    def __exit__(self, *a) -> None:
        self.close()

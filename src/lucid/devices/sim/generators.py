"""Image generators for SimDetector."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class ImageGenerator(ABC):
    """Base class for image generators."""

    @abstractmethod
    def generate(
        self,
        width: int,
        height: int,
        dtype: np.dtype,
        frame_number: int,
        **kwargs: Any,
    ) -> np.ndarray:
        """Generate an image array.

        Args:
            width: Image width in pixels.
            height: Image height in pixels.
            dtype: NumPy dtype for the output array.
            frame_number: Current frame number (for animation).
            **kwargs: Additional generator-specific parameters.

        Returns:
            2D numpy array of shape (height, width).
        """
        pass


class StaticPatternGenerator(ImageGenerator):
    """Generates static test patterns."""

    def generate(
        self,
        width: int,
        height: int,
        dtype: np.dtype,
        frame_number: int,
        pattern: str = "gradient",
        **kwargs: Any,
    ) -> np.ndarray:
        """Generate a static pattern.

        Args:
            width: Image width.
            height: Image height.
            dtype: Output dtype.
            frame_number: Ignored for static patterns.
            pattern: One of 'gradient', 'checker', 'gaussian'.
        """
        if pattern == "gradient":
            return self._gradient(width, height, dtype)
        elif pattern == "checker":
            return self._checker(width, height, dtype)
        elif pattern == "gaussian":
            return self._gaussian(width, height, dtype)
        else:
            return self._gradient(width, height, dtype)

    def _gradient(self, width: int, height: int, dtype: np.dtype) -> np.ndarray:
        """Generate horizontal gradient pattern."""
        info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
        max_val = info.max if info else 1.0
        x = np.linspace(0, max_val, width, dtype=dtype)
        return np.tile(x, (height, 1))

    def _checker(self, width: int, height: int, dtype: np.dtype) -> np.ndarray:
        """Generate checkerboard pattern."""
        info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
        max_val = info.max if info else 1.0
        block_size = max(width, height) // 8
        x = np.arange(width) // block_size
        y = np.arange(height) // block_size
        pattern = (x[np.newaxis, :] + y[:, np.newaxis]) % 2
        return (pattern * max_val).astype(dtype)

    def _gaussian(self, width: int, height: int, dtype: np.dtype) -> np.ndarray:
        """Generate centered gaussian blob."""
        info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
        max_val = info.max if info else 1.0
        x = np.linspace(-1, 1, width)
        y = np.linspace(-1, 1, height)
        xx, yy = np.meshgrid(x, y)
        sigma = 0.3
        gaussian = np.exp(-(xx**2 + yy**2) / (2 * sigma**2))
        return (gaussian * max_val).astype(dtype)


class AnimatedPatternGenerator(ImageGenerator):
    """Generates animated patterns that evolve with frame number."""

    def generate(
        self,
        width: int,
        height: int,
        dtype: np.dtype,
        frame_number: int,
        pattern: str = "sine",
        **kwargs: Any,
    ) -> np.ndarray:
        """Generate an animated pattern.

        Args:
            width: Image width.
            height: Image height.
            dtype: Output dtype.
            frame_number: Frame counter for animation phase.
            pattern: One of 'sine', 'rotating'.
        """
        if pattern == "sine":
            return self._sine_wave(width, height, dtype, frame_number)
        elif pattern == "rotating":
            return self._rotating(width, height, dtype, frame_number)
        else:
            return self._sine_wave(width, height, dtype, frame_number)

    def _sine_wave(
        self, width: int, height: int, dtype: np.dtype, frame: int
    ) -> np.ndarray:
        """Generate moving sine wave pattern."""
        info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
        max_val = info.max if info else 1.0
        phase = frame * 0.1
        x = np.linspace(0, 4 * np.pi, width)
        y = np.linspace(0, 4 * np.pi, height)
        xx, yy = np.meshgrid(x, y)
        pattern = np.sin(xx + phase) * np.sin(yy + phase * 0.7)
        normalized = (pattern + 1) / 2  # Normalize to 0-1
        return (normalized * max_val).astype(dtype)

    def _rotating(
        self, width: int, height: int, dtype: np.dtype, frame: int
    ) -> np.ndarray:
        """Generate rotating pattern."""
        info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
        max_val = info.max if info else 1.0
        angle = frame * 0.05
        x = np.linspace(-1, 1, width)
        y = np.linspace(-1, 1, height)
        xx, yy = np.meshgrid(x, y)
        # Rotate coordinates
        xr = xx * np.cos(angle) - yy * np.sin(angle)
        yr = xx * np.sin(angle) + yy * np.cos(angle)
        # Create spiral pattern
        r = np.sqrt(xr**2 + yr**2)
        theta = np.arctan2(yr, xr)
        pattern = np.sin(r * 10 + theta * 3)
        normalized = (pattern + 1) / 2
        return (normalized * max_val).astype(dtype)


class MotorResponsiveGenerator(ImageGenerator):
    """Generates images that respond to motor positions."""

    def __init__(self, motors: dict[str, Any] | None = None) -> None:
        """Initialize with motor references.

        Args:
            motors: Dict mapping 'x' and 'y' to ophyd motor devices.
        """
        self._motors = motors or {}

    def generate(
        self,
        width: int,
        height: int,
        dtype: np.dtype,
        frame_number: int,
        **kwargs: Any,
    ) -> np.ndarray:
        """Generate image with sample at motor position.

        The 'sample' is rendered as a gaussian blob whose position
        corresponds to the motor positions.
        """
        info = np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else None
        max_val = info.max if info else 1.0

        # Get motor positions (default to center)
        motor_x = self._motors.get("x")
        motor_y = self._motors.get("y")

        # Read positions, normalize to -1..1 range
        # Assume motor range is roughly -100 to 100
        pos_x = 0.0
        pos_y = 0.0
        if motor_x is not None:
            try:
                pos_x = float(motor_x.position) / 100.0
                pos_x = max(-1.0, min(1.0, pos_x))
            except Exception:
                pass
        if motor_y is not None:
            try:
                pos_y = float(motor_y.position) / 100.0
                pos_y = max(-1.0, min(1.0, pos_y))
            except Exception:
                pass

        # Generate image with sample at position
        x = np.linspace(-1, 1, width)
        y = np.linspace(-1, 1, height)
        xx, yy = np.meshgrid(x, y)

        # Gaussian blob at motor position
        sigma = 0.15
        sample = np.exp(-((xx - pos_x) ** 2 + (yy - pos_y) ** 2) / (2 * sigma**2))

        # Add some background structure
        background = 0.1 * (np.sin(xx * 5) * np.sin(yy * 5) + 1) / 2

        combined = sample + background
        combined = np.clip(combined, 0, 1)

        return (combined * max_val).astype(dtype)

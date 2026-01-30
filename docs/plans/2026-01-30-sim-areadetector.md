# SimDetector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a pure-ophyd SimDetector to the mock device backend for testing UI widgets and Bluesky plans without EPICS dependencies.

**Architecture:** A custom ophyd `Device` subclass with plugin-style components (cam, image, stats, roi, transform). All signals are in-memory using `ophyd.Signal`/`SignalRO`. Supports three image generation modes: static patterns, animated, and motor-responsive. Configurable data output: embedded arrays or file references.

**Tech Stack:** ophyd, numpy

---

## Task 1: Create Package Structure

**Files:**
- Create: `src/lucid/devices/sim/__init__.py`
- Create: `src/lucid/devices/sim/generators.py`

**Step 1: Create the sim package directory and __init__.py**

```python
# src/lucid/devices/sim/__init__.py
"""Simulated ophyd devices for testing and development."""

from lucid.devices.sim.areadetector import SimDetector
from lucid.devices.sim.plugins import (
    SimCam,
    SimImagePlugin,
    SimROIPlugin,
    SimStatsPlugin,
    SimTransformPlugin,
)

__all__ = [
    "SimDetector",
    "SimCam",
    "SimImagePlugin",
    "SimROIPlugin",
    "SimStatsPlugin",
    "SimTransformPlugin",
]
```

**Step 2: Create generators.py with base classes**

```python
# src/lucid/devices/sim/generators.py
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
```

**Step 3: Commit**

```bash
git add src/lucid/devices/sim/
git commit -m "feat(sim): add image generators for SimDetector

Add base ImageGenerator class and three implementations:
- StaticPatternGenerator: gradient, checker, gaussian patterns
- AnimatedPatternGenerator: sine waves, rotating patterns
- MotorResponsiveGenerator: sample position tracks motor positions

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Create Plugin Components

**Files:**
- Create: `src/lucid/devices/sim/plugins.py`
- Test: `tests/test_sim_areadetector.py`

**Step 1: Write failing test for SimCam**

```python
# tests/test_sim_areadetector.py
"""Tests for SimDetector and related components."""

import pytest


class TestSimCam:
    """Tests for SimCam component."""

    def test_cam_has_acquire_signal(self):
        """SimCam should have an acquire signal."""
        from lucid.devices.sim.plugins import SimCam

        cam = SimCam(name="test_cam")
        assert hasattr(cam, "acquire")
        assert cam.acquire.get() == 0

    def test_cam_has_image_settings(self):
        """SimCam should have image size and type settings."""
        from lucid.devices.sim.plugins import SimCam

        cam = SimCam(name="test_cam")
        assert cam.size_x.get() == 256
        assert cam.size_y.get() == 256
        assert cam.data_type.get() == "uint8"

    def test_cam_has_acquisition_settings(self):
        """SimCam should have exposure and timing settings."""
        from lucid.devices.sim.plugins import SimCam

        cam = SimCam(name="test_cam")
        assert cam.acquire_time.get() == 0.1
        assert cam.acquire_period.get() == 0.2
        assert cam.num_images.get() == 1
        assert cam.image_mode.get() == 0  # Single
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sim_areadetector.py::TestSimCam -v`
Expected: FAIL with "ModuleNotFoundError" or "ImportError"

**Step 3: Write SimCam implementation**

```python
# src/lucid/devices/sim/plugins.py
"""Plugin components for SimDetector."""

from __future__ import annotations

from ophyd import Component, Device, Signal
from ophyd.signal import SignalRO


class SimCam(Device):
    """Simulated camera component.

    Provides acquisition control and image settings without EPICS.
    """

    # Acquisition control
    acquire = Component(Signal, value=0, kind="config")
    acquire_time = Component(Signal, value=0.1, kind="config")
    acquire_period = Component(Signal, value=0.2, kind="config")
    num_images = Component(Signal, value=1, kind="config")
    image_mode = Component(Signal, value=0, kind="config")  # 0=Single, 1=Multiple, 2=Continuous

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
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_sim_areadetector.py::TestSimCam -v`
Expected: PASS

**Step 5: Add tests for other plugins**

```python
# Append to tests/test_sim_areadetector.py

class TestSimImagePlugin:
    """Tests for SimImagePlugin."""

    def test_image_plugin_has_array_data(self):
        """SimImagePlugin should have array_data signal."""
        from lucid.devices.sim.plugins import SimImagePlugin

        plugin = SimImagePlugin(name="test_image")
        assert hasattr(plugin, "array_data")
        assert hasattr(plugin, "enable")

    def test_image_plugin_has_size_signals(self):
        """SimImagePlugin should report array dimensions."""
        from lucid.devices.sim.plugins import SimImagePlugin

        plugin = SimImagePlugin(name="test_image")
        assert plugin.array_size_x.get() == 256
        assert plugin.array_size_y.get() == 256


class TestSimStatsPlugin:
    """Tests for SimStatsPlugin."""

    def test_stats_plugin_has_statistics(self):
        """SimStatsPlugin should have all stat signals."""
        from lucid.devices.sim.plugins import SimStatsPlugin

        plugin = SimStatsPlugin(name="test_stats")
        assert hasattr(plugin, "min_value")
        assert hasattr(plugin, "max_value")
        assert hasattr(plugin, "mean_value")
        assert hasattr(plugin, "sigma")
        assert hasattr(plugin, "centroid_x")
        assert hasattr(plugin, "centroid_y")


class TestSimROIPlugin:
    """Tests for SimROIPlugin."""

    def test_roi_plugin_has_bounds(self):
        """SimROIPlugin should have ROI bounds."""
        from lucid.devices.sim.plugins import SimROIPlugin

        plugin = SimROIPlugin(name="test_roi")
        assert plugin.min_x.get() == 0
        assert plugin.min_y.get() == 0
        assert plugin.size_x.get() == 256
        assert plugin.size_y.get() == 256


class TestSimTransformPlugin:
    """Tests for SimTransformPlugin."""

    def test_transform_plugin_has_controls(self):
        """SimTransformPlugin should have transform controls."""
        from lucid.devices.sim.plugins import SimTransformPlugin

        plugin = SimTransformPlugin(name="test_trans")
        assert plugin.rotation.get() == 0
        assert plugin.flip_x.get() == 0
        assert plugin.flip_y.get() == 0
```

**Step 6: Run all plugin tests**

Run: `pytest tests/test_sim_areadetector.py -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/lucid/devices/sim/plugins.py tests/test_sim_areadetector.py
git commit -m "feat(sim): add plugin components for SimDetector

Add five plugin Device classes:
- SimCam: acquisition control and image settings
- SimImagePlugin: image data output
- SimStatsPlugin: min, max, mean, sigma, centroid
- SimROIPlugin: region of interest extraction
- SimTransformPlugin: rotation and flip controls

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Create SimDetector Device

**Files:**
- Create: `src/lucid/devices/sim/areadetector.py`
- Modify: `tests/test_sim_areadetector.py`

**Step 1: Write failing test for SimDetector structure**

```python
# Append to tests/test_sim_areadetector.py

class TestSimDetector:
    """Tests for SimDetector device."""

    def test_detector_has_all_components(self):
        """SimDetector should have all plugin components."""
        from lucid.devices.sim.areadetector import SimDetector

        det = SimDetector(name="test_det")
        assert hasattr(det, "cam")
        assert hasattr(det, "image")
        assert hasattr(det, "stats")
        assert hasattr(det, "roi1")
        assert hasattr(det, "trans1")

    def test_detector_trigger_returns_status(self):
        """trigger() should return an ophyd Status."""
        from lucid.devices.sim.areadetector import SimDetector

        det = SimDetector(name="test_det")
        status = det.trigger()
        assert hasattr(status, "wait")
        status.wait(timeout=5)
        assert status.success

    def test_detector_generates_image_on_trigger(self):
        """Triggering should populate image.array_data."""
        from lucid.devices.sim.areadetector import SimDetector
        import numpy as np

        det = SimDetector(name="test_det")
        det.trigger().wait(timeout=5)

        data = det.image.array_data.get()
        assert data is not None
        assert isinstance(data, np.ndarray)
        assert data.shape == (256, 256)
        assert data.dtype == np.uint8
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sim_areadetector.py::TestSimDetector::test_detector_has_all_components -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write SimDetector implementation**

```python
# src/lucid/devices/sim/areadetector.py
"""Simulated area detector for testing without EPICS."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from ophyd import Component, Device
from ophyd.status import Status

from lucid.devices.sim.generators import (
    AnimatedPatternGenerator,
    ImageGenerator,
    MotorResponsiveGenerator,
    StaticPatternGenerator,
)
from lucid.devices.sim.plugins import (
    SimCam,
    SimImagePlugin,
    SimROIPlugin,
    SimStatsPlugin,
    SimTransformPlugin,
)


class SimDetector(Device):
    """Simulated area detector with pure ophyd signals.

    A complete area detector simulation that works without EPICS.
    Supports Bluesky plans (trigger/read/describe) and can output
    image data either embedded in events or as file references.

    Components:
        cam: Camera settings and acquisition control
        image: Image data output
        stats: Image statistics (min, max, mean, centroid)
        roi1: Region of interest extraction
        trans1: Image transformations (rotation, flip)

    Example:
        >>> det = SimDetector(name='sim_det')
        >>> det.trigger().wait()
        >>> image = det.image.array_data.get()

        With motors for position-responsive images:
        >>> det = SimDetector(name='sim_det', motors={'x': motor_x, 'y': motor_y})
        >>> det.cam.pattern_mode.set('motor')
    """

    cam = Component(SimCam, "")
    image = Component(SimImagePlugin, "image1:")
    stats = Component(SimStatsPlugin, "Stats1:")
    roi1 = Component(SimROIPlugin, "ROI1:")
    trans1 = Component(SimTransformPlugin, "Trans1:")

    def __init__(
        self,
        name: str,
        motors: dict[str, Any] | None = None,
        data_mode: str = "embedded",
        file_path: str = "/tmp/sim_det",
        **kwargs: Any,
    ) -> None:
        """Initialize SimDetector.

        Args:
            name: Device name.
            motors: Optional dict of {'x': motor, 'y': motor} for motor-responsive mode.
            data_mode: 'embedded' for array data in events, 'file' for file references.
            file_path: Base path for file output (only used if data_mode='file').
            **kwargs: Passed to Device.__init__.
        """
        super().__init__(name=name, **kwargs)

        self._motors = motors or {}
        self._data_mode = data_mode
        self._file_path = Path(file_path)
        self._frame_number = 0

        # Initialize generators
        self._generators: dict[str, ImageGenerator] = {
            "static": StaticPatternGenerator(),
            "animated": AnimatedPatternGenerator(),
            "motor": MotorResponsiveGenerator(motors=self._motors),
        }

    @property
    def data_mode(self) -> str:
        """Get current data mode ('embedded' or 'file')."""
        return self._data_mode

    @data_mode.setter
    def data_mode(self, value: str) -> None:
        """Set data mode."""
        if value not in ("embedded", "file"):
            raise ValueError("data_mode must be 'embedded' or 'file'")
        self._data_mode = value

    def trigger(self) -> Status:
        """Acquire one frame.

        Returns:
            Status that completes when acquisition is done.
        """
        status = Status(obj=self)

        def acquire():
            try:
                # Simulate exposure time
                exposure = self.cam.acquire_time.get()
                time.sleep(exposure)

                # Generate image
                image = self._generate_image()

                # Apply transforms if enabled
                if self.trans1.enable.get():
                    image = self._apply_transforms(image)

                # Update image plugin
                self.image.array_data._readback = image
                self.image.array_size_x._readback = image.shape[1]
                self.image.array_size_y._readback = image.shape[0]
                self.image.unique_id._readback = self._frame_number

                # Compute stats if enabled
                if self.stats.enable.get():
                    self._compute_stats(image)

                # Extract ROI if enabled
                if self.roi1.enable.get():
                    self._extract_roi(image)

                # Update counters
                self._frame_number += 1
                self.cam.array_counter._readback = self._frame_number

                # Handle file output
                if self._data_mode == "file":
                    self._save_to_file(image)

                status.set_finished()
            except Exception as e:
                status.set_exception(e)

        # Run acquisition (synchronous for simplicity)
        acquire()
        return status

    def _generate_image(self) -> np.ndarray:
        """Generate image based on current settings."""
        width = self.cam.size_x.get()
        height = self.cam.size_y.get()
        dtype_str = self.cam.data_type.get()
        dtype = np.dtype(dtype_str)
        pattern_mode = self.cam.pattern_mode.get()
        pattern_type = self.cam.pattern_type.get()

        generator = self._generators.get(pattern_mode)
        if generator is None:
            generator = self._generators["animated"]

        image = generator.generate(
            width=width,
            height=height,
            dtype=dtype,
            frame_number=self._frame_number,
            pattern=pattern_type,
        )

        # Apply gain
        gain = self.cam.gain.get()
        if gain != 1.0:
            image = np.clip(image * gain, 0, np.iinfo(dtype).max).astype(dtype)

        # Apply binning
        bin_x = self.cam.bin_x.get()
        bin_y = self.cam.bin_y.get()
        if bin_x > 1 or bin_y > 1:
            image = self._apply_binning(image, bin_x, bin_y)

        return image

    def _apply_binning(
        self, image: np.ndarray, bin_x: int, bin_y: int
    ) -> np.ndarray:
        """Apply pixel binning to image."""
        h, w = image.shape
        new_h = h // bin_y
        new_w = w // bin_x
        # Reshape and sum for binning
        binned = image[: new_h * bin_y, : new_w * bin_x]
        binned = binned.reshape(new_h, bin_y, new_w, bin_x).sum(axis=(1, 3))
        return binned.astype(image.dtype)

    def _apply_transforms(self, image: np.ndarray) -> np.ndarray:
        """Apply rotation and flip transforms."""
        rotation = self.trans1.rotation.get()
        if rotation == 90:
            image = np.rot90(image, k=1)
        elif rotation == 180:
            image = np.rot90(image, k=2)
        elif rotation == 270:
            image = np.rot90(image, k=3)

        if self.trans1.flip_x.get():
            image = np.fliplr(image)
        if self.trans1.flip_y.get():
            image = np.flipud(image)

        return image

    def _compute_stats(self, image: np.ndarray) -> None:
        """Compute and update image statistics."""
        self.stats.min_value._readback = int(image.min())
        self.stats.max_value._readback = int(image.max())
        self.stats.mean_value._readback = float(image.mean())
        self.stats.sigma._readback = float(image.std())
        self.stats.total._readback = int(image.sum())

        # Compute centroid
        h, w = image.shape
        total = image.sum()
        if total > 0:
            x_coords = np.arange(w)
            y_coords = np.arange(h)
            self.stats.centroid_x._readback = float(
                (image.sum(axis=0) * x_coords).sum() / total
            )
            self.stats.centroid_y._readback = float(
                (image.sum(axis=1) * y_coords).sum() / total
            )

    def _extract_roi(self, image: np.ndarray) -> None:
        """Extract ROI from image."""
        min_x = self.roi1.min_x.get()
        min_y = self.roi1.min_y.get()
        size_x = self.roi1.size_x.get()
        size_y = self.roi1.size_y.get()

        roi = image[min_y : min_y + size_y, min_x : min_x + size_x]
        self.roi1.array_data._readback = roi

    def _save_to_file(self, image: np.ndarray) -> str:
        """Save image to file and return path."""
        self._file_path.mkdir(parents=True, exist_ok=True)
        filename = self._file_path / f"frame_{self._frame_number:06d}.npy"
        np.save(filename, image)
        return str(filename)

    def read(self) -> dict[str, dict[str, Any]]:
        """Read current values for event document."""
        timestamp = time.time()
        data = {}

        # Image data
        image_key = f"{self.name}_image"
        if self._data_mode == "embedded":
            data[image_key] = {
                "value": self.image.array_data.get(),
                "timestamp": timestamp,
            }
        else:
            filename = self._file_path / f"frame_{self._frame_number:06d}.npy"
            data[image_key] = {
                "value": str(filename),
                "timestamp": timestamp,
            }

        # Stats if enabled
        if self.stats.enable.get():
            data[f"{self.name}_stats_mean"] = {
                "value": self.stats.mean_value.get(),
                "timestamp": timestamp,
            }
            data[f"{self.name}_stats_total"] = {
                "value": self.stats.total.get(),
                "timestamp": timestamp,
            }

        return data

    def describe(self) -> dict[str, dict[str, Any]]:
        """Describe data keys for event descriptor."""
        desc = {}

        image_key = f"{self.name}_image"
        if self._data_mode == "embedded":
            desc[image_key] = {
                "source": f"SIM:{self.name}",
                "dtype": "array",
                "shape": [self.cam.size_y.get(), self.cam.size_x.get()],
                "dtype_str": self.cam.data_type.get(),
            }
        else:
            desc[image_key] = {
                "source": f"SIM:{self.name}",
                "dtype": "string",
                "shape": [],
                "external": "FILESTORE:",
            }

        if self.stats.enable.get():
            desc[f"{self.name}_stats_mean"] = {
                "source": f"SIM:{self.name}:Stats1",
                "dtype": "number",
                "shape": [],
            }
            desc[f"{self.name}_stats_total"] = {
                "source": f"SIM:{self.name}:Stats1",
                "dtype": "integer",
                "shape": [],
            }

        return desc

    def stage(self) -> list[object]:
        """Prepare for acquisition."""
        self._frame_number = 0
        self.cam.array_counter._readback = 0
        if self._data_mode == "file":
            self._file_path.mkdir(parents=True, exist_ok=True)
        return [self]

    def unstage(self) -> list[object]:
        """Clean up after acquisition."""
        return [self]
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sim_areadetector.py::TestSimDetector -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/lucid/devices/sim/areadetector.py tests/test_sim_areadetector.py
git commit -m "feat(sim): add SimDetector device class

Full-featured simulated area detector with:
- Plugin components (cam, image, stats, roi, trans)
- Three image generation modes (static, animated, motor-responsive)
- Bluesky interface (trigger, read, describe, stage, unstage)
- Embedded or file-based data output modes

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Add Advanced Tests

**Files:**
- Modify: `tests/test_sim_areadetector.py`

**Step 1: Add tests for pattern modes**

```python
# Append to tests/test_sim_areadetector.py

class TestSimDetectorPatterns:
    """Tests for different image generation patterns."""

    def test_static_gradient_pattern(self):
        """Static gradient pattern should produce horizontal gradient."""
        from lucid.devices.sim.areadetector import SimDetector
        import numpy as np

        det = SimDetector(name="test_det")
        det.cam.pattern_mode.set("static")
        det.cam.pattern_type.set("gradient")
        det.trigger().wait(timeout=5)

        data = det.image.array_data.get()
        # Gradient: first column should be 0, last column should be max
        assert data[0, 0] == 0
        assert data[0, -1] == 255

    def test_animated_pattern_changes(self):
        """Animated pattern should change between frames."""
        from lucid.devices.sim.areadetector import SimDetector
        import numpy as np

        det = SimDetector(name="test_det")
        det.cam.pattern_mode.set("animated")
        det.cam.acquire_time.set(0.001)  # Fast for testing

        det.trigger().wait(timeout=5)
        frame1 = det.image.array_data.get().copy()

        det.trigger().wait(timeout=5)
        frame2 = det.image.array_data.get().copy()

        # Frames should be different
        assert not np.array_equal(frame1, frame2)

    def test_motor_responsive_pattern(self):
        """Motor-responsive pattern should change with motor position."""
        from lucid.devices.sim.areadetector import SimDetector
        from ophyd.sim import SynAxis
        import numpy as np

        motor_x = SynAxis(name="motor_x")
        motor_y = SynAxis(name="motor_y")

        det = SimDetector(
            name="test_det",
            motors={"x": motor_x, "y": motor_y},
        )
        det.cam.pattern_mode.set("motor")
        det.cam.acquire_time.set(0.001)

        # Image at center
        motor_x.set(0).wait()
        motor_y.set(0).wait()
        det.trigger().wait(timeout=5)
        center_image = det.image.array_data.get().copy()

        # Image with motor moved
        motor_x.set(50).wait()
        det.trigger().wait(timeout=5)
        moved_image = det.image.array_data.get().copy()

        # Images should be different
        assert not np.array_equal(center_image, moved_image)


class TestSimDetectorStats:
    """Tests for statistics computation."""

    def test_stats_computed_on_trigger(self):
        """Stats should be computed after trigger."""
        from lucid.devices.sim.areadetector import SimDetector

        det = SimDetector(name="test_det")
        det.stats.enable.set(1)
        det.trigger().wait(timeout=5)

        assert det.stats.min_value.get() >= 0
        assert det.stats.max_value.get() <= 255
        assert 0 < det.stats.mean_value.get() < 255
        assert det.stats.sigma.get() > 0

    def test_centroid_computed(self):
        """Centroid should be computed for gaussian pattern."""
        from lucid.devices.sim.areadetector import SimDetector

        det = SimDetector(name="test_det")
        det.cam.pattern_mode.set("static")
        det.cam.pattern_type.set("gaussian")
        det.stats.enable.set(1)
        det.trigger().wait(timeout=5)

        # Gaussian centered at (128, 128) should have centroid near center
        cx = det.stats.centroid_x.get()
        cy = det.stats.centroid_y.get()
        assert 100 < cx < 156  # Near center
        assert 100 < cy < 156


class TestSimDetectorTransforms:
    """Tests for image transformations."""

    def test_rotation_90(self):
        """90-degree rotation should work."""
        from lucid.devices.sim.areadetector import SimDetector
        import numpy as np

        det = SimDetector(name="test_det")
        det.cam.pattern_mode.set("static")
        det.cam.pattern_type.set("gradient")
        det.cam.acquire_time.set(0.001)

        # Get original
        det.trigger().wait(timeout=5)
        original = det.image.array_data.get().copy()

        # Rotate 90
        det.trans1.enable.set(1)
        det.trans1.rotation.set(90)
        det.trigger().wait(timeout=5)
        rotated = det.image.array_data.get()

        # Original gradient is horizontal, rotated should be vertical
        assert not np.array_equal(original, rotated)
        # Check dimensions swapped
        assert rotated.shape == (256, 256)  # Still square

    def test_flip_x(self):
        """Horizontal flip should reverse columns."""
        from lucid.devices.sim.areadetector import SimDetector
        import numpy as np

        det = SimDetector(name="test_det")
        det.cam.pattern_mode.set("static")
        det.cam.pattern_type.set("gradient")
        det.cam.acquire_time.set(0.001)

        det.trigger().wait(timeout=5)
        original = det.image.array_data.get().copy()

        det.trans1.enable.set(1)
        det.trans1.flip_x.set(1)
        det.trigger().wait(timeout=5)
        flipped = det.image.array_data.get()

        # Gradient should now be reversed
        assert flipped[0, 0] == 255
        assert flipped[0, -1] == 0


class TestSimDetectorROI:
    """Tests for ROI extraction."""

    def test_roi_extracts_region(self):
        """ROI should extract specified region."""
        from lucid.devices.sim.areadetector import SimDetector
        import numpy as np

        det = SimDetector(name="test_det")
        det.roi1.enable.set(1)
        det.roi1.min_x.set(50)
        det.roi1.min_y.set(50)
        det.roi1.size_x.set(100)
        det.roi1.size_y.set(100)

        det.trigger().wait(timeout=5)
        roi_data = det.roi1.array_data.get()

        assert roi_data is not None
        assert roi_data.shape == (100, 100)
```

**Step 2: Run all tests**

Run: `pytest tests/test_sim_areadetector.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add tests/test_sim_areadetector.py
git commit -m "test(sim): add comprehensive tests for SimDetector

Add tests for:
- Pattern modes (static, animated, motor-responsive)
- Statistics computation (min, max, mean, centroid)
- Image transformations (rotation, flip)
- ROI extraction

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Integrate with MockBackend

**Files:**
- Modify: `src/lucid/devices/backends/mock.py`
- Modify: `tests/test_sim_areadetector.py`

**Step 1: Write failing integration test**

```python
# Append to tests/test_sim_areadetector.py

class TestMockBackendIntegration:
    """Tests for SimDetector integration with MockBackend."""

    def test_sim_det_in_mock_backend(self):
        """MockBackend should include sim_det device."""
        from lucid.devices.backends.mock import MockBackend
        from lucid.devices.model import DeviceCategory

        backend = MockBackend()
        backend.connect()

        devices = backend.list_devices(category=DeviceCategory.CAMERA)
        names = [d.name for d in devices]
        assert "sim_det" in names

    def test_sim_det_ophyd_device_accessible(self):
        """sim_det ophyd device should be accessible from backend."""
        from lucid.devices.backends.mock import MockBackend
        from lucid.devices.sim.areadetector import SimDetector

        backend = MockBackend()
        backend.connect()

        ophyd_dev = backend.get_ophyd_device("sim_det")
        assert ophyd_dev is not None
        assert isinstance(ophyd_dev, SimDetector)

    def test_sim_det_motor_responsive_with_backend_motors(self):
        """sim_det should respond to sample_x/sample_y motors from backend."""
        from lucid.devices.backends.mock import MockBackend
        import numpy as np

        backend = MockBackend()
        backend.connect()

        sim_det = backend.get_ophyd_device("sim_det")
        sample_x = backend.get_ophyd_device("sample_x")
        sample_y = backend.get_ophyd_device("sample_y")

        # Set motor mode and acquire
        sim_det.cam.pattern_mode.set("motor")
        sim_det.cam.acquire_time.set(0.001)

        sample_x.set(0).wait()
        sample_y.set(0).wait()
        sim_det.trigger().wait(timeout=5)
        center = sim_det.image.array_data.get().copy()

        sample_x.set(50).wait()
        sim_det.trigger().wait(timeout=5)
        moved = sim_det.image.array_data.get()

        assert not np.array_equal(center, moved)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sim_areadetector.py::TestMockBackendIntegration::test_sim_det_in_mock_backend -v`
Expected: FAIL with "sim_det not in names"

**Step 3: Modify MockBackend to include SimDetector**

Add the following to `src/lucid/devices/backends/mock.py` at the end of `_create_simulated_devices()` method (before `logger.debug`):

```python
        # === Area Detector ===
        try:
            from lucid.devices.sim.areadetector import SimDetector

            sim_det = SimDetector(
                name="sim_det",
                motors={
                    "x": self._ophyd_devices.get("sample_x"),
                    "y": self._ophyd_devices.get("sample_y"),
                },
            )
            sim_det_info = DeviceInfo(
                name="sim_det",
                description="Simulated area detector for testing",
                category=DeviceCategory.CAMERA,
                device_class="lucid.devices.sim.areadetector.SimDetector",
                connection_type=ConnectionType.SIMULATED,
                prefix="sim_det",
                location="Detector Arm",
                tags=["detector", "camera", "area", "simulated"],
                metadata={
                    "size_x": 256,
                    "size_y": 256,
                    "data_type": "uint8",
                },
            )
            sim_det_info._ophyd_device = sim_det
            self._add_device_internal(sim_det_info)
            self._ophyd_devices["sim_det"] = sim_det
        except ImportError:
            logger.warning("SimDetector not available")
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sim_areadetector.py::TestMockBackendIntegration -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/lucid/devices/backends/mock.py tests/test_sim_areadetector.py
git commit -m "feat(mock): integrate SimDetector into MockBackend

MockBackend now automatically creates a sim_det area detector
linked to sample_x/sample_y motors for motor-responsive imaging.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Add Bluesky Plan Integration Tests

**Files:**
- Modify: `tests/test_sim_areadetector.py`

**Step 1: Add Bluesky integration tests**

```python
# Append to tests/test_sim_areadetector.py

class TestBlueskyIntegration:
    """Tests for SimDetector with Bluesky plans."""

    @pytest.fixture
    def sim_det(self):
        """Create a SimDetector for testing."""
        from lucid.devices.sim.areadetector import SimDetector

        det = SimDetector(name="sim_det")
        det.cam.acquire_time.set(0.001)  # Fast acquisitions
        return det

    def test_count_plan(self, sim_det):
        """SimDetector should work with bp.count."""
        pytest.importorskip("bluesky")
        from bluesky import RunEngine
        from bluesky.plans import count

        RE = RunEngine({})
        docs = []

        def collector(name, doc):
            docs.append((name, doc))

        RE.subscribe(collector)
        RE(count([sim_det], num=3))

        # Should have start, descriptor, 3 events, stop
        names = [d[0] for d in docs]
        assert names.count("event") == 3
        assert "start" in names
        assert "stop" in names

    def test_scan_with_motor(self, sim_det):
        """SimDetector should work with bp.scan."""
        pytest.importorskip("bluesky")
        from bluesky import RunEngine
        from bluesky.plans import scan
        from ophyd.sim import SynAxis

        motor = SynAxis(name="motor")
        RE = RunEngine({})
        docs = []

        def collector(name, doc):
            docs.append((name, doc))

        RE.subscribe(collector)
        RE(scan([sim_det], motor, 0, 10, 5))

        # Should have 5 events
        names = [d[0] for d in docs]
        assert names.count("event") == 5

    def test_event_contains_image_data(self, sim_det):
        """Events should contain image data in embedded mode."""
        pytest.importorskip("bluesky")
        from bluesky import RunEngine
        from bluesky.plans import count
        import numpy as np

        RE = RunEngine({})
        events = []

        def collector(name, doc):
            if name == "event":
                events.append(doc)

        RE.subscribe(collector)
        RE(count([sim_det], num=1))

        assert len(events) == 1
        data = events[0]["data"]
        assert "sim_det_image" in data
        image = data["sim_det_image"]
        assert isinstance(image, np.ndarray)
        assert image.shape == (256, 256)
```

**Step 2: Run Bluesky tests (these may skip if bluesky not installed)**

Run: `pytest tests/test_sim_areadetector.py::TestBlueskyIntegration -v`
Expected: PASS (or skip if bluesky unavailable)

**Step 3: Commit**

```bash
git add tests/test_sim_areadetector.py
git commit -m "test(sim): add Bluesky plan integration tests

Test SimDetector with bp.count and bp.scan plans,
verify event documents contain image data.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Update Package __init__.py

**Files:**
- Modify: `src/lucid/devices/sim/__init__.py`

**Step 1: Verify __init__.py exports are correct**

The __init__.py was created in Task 1. Verify it can be imported:

Run: `python -c "from lucid.devices.sim import SimDetector; print('OK')"`
Expected: "OK"

If import fails, fix the __init__.py to handle missing dependencies gracefully:

```python
# src/lucid/devices/sim/__init__.py
"""Simulated ophyd devices for testing and development."""

try:
    from lucid.devices.sim.areadetector import SimDetector
    from lucid.devices.sim.plugins import (
        SimCam,
        SimImagePlugin,
        SimROIPlugin,
        SimStatsPlugin,
        SimTransformPlugin,
    )

    __all__ = [
        "SimDetector",
        "SimCam",
        "SimImagePlugin",
        "SimROIPlugin",
        "SimStatsPlugin",
        "SimTransformPlugin",
    ]
except ImportError as e:
    import warnings

    warnings.warn(f"SimDetector components not available: {e}")
    __all__ = []
```

**Step 2: Commit if changes needed**

```bash
git add src/lucid/devices/sim/__init__.py
git commit -m "fix(sim): handle import errors gracefully in __init__

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Final Verification

**Step 1: Run full test suite**

Run: `pytest tests/test_sim_areadetector.py -v --tb=short`
Expected: All tests PASS

**Step 2: Run all project tests to ensure no regressions**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS (or expected skips)

**Step 3: Verify git status is clean**

Run: `git status`
Expected: "nothing to commit, working tree clean"

---

## Summary

This plan implements a SimDetector with:
- 7 commits of incremental, tested changes
- Full TDD approach with tests before implementation
- Clean integration with existing MockBackend
- Support for Bluesky plans

**Files created:**
- `src/lucid/devices/sim/__init__.py`
- `src/lucid/devices/sim/generators.py`
- `src/lucid/devices/sim/plugins.py`
- `src/lucid/devices/sim/areadetector.py`
- `tests/test_sim_areadetector.py`

**Files modified:**
- `src/lucid/devices/backends/mock.py`

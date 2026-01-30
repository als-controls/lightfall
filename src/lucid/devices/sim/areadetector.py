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

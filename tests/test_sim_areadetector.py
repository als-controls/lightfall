"""Tests for SimDetector and related components."""

import pytest

pytest.importorskip("ophyd")

import numpy as np

from lucid.devices.sim.areadetector import SimDetector
from lucid.devices.sim.plugins import (
    SimCam,
    SimImagePlugin,
    SimROIPlugin,
    SimStatsPlugin,
    SimTransformPlugin,
)


class TestSimCam:
    """Tests for SimCam component."""

    def test_cam_has_acquire_signal(self) -> None:
        """SimCam should have an acquire signal."""
        cam = SimCam(name="test_cam")
        assert hasattr(cam, "acquire")
        assert cam.acquire.get() == 0

    def test_cam_has_image_settings(self) -> None:
        """SimCam should have image size and type settings."""
        cam = SimCam(name="test_cam")
        assert cam.size_x.get() == 256
        assert cam.size_y.get() == 256
        assert cam.data_type.get() == "uint8"

    def test_cam_has_acquisition_settings(self) -> None:
        """SimCam should have exposure and timing settings."""
        cam = SimCam(name="test_cam")
        assert cam.acquire_time.get() == 0.1
        assert cam.acquire_period.get() == 0.2
        assert cam.num_images.get() == 1
        assert cam.image_mode.get() == 0


class TestSimImagePlugin:
    """Tests for SimImagePlugin."""

    def test_image_plugin_has_array_data(self) -> None:
        """SimImagePlugin should have array_data signal."""
        plugin = SimImagePlugin(name="test_image")
        assert hasattr(plugin, "array_data")
        assert hasattr(plugin, "enable")

    def test_image_plugin_has_size_signals(self) -> None:
        """SimImagePlugin should report array dimensions."""
        plugin = SimImagePlugin(name="test_image")
        assert plugin.array_size_x.get() == 256
        assert plugin.array_size_y.get() == 256


class TestSimStatsPlugin:
    """Tests for SimStatsPlugin."""

    def test_stats_plugin_has_statistics(self) -> None:
        """SimStatsPlugin should have all stat signals."""
        plugin = SimStatsPlugin(name="test_stats")
        assert hasattr(plugin, "min_value")
        assert hasattr(plugin, "max_value")
        assert hasattr(plugin, "mean_value")
        assert hasattr(plugin, "sigma")
        assert hasattr(plugin, "centroid_x")
        assert hasattr(plugin, "centroid_y")


class TestSimROIPlugin:
    """Tests for SimROIPlugin."""

    def test_roi_plugin_has_bounds(self) -> None:
        """SimROIPlugin should have ROI bounds."""
        plugin = SimROIPlugin(name="test_roi")
        assert plugin.min_x.get() == 0
        assert plugin.min_y.get() == 0
        assert plugin.size_x.get() == 256
        assert plugin.size_y.get() == 256


class TestSimTransformPlugin:
    """Tests for SimTransformPlugin."""

    def test_transform_plugin_has_controls(self) -> None:
        """SimTransformPlugin should have transform controls."""
        plugin = SimTransformPlugin(name="test_trans")
        assert plugin.rotation.get() == 0
        assert plugin.flip_x.get() == 0
        assert plugin.flip_y.get() == 0


class TestSimDetector:
    """Tests for SimDetector device."""

    def test_detector_has_all_components(self) -> None:
        """SimDetector should have all plugin components."""
        det = SimDetector(name="test_det")
        assert hasattr(det, "cam")
        assert hasattr(det, "image")
        assert hasattr(det, "stats")
        assert hasattr(det, "roi1")
        assert hasattr(det, "trans1")

    def test_detector_trigger_returns_status(self) -> None:
        """trigger() should return an ophyd Status."""
        det = SimDetector(name="test_det")
        status = det.trigger()
        assert hasattr(status, "wait")
        status.wait(timeout=5)
        assert status.success

    def test_detector_generates_image_on_trigger(self) -> None:
        """Triggering should populate image.array_data."""
        det = SimDetector(name="test_det")
        det.trigger().wait(timeout=5)

        data = det.image.array_data.get()
        assert data is not None
        assert isinstance(data, np.ndarray)
        assert data.shape == (256, 256)
        assert data.dtype == np.uint8

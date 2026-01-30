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

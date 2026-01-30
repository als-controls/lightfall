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


class TestSimDetectorPatterns:
    """Tests for different image generation patterns."""

    def test_static_gradient_pattern(self) -> None:
        """Static gradient pattern should produce horizontal gradient."""
        det = SimDetector(name="test_det")
        det.cam.pattern_mode.set("static")
        det.cam.pattern_type.set("gradient")
        det.trigger().wait(timeout=5)

        data = det.image.array_data.get()
        # Gradient: first column should be 0, last column should be max
        assert data[0, 0] == 0
        assert data[0, -1] == 255

    def test_animated_pattern_changes(self) -> None:
        """Animated pattern should change between frames."""
        det = SimDetector(name="test_det")
        det.cam.pattern_mode.set("animated")
        det.cam.acquire_time.set(0.001)  # Fast for testing

        det.trigger().wait(timeout=5)
        frame1 = det.image.array_data.get().copy()

        det.trigger().wait(timeout=5)
        frame2 = det.image.array_data.get().copy()

        # Frames should be different
        assert not np.array_equal(frame1, frame2)

    def test_motor_responsive_pattern(self) -> None:
        """Motor-responsive pattern should change with motor position."""
        from ophyd.sim import SynAxis

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

    def test_stats_computed_on_trigger(self) -> None:
        """Stats should be computed after trigger."""
        det = SimDetector(name="test_det")
        det.stats.enable.set(1)
        det.trigger().wait(timeout=5)

        assert det.stats.min_value.get() >= 0
        assert det.stats.max_value.get() <= 255
        assert 0 < det.stats.mean_value.get() < 255
        assert det.stats.sigma.get() > 0

    def test_centroid_computed(self) -> None:
        """Centroid should be computed for gaussian pattern."""
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

    def test_rotation_90(self) -> None:
        """90-degree rotation should work."""
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

    def test_flip_x(self) -> None:
        """Horizontal flip should reverse columns."""
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

    def test_roi_extracts_region(self) -> None:
        """ROI should extract specified region."""
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


class TestMockBackendIntegration:
    """Tests for SimDetector integration with MockBackend."""

    def test_sim_det_in_mock_backend(self) -> None:
        """MockBackend should include sim_det device."""
        from lucid.devices.backends.mock import MockBackend
        from lucid.devices.model import DeviceCategory

        backend = MockBackend()
        backend.connect()

        devices = backend.list_devices(category=DeviceCategory.CAMERA)
        names = [d.name for d in devices]
        assert "sim_det" in names

    def test_sim_det_ophyd_device_accessible(self) -> None:
        """sim_det ophyd device should be accessible from backend."""
        from lucid.devices.backends.mock import MockBackend

        backend = MockBackend()
        backend.connect()

        ophyd_dev = backend.get_ophyd_device("sim_det")
        assert ophyd_dev is not None
        assert isinstance(ophyd_dev, SimDetector)

    def test_sim_det_motor_responsive_with_backend_motors(self) -> None:
        """sim_det should respond to sample_x/sample_y motors from backend."""
        from lucid.devices.backends.mock import MockBackend

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


class TestBlueskyIntegration:
    """Tests for SimDetector with Bluesky plans."""

    @pytest.fixture
    def sim_det(self):
        """Create a SimDetector for testing."""
        det = SimDetector(name="sim_det")
        det.cam.acquire_time.set(0.001)  # Fast acquisitions
        return det

    def test_count_plan(self, sim_det) -> None:
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
        assert "descriptor" in names
        assert "stop" in names

    def test_scan_with_motor(self, sim_det) -> None:
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

    def test_event_contains_image_data(self, sim_det) -> None:
        """Events should contain image data in embedded mode."""
        pytest.importorskip("bluesky")
        from bluesky import RunEngine
        from bluesky.plans import count

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

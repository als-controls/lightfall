"""Tests for OphydImageView."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from lucid.ui.widgets.camera.image_view import OphydImageView


@pytest.fixture()
def qapp():
    """Ensure QApplication exists."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_mock_device(image_data: np.ndarray | None = None):
    """Create a mock ophyd device with image plugin."""
    if image_data is None:
        image_data = np.random.randint(0, 255, (480, 640), dtype=np.uint8)

    device = MagicMock()
    device.name = "sim_det"
    device.image1.array_data.get.return_value = image_data
    device.image1.width.get.return_value = image_data.shape[1]
    device.image1.height.get.return_value = image_data.shape[0]
    return device


class TestOphydImageViewBasic:
    """Basic display tests."""

    def test_has_axes(self, qapp):
        """PlotItem should provide visible axes."""
        device = _make_mock_device()
        view = OphydImageView(device)

        # PlotItem provides axes
        assert view._plot_item is not None
        assert view._plot_item.axes["bottom"]["item"].isVisible()
        assert view._plot_item.axes["left"]["item"].isVisible()
        view.close()

    def test_image_orientation_y_inverted(self, qapp):
        """Y axis should be inverted so row 0 is at the top."""
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._plot_item.getViewBox().yInverted()
        view.close()

    def test_histogram_present(self, qapp):
        """Histogram LUT widget should be present."""
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._histogram is not None
        view.close()


class TestLUTBehavior:
    """LUT should auto-scale on first frame, then stay stable."""

    def test_first_frame_sets_levels(self, qapp):
        """First frame should auto-scale the histogram levels."""
        data = np.zeros((100, 100), dtype=np.uint16)
        data[50, 50] = 1000
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        assert view._first_frame is False

        levels = view._histogram.getLevels()
        assert levels[0] < levels[1]
        view.close()

    def test_subsequent_frames_preserve_levels(self, qapp):
        """After first frame, levels should not change on new frames."""
        data1 = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data1)
        view = OphydImageView(device)

        view._display_array(data1)
        levels_after_first = view._histogram.getLevels()

        data2 = np.random.randint(500, 1000, (100, 100), dtype=np.uint16)
        view._display_array(data2)
        levels_after_second = view._histogram.getLevels()

        assert levels_after_first == levels_after_second
        view.close()

    def test_reset_lut_flag(self, qapp):
        """reset_lut() should re-enable auto-levels for next frame."""
        data = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        assert view._first_frame is False

        view.reset_lut()
        assert view._first_frame is True
        view.close()


class TestToolbar:
    """Toolbar buttons above the image."""

    def test_toolbar_buttons_exist(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._reset_lut_btn is not None
        assert view._reset_axes_btn is not None
        assert view._log_intensity_btn is not None
        assert view._log_intensity_btn.isCheckable()
        view.close()

    def test_reset_lut_button_resets_flag(self, qapp):
        data = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        assert view._first_frame is False

        view._reset_lut_btn.click()
        assert view._first_frame is True
        view.close()

    def test_reset_axes_button_calls_autorange(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)

        view._reset_axes_btn.click()
        view.close()


class TestLogIntensity:
    """Log intensity: displayed image is log-scaled, histogram shows true values."""

    def test_log_mode_off_by_default(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        assert view._log_mode is False
        view.close()

    def test_toggle_log_mode(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        view._log_intensity_btn.setChecked(True)
        assert view._log_mode is True
        view._log_intensity_btn.setChecked(False)
        assert view._log_mode is False
        view.close()

    def test_log_mode_displays_log_data(self, qapp):
        """ImageItem should contain log1p(data) when log mode is on."""
        data = np.full((100, 100), 100, dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._log_intensity_btn.setChecked(True)
        view._display_array(data)

        displayed = view._image_item.image
        expected = np.log1p(data.astype(np.float64))
        np.testing.assert_allclose(displayed, expected, rtol=1e-5)
        view.close()

    def test_linear_mode_displays_raw_data(self, qapp):
        """ImageItem should contain raw data when log mode is off."""
        data = np.full((100, 100), 100, dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._log_intensity_btn.setChecked(False)
        view._display_array(data)

        displayed = view._image_item.image
        np.testing.assert_array_equal(displayed, data)
        view.close()

    def test_histogram_levels_in_real_units(self, qapp):
        """Histogram level handles should operate in real intensity units."""
        data = np.random.randint(10, 1000, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        levels_linear = view._histogram.getLevels()

        view._log_intensity_btn.setChecked(True)
        view._display_array(data)
        levels_log = view._histogram.getLevels()

        # Histogram levels stay in real units regardless of log mode
        assert abs(levels_linear[0] - levels_log[0]) < 1.0
        assert abs(levels_linear[1] - levels_log[1]) < 1.0
        view.close()

    def test_raw_image_cached(self, qapp):
        """_raw_image should always contain the original un-transformed data."""
        data = np.full((100, 100), 42, dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._log_intensity_btn.setChecked(True)
        view._display_array(data)
        np.testing.assert_array_equal(view._raw_image, data)
        view.close()


class TestCrosshair:
    """Crosshair and coordinate display."""

    def test_crosshair_lines_exist(self, qapp):
        from pyqtgraph import InfiniteLine

        device = _make_mock_device()
        view = OphydImageView(device)

        assert isinstance(view._vline, InfiniteLine)
        assert isinstance(view._hline, InfiniteLine)
        assert not view._vline.isVisible()
        assert not view._hline.isVisible()
        view.close()

    def test_coords_label_exists(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._coords_label is not None
        assert view._coords_label.text() == ""
        view.close()

    def test_format_coordinates(self, qapp):
        """_format_coordinates should produce x=... y=... I=... string."""
        data = np.ones((100, 100), dtype=np.uint16) * 42
        device = _make_mock_device(data)
        view = OphydImageView(device)
        view._display_array(data)

        text = view._format_coordinates(50.0, 25.0)
        assert "x=50.0" in text
        assert "y=25.0" in text
        assert "I=42" in text
        view.close()

    def test_format_coordinates_out_of_bounds(self, qapp):
        """Out-of-bounds coordinates should return empty string."""
        data = np.ones((100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)
        view._display_array(data)

        assert view._format_coordinates(-1, 50) == ""
        assert view._format_coordinates(50, 200) == ""
        view.close()


class TestROIStats:
    """Hardware ROI statistics display."""

    def _make_device_with_stats(self):
        device = _make_mock_device()
        stats = MagicMock()
        stats.min_value.get.return_value = 10
        stats.max_value.get.return_value = 950
        stats.mean_value.get.return_value = 123.4
        stats.total.get.return_value = 1234000
        stats.centroid_x.get.return_value = 320.5
        stats.centroid_y.get.return_value = 240.1
        device.roi_stat1 = stats
        return device

    def test_stats_overlay_shown_when_available(self, qapp):
        device = self._make_device_with_stats()
        view = OphydImageView(device)
        view._update_roi_stats()
        text = view._stats_text.toPlainText()
        assert "950" in text
        view.close()

    def test_stats_overlay_hidden_when_no_plugin(self, qapp):
        device = _make_mock_device()
        if hasattr(device, "roi_stat1"):
            del device.roi_stat1
        view = OphydImageView(device)
        view._update_roi_stats()
        assert not view._stats_text.isVisible()
        view.close()

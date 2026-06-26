"""Tests for OphydImageView."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QProgressBar

from lightfall.ui.widgets.camera.dark_frames import DarkFrameManager
from lightfall.ui.widgets.camera.image_view import OphydImageView


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

    def test_image_orientation_row_major(self, qapp):
        """ImageItem should use row-major axis order (array (row, col) = (y, x))."""
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._image_item.axisOrder == "row-major"
        # No Y inversion needed with row-major
        assert not view._plot_item.getViewBox().yInverted()
        view.close()

    def test_histogram_present(self, qapp):
        """Histogram LUT widget should be present."""
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._histogram is not None
        view.close()


class TestToolbarInjection:
    """Panels embedding the view (e.g. XPCS) inject ROI/mask tools into the
    image toolbar via a public hook."""

    def test_add_toolbar_action_inserts_button_before_stretch(self, qapp):
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QToolButton

        view = OphydImageView(_make_mock_device())
        act = QAction("Add ROI", view)
        btn = view.add_toolbar_action(act)
        assert isinstance(btn, QToolButton)
        assert btn.defaultAction() is act
        # inserted with the other tools, not pushed past the trailing stretch
        idx = view._toolbar.indexOf(btn)
        assert idx == view._toolbar.count() - 2
        view.close()

    def test_add_toolbar_action_with_menu_is_instant_popup(self, qapp):
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu, QToolButton

        view = OphydImageView(_make_mock_device())
        menu = QMenu()
        menu.addAction("Add mask")
        act = QAction("Mask", view)
        act.setMenu(menu)
        btn = view.add_toolbar_action(act)
        assert btn.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
        assert btn.menu() is menu
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

    def test_reset_lut_recalculates_levels(self, qapp):
        """reset_lut() should recalculate levels from current image."""
        data = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        # Manually set levels to something wrong
        view._histogram.setLevels(0, 1)

        view.reset_lut()
        lo, hi = view._histogram.getLevels()
        # Should have recalculated from the data
        assert hi > 1
        view.close()

    def test_dragging_levels_maps_linearly_in_linear_mode(self, qapp):
        """Dragging the histogram level bars must map levels 1:1 to the image.

        Regression: HistogramLUTItem.sigLevelsChanged emits the item itself as
        its argument. Connected directly to _apply_display_levels(log_mode=...),
        that truthy object was treated as log_mode=True, so during a drag the
        displayed image was momentarily log1p-scaled in linear mode.
        """
        data = np.random.randint(10, 1000, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)
        view._display_array(data)
        assert view._log_mode is False

        view._histogram.setLevels(100.0, 800.0)
        # Reproduce exactly what a drag does (HistogramLUTItem.regionChanging):
        view._histogram.sigLevelsChanged.emit(view._histogram)

        img_lo, img_hi = view._image_item.levels
        assert img_lo == pytest.approx(100.0)
        assert img_hi == pytest.approx(800.0)
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

    def test_reset_lut_button_recalculates(self, qapp):
        data = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        view._histogram.setLevels(0, 1)

        view._reset_lut_btn.click()
        lo, hi = view._histogram.getLevels()
        assert hi > 1
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

        # With row-major, view (x, y) maps to array[int(y), int(x)]
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
        # Simulate what _apply_frame does with ROI stats
        stats = view._read_roi_stats()
        assert stats is not None
        view._stats_text.setText("\n".join(stats))
        view._stats_text.setVisible(True)
        text = view._stats_text.toPlainText()
        assert "950" in text
        view.close()

    def test_stats_overlay_hidden_when_no_plugin(self, qapp):
        device = _make_mock_device()
        if hasattr(device, "roi_stat1"):
            del device.roi_stat1
        view = OphydImageView(device)
        stats = view._read_roi_stats()
        assert stats is None
        view.close()


class TestProgressBar:
    """Acquisition progress tracking."""

    def test_progress_bar_exists_and_hidden(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        view.show()
        assert isinstance(view._progress_bar, QProgressBar)
        assert not view._progress_bar.isVisible()
        view.close()

    def test_update_progress_from_cam(self, qapp):
        device = _make_mock_device()
        device.cam = MagicMock()
        device.cam.array_counter.get.return_value = 5
        device.cam.num_images.get.return_value = 10
        device.cam.acquire.get.return_value = 1
        device.configure_mock(**{"hdf5": None})

        view = OphydImageView(device)
        view.show()

        progress = view._read_progress()
        assert progress == (5, 10)

        # Simulate _apply_frame setting the progress bar
        view._progress_bar.setMaximum(progress[1])
        view._progress_bar.setValue(progress[0])
        view._progress_bar.setVisible(True)
        assert view._progress_bar.isVisible()
        assert view._progress_bar.value() == 5
        assert view._progress_bar.maximum() == 10
        view.close()

    def test_progress_hides_when_idle(self, qapp):
        device = _make_mock_device()
        device.cam = MagicMock()
        device.cam.acquire.get.return_value = 0
        device.configure_mock(**{"hdf5": None})

        view = OphydImageView(device)
        view.show()

        progress = view._read_progress()
        assert progress is None
        view.close()


class TestBackgroundCorrection:

    def test_bg_correct_button_exists(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        assert view._bg_correct_btn is not None
        assert view._bg_correct_btn.isCheckable()
        view.close()

    def test_bg_correct_subtracts_dark(self, qapp):
        data = np.full((100, 100), 200, dtype=np.uint16)
        dark = np.full((100, 100), 50, dtype=np.float64)
        device = _make_mock_device(data)
        view = OphydImageView(device)
        view.show()

        view._dark_manager._cached_dark = dark
        view._bg_correct_btn.setChecked(True)
        view._display_array(data)

        # _raw_image should be the corrected image (200 - 50 = 150)
        np.testing.assert_allclose(view._raw_image, 150, atol=1)
        view.close()

    def test_bg_correct_off_shows_raw(self, qapp):
        data = np.full((100, 100), 200, dtype=np.uint16)
        dark = np.full((100, 100), 50, dtype=np.float64)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._dark_manager._cached_dark = dark
        view._bg_correct_btn.setChecked(False)
        view._display_array(data)

        np.testing.assert_allclose(view._raw_image, 200, atol=1)
        view.close()

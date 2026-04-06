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



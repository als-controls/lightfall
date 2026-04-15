"""Tests for the SpinnerIndicator widget."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage

from lucid.ui.widgets.runengine_control import SpinnerIndicator


@pytest.fixture
def indicator(qtbot):
    """Create a SpinnerIndicator and register it with qtbot."""
    widget = SpinnerIndicator()
    qtbot.addWidget(widget)
    return widget


class TestPixmapBaking:
    """The three color variants must be pre-baked at construction."""

    def test_widget_is_24x24(self, indicator):
        assert indicator.width() == 24
        assert indicator.height() == 24

    def test_color_pixmap_exists_and_is_24x24(self, indicator):
        pm = indicator._color_pixmap
        assert not pm.isNull()
        assert pm.width() == 24
        assert pm.height() == 24

    def test_gray_pixmap_pixels_have_equal_rgb(self, indicator):
        """In the gray pixmap every opaque pixel must have R == G == B."""
        img: QImage = indicator._gray_pixmap.toImage()
        opaque_pixels = 0
        for y in range(img.height()):
            for x in range(img.width()):
                px = img.pixelColor(x, y)
                if px.alpha() == 0:
                    continue
                opaque_pixels += 1
                assert px.red() == px.green() == px.blue(), (
                    f"Non-gray pixel at ({x},{y}): "
                    f"r={px.red()} g={px.green()} b={px.blue()}"
                )
        assert opaque_pixels > 0, "Gray pixmap has no opaque pixels"

    def test_red_pixmap_pixels_have_zero_green_and_blue(self, indicator):
        """In the red pixmap every opaque pixel must have G == B == 0
        and R should equal the source luminance (non-zero for visible pixels)."""
        img: QImage = indicator._red_pixmap.toImage()
        opaque_pixels = 0
        non_zero_red = 0
        for y in range(img.height()):
            for x in range(img.width()):
                px = img.pixelColor(x, y)
                if px.alpha() == 0:
                    continue
                opaque_pixels += 1
                assert px.green() == 0, (
                    f"Non-zero green at ({x},{y}): {px.green()}"
                )
                assert px.blue() == 0, (
                    f"Non-zero blue at ({x},{y}): {px.blue()}"
                )
                if px.red() > 0:
                    non_zero_red += 1
        assert opaque_pixels > 0, "Red pixmap has no opaque pixels"
        assert non_zero_red > 0, "Red pixmap has no visible red intensity"

    def test_alpha_preserved_across_variants(self, indicator):
        """All three variants must have the same alpha channel as the color pixmap."""
        color_img = indicator._color_pixmap.toImage()
        gray_img = indicator._gray_pixmap.toImage()
        red_img = indicator._red_pixmap.toImage()
        for y in (0, 5, 12, 20):
            for x in (0, 5, 12, 20):
                a_color = color_img.pixelColor(x, y).alpha()
                assert gray_img.pixelColor(x, y).alpha() == a_color
                assert red_img.pixelColor(x, y).alpha() == a_color

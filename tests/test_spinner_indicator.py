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


class TestSpinTimer:
    """Spin timer should run only while in a spinning state."""

    def test_spin_timer_inactive_at_construction(self, indicator):
        assert not indicator._spin_timer.isActive()

    def test_spin_timer_starts_when_running(self, indicator):
        indicator.set_status("running")
        assert indicator._spin_timer.isActive()

    def test_spin_timer_starts_when_stopping(self, indicator):
        indicator.set_status("stopping")
        assert indicator._spin_timer.isActive()

    def test_spin_timer_stops_when_idle(self, indicator):
        indicator.set_status("running")
        assert indicator._spin_timer.isActive()
        indicator.set_status("idle")
        assert not indicator._spin_timer.isActive()

    def test_spin_timer_stops_when_paused(self, indicator):
        indicator.set_status("running")
        indicator.set_status("paused")
        assert not indicator._spin_timer.isActive()

    def test_rotation_advances_on_tick(self, indicator, qtbot):
        indicator.set_status("running")
        start_rotation = indicator._rotation
        # Wait for at least 2 ticks (~66 ms); use 200 ms for safety margin
        qtbot.wait(200)
        assert indicator._rotation != start_rotation

    def test_rotation_preserved_across_pause(self, indicator, qtbot):
        """Pausing stops the timer but should not reset the angle."""
        indicator.set_status("running")
        qtbot.wait(150)
        rotation_at_pause = indicator._rotation
        indicator.set_status("paused")
        assert indicator._rotation == rotation_at_pause


class TestErrorFlash:
    """flash_error must set the flag, auto-clear after 1500 ms, and be re-entrant."""

    def test_flash_inactive_at_construction(self, indicator):
        assert indicator._flash_active is False

    def test_flash_error_sets_flag_and_starts_timer(self, indicator):
        indicator.flash_error()
        assert indicator._flash_active is True
        assert indicator._flash_timer.isActive()

    def test_flash_clears_after_timeout(self, indicator, qtbot):
        indicator.flash_error()
        # Wait slightly longer than the 1500 ms flash duration
        qtbot.wait(1700)
        assert indicator._flash_active is False
        assert not indicator._flash_timer.isActive()

    def test_flash_is_reentrant(self, indicator, qtbot):
        """Calling flash_error during an active flash should restart the timer.

        First call at t=0; second call at t=500. Without re-entrancy the flash
        would clear at t=1500 (1000 ms after second call). With re-entrancy it
        clears at t=500+1500=2000 ms. We check at t=1700.
        """
        indicator.flash_error()
        qtbot.wait(500)
        indicator.flash_error()
        qtbot.wait(1200)  # total elapsed: 1700 ms
        # Should still be active because second call restarted the 1500 ms timer
        assert indicator._flash_active is True
        # And clear after another ~400 ms (at total ~2100 ms)
        qtbot.wait(500)
        assert indicator._flash_active is False

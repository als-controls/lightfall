"""Tests for TheaterOverlay."""

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from lucid.ui.theater.overlay import TheaterOverlay
from lucid.ui.theater.proxy import TheaterProxy


def _wait_animation(overlay, qtbot):
    """Wait until the overlay finishes any in-progress animation."""
    qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)


class TestTheaterOverlayCore:
    """Overlay creation, backdrop, activate/deactivate."""

    def test_starts_hidden(self, parent_widget, qtbot):
        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        assert not overlay.isVisible()

    def test_backdrop_opacity_property(self, parent_widget, qtbot):
        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        assert overlay.backdrop_opacity == 0
        overlay.backdrop_opacity = 100
        assert overlay.backdrop_opacity == 100

    def test_activate_shows_overlay(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)

        # Wait for activation animation to finish
        _wait_animation(overlay, qtbot)

        assert overlay.isVisible()
        assert overlay._active_proxy is proxy
        assert overlay._active_widget is target

    def test_activate_reparents_widget_to_overlay(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)

        _wait_animation(overlay, qtbot)

        assert target.parentWidget() is overlay

    def test_deactivate_returns_widget_to_proxy(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        # Activate
        overlay.activate(proxy)
        _wait_animation(overlay, qtbot)

        # Deactivate
        overlay.deactivate()
        _wait_animation(overlay, qtbot)

        assert not overlay.isVisible()
        assert overlay._active_proxy is None
        assert proxy.currentWidget() is target

    def test_deactivate_noop_when_inactive(self, parent_widget, qtbot):
        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        overlay.deactivate()  # should not raise

    def test_only_one_widget_at_a_time(self, parent_widget, qtbot):
        target1 = QLabel("plot1")
        target2 = QLabel("plot2")
        parent_widget.layout().addWidget(target1)
        parent_widget.layout().addWidget(target2)
        proxy1 = TheaterProxy(target1)
        proxy2 = TheaterProxy(target2)
        parent_widget.layout().addWidget(proxy1)
        parent_widget.layout().addWidget(proxy2)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        # Activate first
        overlay.activate(proxy1)
        _wait_animation(overlay, qtbot)

        # Activate second — first should be returned
        overlay.activate(proxy2)
        _wait_animation(overlay, qtbot)

        assert overlay._active_proxy is proxy2
        assert proxy1.currentWidget() is target1  # returned


class TestTheaterOverlayDismissal:
    """Escape key, backdrop click, collapse button."""

    def _activate_and_wait(self, overlay, proxy, qtbot):
        """Helper: activate and wait for animation to finish."""
        overlay.activate(proxy)
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

    def test_escape_key_deactivates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        qtbot.waitUntil(lambda: not overlay.isVisible(), timeout=2000)

        assert not overlay.isVisible()
        assert proxy.currentWidget() is target

    def test_collapse_button_deactivates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        overlay._collapse_btn.click()
        qtbot.waitUntil(lambda: not overlay.isVisible(), timeout=2000)

        assert not overlay.isVisible()
        assert proxy.currentWidget() is target

    def test_backdrop_click_deactivates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        # Click at (1, 1) — inside the margin, outside the widget
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        qtbot.waitUntil(lambda: not overlay.isVisible(), timeout=2000)

        assert not overlay.isVisible()
        assert proxy.currentWidget() is target

    def test_click_inside_widget_does_not_deactivate(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        # Click in center — should be inside the expanded widget
        center = QPoint(parent_widget.width() // 2, parent_widget.height() // 2)
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=center)

        assert overlay.isVisible()
        assert overlay._active_proxy is proxy


class TestTheaterOverlayResize:
    """Overlay and widget resize when parent resizes."""

    def _activate_and_wait(self, overlay, proxy, qtbot):
        overlay.activate(proxy)
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

    def test_overlay_resizes_with_parent(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        parent_widget.resize(1024, 768)
        QApplication.processEvents()

        assert overlay.width() == 1024
        assert overlay.height() == 768

    def test_widget_fills_new_area_on_resize(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        parent_widget.resize(1024, 768)
        QApplication.processEvents()

        margin = TheaterOverlay._MARGIN
        expected_width = 1024 - 2 * margin
        expected_height = 768 - 2 * margin
        assert target.width() == expected_width
        assert target.height() == expected_height


class TestTheaterOverlayAnimation:
    """Animation setup and final states."""

    def test_activate_starts_animation(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)

        assert overlay._anim_group is not None
        assert overlay._is_animating

    def test_backdrop_reaches_target_opacity_after_activate(
        self, parent_widget, qtbot
    ):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        assert overlay.backdrop_opacity == 150

    def test_widget_reaches_expanded_rect_after_activate(
        self, parent_widget, qtbot
    ):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        expected = overlay._expanded_rect()
        assert target.geometry() == expected

    def test_backdrop_reaches_zero_after_deactivate(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        overlay.activate(proxy)
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        overlay.deactivate()
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        assert overlay.backdrop_opacity == 0

    def test_is_animating_false_after_complete_cycle(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        overlay.activate(proxy)
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)
        assert not overlay._is_animating

        overlay.deactivate()
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)
        assert not overlay._is_animating

"""Tests for TheaterOverlay."""

import pytest
from PySide6.QtCore import QPoint, Qt
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

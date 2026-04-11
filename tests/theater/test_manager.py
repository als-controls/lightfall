"""Tests for TheaterManager — install, uninstall, activate delegation."""

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lucid.ui.theater.manager import TheaterManager, theater_manager
from lucid.ui.theater.proxy import TheaterProxy


class TestTheaterManagerRegister:
    """Registration and signal wiring."""

    def test_proxy_auto_registered(self, qtbot):
        target = QLabel("plot")
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert id(target) in theater_manager._proxies

    def test_unregister_removes_proxy(self, qtbot):
        target = QLabel("plot")
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        theater_manager.unregister(proxy)
        assert id(target) not in theater_manager._proxies


class TestTheaterManagerInstall:
    """install() layout surgery."""

    def test_install_wraps_widget_in_proxy(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)

        proxy = theater_manager.install(target)

        assert isinstance(proxy, TheaterProxy)
        assert proxy.target_widget is target
        # Proxy should be in the layout where target was
        assert parent_widget.layout().indexOf(proxy) >= 0

    def test_install_preserves_layout_index(self, parent_widget, qtbot):
        before = QLabel("before")
        target = QLabel("target")
        after = QLabel("after")
        layout = parent_widget.layout()
        layout.addWidget(before)
        layout.addWidget(target)
        layout.addWidget(after)

        proxy = theater_manager.install(target)

        assert layout.indexOf(before) == 0
        assert layout.indexOf(proxy) == 1
        assert layout.indexOf(after) == 2

    def test_install_raises_without_parent(self, qtbot):
        target = QLabel("orphan")
        qtbot.addWidget(target)

        with pytest.raises(ValueError, match="without a parent"):
            theater_manager.install(target)

    def test_install_raises_without_layout(self, qtbot):
        parent = QWidget()
        qtbot.addWidget(parent)
        target = QLabel("child", parent)

        with pytest.raises(ValueError, match="no layout"):
            theater_manager.install(target)


class TestTheaterManagerUninstall:
    """uninstall() layout restoration."""

    def test_uninstall_restores_widget(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        theater_manager.install(target)

        theater_manager.uninstall(target)

        # Widget should be back in the layout, proxy gone
        assert parent_widget.layout().indexOf(target) >= 0
        assert id(target) not in theater_manager._proxies

    def test_uninstall_preserves_layout_index(self, parent_widget, qtbot):
        before = QLabel("before")
        target = QLabel("target")
        after = QLabel("after")
        layout = parent_widget.layout()
        layout.addWidget(before)
        layout.addWidget(target)
        layout.addWidget(after)

        theater_manager.install(target)
        theater_manager.uninstall(target)

        assert layout.indexOf(before) == 0
        assert layout.indexOf(target) == 1
        assert layout.indexOf(after) == 2

    def test_uninstall_noop_for_unknown_widget(self, qtbot):
        target = QLabel("unknown")
        qtbot.addWidget(target)
        theater_manager.uninstall(target)  # should not raise


class TestTheaterManagerActivate:
    """activate() creates overlay lazily and delegates."""

    def test_activate_creates_overlay(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        theater_manager.activate(proxy)

        assert theater_manager._overlay is not None
        assert theater_manager._overlay.isVisible()

    def test_deactivate_delegates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        theater_manager.deactivate()
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        assert not overlay.isVisible()

    def test_is_active_property(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        assert not theater_manager.is_active

        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        assert theater_manager.is_active

        theater_manager.deactivate()
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        assert not theater_manager.is_active


class TestTheaterIntegration:
    """Full round-trip: install → expand → dismiss → verify."""

    def test_full_cycle_via_install(self, parent_widget, qtbot):
        """Simulates the intended usage: install, click expand, press Escape."""
        target = QLabel("My Plot")
        parent_widget.layout().addWidget(target)

        # Install theater mode
        proxy = theater_manager.install(target)
        proxy.show()
        parent_widget.show()

        # Verify proxy is in layout
        assert parent_widget.layout().indexOf(proxy) >= 0

        # Simulate expand button click
        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        assert overlay is not None
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        # Widget is on overlay
        assert target.parentWidget() is overlay
        assert theater_manager.is_active

        # Dismiss
        theater_manager.deactivate()
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        # Widget is back in proxy
        assert proxy.currentWidget() is target
        assert not theater_manager.is_active
        assert not overlay.isVisible()

    def test_full_cycle_via_direct_proxy(self, parent_widget, qtbot):
        """Direct TheaterProxy construction (no install)."""
        target = QLabel("My Image")
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        # Activate via manager (proxy auto-registered)
        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        assert target.parentWidget() is overlay

        # Deactivate
        theater_manager.deactivate()
        qtbot.waitUntil(lambda: not overlay._is_animating, timeout=2000)

        assert proxy.currentWidget() is target

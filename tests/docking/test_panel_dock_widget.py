"""Tests for PanelDockWidget theater/title-bar integration."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow

from lightfall.ui.docking.widget import PanelDockWidget
from lightfall.ui.theater.manager import theater_manager
from lightfall.ui.theater.proxy import TheaterProxy

from .conftest import make_panel_class


def _make_dock(qtbot, panel_id="test.dock.panel"):
    window = QMainWindow()
    window.resize(1200, 800)
    qtbot.addWidget(window)
    panel = make_panel_class(panel_id)()
    dock = PanelDockWidget(panel, use_custom_title_bar=True)
    qtbot.addWidget(dock)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    window.show()
    dock.setVisible(True)
    return window, panel, dock


class TestProxyInDock:
    def test_dock_widget_is_proxy_wrapping_panel(self, qtbot):
        window, panel, dock = _make_dock(qtbot)
        proxy = dock.widget()
        assert isinstance(proxy, TheaterProxy)
        assert proxy.target_widget is panel
        assert dock.panel is panel

    def test_proxy_hover_button_suppressed(self, qtbot):
        window, panel, dock = _make_dock(qtbot)
        assert dock.widget()._show_hover_button is False


class TestExpand:
    def test_expand_activates_theater(self, qtbot):
        window, panel, dock = _make_dock(qtbot)
        dock._title_bar.expand_requested.emit()
        assert theater_manager.is_active
        theater_manager.deactivate()

    def test_collapse_returns_panel(self, qtbot):
        window, panel, dock = _make_dock(qtbot)
        dock._title_bar.expand_requested.emit()
        # Wait for the activation animation to finish before deactivating
        qtbot.waitUntil(
            lambda: theater_manager._overlay is not None
            and not theater_manager._overlay._is_animating,
            timeout=3000,
        )
        theater_manager.deactivate()
        qtbot.waitUntil(
            lambda: dock.widget().currentWidget() is panel, timeout=3000
        )


class TestFloating:
    def test_top_level_change_updates_title_bar(self, qtbot):
        window, panel, dock = _make_dock(qtbot)
        dock.setFloating(True)
        assert not dock._title_bar._redock_btn.isHidden()
        assert dock._title_bar._expand_btn.isHidden()
        dock.setFloating(False)
        assert dock._title_bar._redock_btn.isHidden()

    def test_redock_request_docks(self, qtbot):
        window, panel, dock = _make_dock(qtbot)
        dock.setFloating(True)
        dock._title_bar.redock_requested.emit()
        assert not dock.isFloating()


class TestTitleBarActions:
    def test_panel_actions_rendered(self, qtbot):
        window, panel, dock = _make_dock(qtbot)
        assert dock._title_bar._actions_layout.count() == 0
        action = QAction("Go", panel)
        panel.add_title_bar_action(action)
        assert dock._title_bar._actions_layout.count() == 1


class TestTheaterTeardownInteractions:
    def test_minimize_while_expanded_collapses_first(self, qtbot):
        _, panel, dock = _make_dock(qtbot)
        dock._title_bar.expand_requested.emit()
        qtbot.waitUntil(lambda: theater_manager.is_active, timeout=3000)
        dock._title_bar.close_requested.emit()
        assert not theater_manager.is_active
        assert dock.widget().currentWidget() is panel
        assert not dock.isVisible()

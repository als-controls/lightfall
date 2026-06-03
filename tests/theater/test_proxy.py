"""Tests for TheaterProxy page switching and widget handoff."""

from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QLabel, QWidget

from lightfall.ui.theater.proxy import TheaterProxy


class TestTheaterProxyCore:
    """Core page switching: init, take_widget, return_widget."""

    def test_init_shows_target_as_current(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert proxy.count() == 2
        assert proxy.currentIndex() == 0
        assert proxy.currentWidget() is target

    def test_target_widget_property(self, qtbot):
        target = QLabel("plot")
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert proxy.target_widget is target

    def test_take_widget_switches_to_placeholder(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        taken = proxy.take_widget()

        assert taken is target
        assert proxy.currentIndex() == 0  # placeholder is now index 0
        assert proxy.currentWidget() is not target

    def test_return_widget_restores_target(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        taken = proxy.take_widget()
        proxy.return_widget(taken)

        assert proxy.currentIndex() == 0
        assert proxy.currentWidget() is target

    def test_take_return_roundtrip_preserves_count(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert proxy.count() == 2
        proxy.take_widget()
        assert proxy.count() == 1  # target removed from stack
        proxy.return_widget(target)
        assert proxy.count() == 2  # target re-inserted

    def test_auto_registers_with_manager(self, qtbot):
        from lightfall.ui.theater.manager import theater_manager

        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert id(target) in theater_manager._proxies
        assert theater_manager._proxies[id(target)] is proxy


class TestTheaterProxyHoverButton:
    """Hover expand button visibility and signal."""

    def test_button_hidden_by_default(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        assert not proxy._expand_btn.isVisible()

    def test_button_visible_on_enter(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        event = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
        proxy.enterEvent(event)

        assert proxy._expand_btn.isVisible()

    def test_button_hidden_on_leave(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        # Enter then leave
        enter = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
        proxy.enterEvent(enter)
        leave = QEvent(QEvent.Type.Leave)
        proxy.leaveEvent(leave)

        assert not proxy._expand_btn.isVisible()

    def test_button_hidden_when_widget_taken(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        proxy.take_widget()
        enter = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
        proxy.enterEvent(enter)

        assert not proxy._expand_btn.isVisible()

    def test_button_click_emits_expand_requested(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        with qtbot.waitSignal(proxy.expand_requested, timeout=1000):
            proxy._expand_btn.click()

    def test_button_positioned_top_right_on_resize(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.resize(400, 300)
        proxy.show()

        btn = proxy._expand_btn
        margin = 4
        assert btn.x() == 400 - btn.width() - margin
        assert btn.y() == margin

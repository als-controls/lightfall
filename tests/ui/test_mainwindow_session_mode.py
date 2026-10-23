"""Main window: the session-mode title and stripe; close_confirmed."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFrame

from lightfall.devices.backend_types.session import SessionMode, set_session_mode


@pytest.fixture()
def mode():
    m = SessionMode("r1", "excl", "Excl: 127.0.0.1", {})
    set_session_mode(m)
    yield m
    set_session_mode(None)


def _window(qtbot):
    from lightfall.ui import LFMainWindow

    window = LFMainWindow()
    qtbot.addWidget(window)
    return window


def test_title_and_stripe_in_session_mode(qtbot, mode):
    from lightfall.ui.theme import ThemeManager

    window = _window(qtbot)
    assert window.windowTitle() == "Lightfall — Excl: 127.0.0.1"
    stripe = window.findChild(QFrame, "sessionModeStripe")
    assert stripe is not None and stripe.height() == 2
    assert ThemeManager.get_instance().colors.warning in stripe.styleSheet()


def test_no_stripe_without_mode(qtbot):
    set_session_mode(None)
    window = _window(qtbot)
    assert window.windowTitle() == "Lightfall"
    assert window.findChild(QFrame, "sessionModeStripe") is None


class _Recorder:
    def __init__(self, order):
        self.order = order

    def cleanup(self):
        self.order.append("cleanup")


def test_close_confirmed_emitted_before_statusbar_cleanup(qtbot, monkeypatch):
    window = _window(qtbot)
    order: list[str] = []
    window._statusbar_manager = _Recorder(order)
    window.close_confirmed.connect(lambda: order.append("confirmed"))
    monkeypatch.setattr(window, "list_open_panels", lambda: [])
    window.closeEvent(QCloseEvent())
    assert order == ["confirmed", "cleanup"]


def test_close_confirmed_not_emitted_when_panel_vetoes(qtbot, monkeypatch):
    window = _window(qtbot)
    fired: list[int] = []
    window.close_confirmed.connect(lambda: fired.append(1))

    class _Busy:
        def can_close(self, force=False):
            return False

    monkeypatch.setattr(window, "list_open_panels", lambda: ["busy"])
    monkeypatch.setattr(window, "get_panel", lambda panel_id: _Busy())
    event = QCloseEvent()
    window.closeEvent(event)
    assert fired == [] and not event.isAccepted()

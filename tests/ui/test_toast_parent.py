"""ToastManager never parents a toast to another toast (eval harness crash, #13).

With no main window, QApplication.activeWindow() is the toast shown last. Parenting the
next toast to it made Qt delete the child when the parent expired, while pyqttoast still
listed it; the next show() touched the dead object and crashed the process.
"""
from __future__ import annotations

from pyqttoast import Toast
from PySide6.QtWidgets import QApplication, QWidget

from lightfall.ui.toast import ToastManager


def test_an_active_toast_is_not_used_as_parent(qtbot, monkeypatch):
    other = Toast(None)
    qtbot.addWidget(other)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: other))
    assert ToastManager()._get_parent() is None


def test_an_active_ordinary_window_is_still_the_parent(qtbot, monkeypatch):
    window = QWidget()
    qtbot.addWidget(window)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: window))
    assert ToastManager()._get_parent() is window


def test_an_explicit_parent_wins(qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    manager = ToastManager()
    manager.set_parent_widget(window)
    assert manager._get_parent() is window

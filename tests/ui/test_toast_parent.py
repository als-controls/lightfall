"""ToastManager never parents a toast to another toast (eval harness crash, #13).

With no main window, QApplication.activeWindow() is the toast shown last. Parenting the
next toast to it made Qt delete the child when the parent expired, while pyqttoast still
listed it; the next show() touched the dead object and crashed the process.
"""
from __future__ import annotations

import pytest
import shiboken6
from pyqttoast import Toast
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QWidget

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


@pytest.fixture
def clean_toasts():
    yield
    try:
        Toast.reset()
    except RuntimeError:  # a dead toast left in pyqttoast's list (the bug itself)
        Toast._Toast__currently_shown.clear()
        Toast._Toast__queue.clear()


def _closing_dialog(parent: QWidget | None) -> QDialog:
    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    return dialog


def test_an_active_dialog_yields_its_main_window(qtbot, monkeypatch):
    window = QWidget()
    qtbot.addWidget(window)
    dialog = _closing_dialog(window)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: dialog))
    assert ToastManager()._get_parent() is window


def test_an_unparented_closing_dialog_is_not_used_as_parent(qtbot, monkeypatch):
    dialog = _closing_dialog(None)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: dialog))
    assert ToastManager()._get_parent() is None


def test_toast_outlives_the_dialog_that_was_active(qtbot, monkeypatch, clean_toasts):
    """GlitchTip LIGHTFALL-H: a toast shown from a WA_DeleteOnClose dialog (Publish)
    died with the dialog while pyqttoast still listed it; the next toast to hide then
    touched it and raised "Internal C++ object (Toast) already deleted"."""
    window = QWidget()
    qtbot.addWidget(window)
    dialog = _closing_dialog(window)
    dialog.show()
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: dialog))

    toast = ToastManager().success("Published", "item to facility")
    dialog.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert shiboken6.isValid(toast)

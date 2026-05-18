"""RE-gate logout dialog: confirm when engine is active, proceed when idle."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lucid.acquire.engine.state import EngineState


@pytest.fixture
def mainwindow_class():
    """Import MainWindow lazily so qapp fixture has fired first."""
    from lucid.ui.mainwindow import NCSMainWindow
    return NCSMainWindow


def _make_window(qapp, mainwindow_class):
    """Build a minimal MainWindow without doing full app init.

    We exercise just the `_re_active_for_logout_gate` and `_on_logout`
    helpers; bypass __init__ via MagicMock-ed dependencies.
    """
    # MainWindow's __init__ is heavy; use MagicMock to stand in
    # without actually constructing the widget tree. We need a real
    # QWidget-derived instance so QMessageBox.question accepts it as parent.
    win = mainwindow_class.__new__(mainwindow_class)
    # Initialize the Qt base (QMainWindow is the direct base of NCSMainWindow)
    from PySide6.QtWidgets import QMainWindow
    QMainWindow.__init__(win)
    win._session_manager = MagicMock()
    return win


def test_gate_returns_false_when_engine_idle(qapp, mainwindow_class, monkeypatch):
    win = _make_window(qapp, mainwindow_class)

    fake_engine = MagicMock()
    fake_engine.state = EngineState.IDLE

    monkeypatch.setattr(
        "lucid.acquire.engine.get_engine", lambda: fake_engine
    )

    assert win._re_active_for_logout_gate() is False


def test_gate_returns_true_when_engine_running(qapp, mainwindow_class, monkeypatch):
    win = _make_window(qapp, mainwindow_class)

    fake_engine = MagicMock()
    fake_engine.state = EngineState.RUNNING

    monkeypatch.setattr(
        "lucid.acquire.engine.get_engine", lambda: fake_engine
    )

    assert win._re_active_for_logout_gate() is True


def test_gate_returns_true_when_engine_paused(qapp, mainwindow_class, monkeypatch):
    win = _make_window(qapp, mainwindow_class)

    fake_engine = MagicMock()
    fake_engine.state = EngineState.PAUSED

    monkeypatch.setattr(
        "lucid.acquire.engine.get_engine", lambda: fake_engine
    )

    assert win._re_active_for_logout_gate() is True


def test_gate_returns_false_on_error(qapp, mainwindow_class, monkeypatch):
    win = _make_window(qapp, mainwindow_class)

    def boom():
        raise RuntimeError("engine plugin not loaded")
    monkeypatch.setattr("lucid.acquire.engine.get_engine", boom)

    assert win._re_active_for_logout_gate() is False


def test_logout_skips_thread_when_user_cancels(qapp, mainwindow_class, monkeypatch):
    """When the gate fires and the user clicks No, logout does NOT start."""
    win = _make_window(qapp, mainwindow_class)

    monkeypatch.setattr(win, "_re_active_for_logout_gate", lambda: True)

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )

    thread_started = []
    class _FakeFuture:
        def __init__(self, *a, **kw): pass
        def start(self): thread_started.append(True)
    monkeypatch.setattr("lucid.utils.threads.QThreadFuture", _FakeFuture)

    win._on_logout()

    assert thread_started == []
    win._session_manager.logout.assert_not_called()


def test_logout_proceeds_when_user_confirms(qapp, mainwindow_class, monkeypatch):
    """When the gate fires and the user clicks Yes, logout starts."""
    win = _make_window(qapp, mainwindow_class)

    monkeypatch.setattr(win, "_re_active_for_logout_gate", lambda: True)

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )

    thread_started = []
    class _FakeFuture:
        def __init__(self, fn, *a, **kw):
            self._fn = fn
        def start(self): thread_started.append(True)
    monkeypatch.setattr("lucid.utils.threads.QThreadFuture", _FakeFuture)

    win._on_logout()

    assert thread_started == [True]


def test_logout_skips_dialog_when_engine_idle(qapp, mainwindow_class, monkeypatch):
    """Idle engine: logout runs without showing the dialog."""
    win = _make_window(qapp, mainwindow_class)

    monkeypatch.setattr(win, "_re_active_for_logout_gate", lambda: False)

    dialog_called = []
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        lambda *a, **kw: (dialog_called.append(True), QMessageBox.StandardButton.Yes)[1],
    )

    thread_started = []
    class _FakeFuture:
        def __init__(self, *a, **kw): pass
        def start(self): thread_started.append(True)
    monkeypatch.setattr("lucid.utils.threads.QThreadFuture", _FakeFuture)

    win._on_logout()

    assert dialog_called == []
    assert thread_started == [True]

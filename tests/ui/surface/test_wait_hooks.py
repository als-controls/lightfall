"""ui_wait conditions, plus the app hooks the surface relies on (toast history, sidebar names)."""

import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow, QPushButton, QVBoxLayout, QWidget

from lightfall.ui.surface import wait as wait_mod
from lightfall.ui.surface.snapshot import snapshot
from lightfall.ui.surface.wait import WaitError, wait
from tests.ui.surface.test_act import in_worker


@pytest.fixture
def window(qtbot):
    w = QMainWindow()
    w.setWindowTitle("Lightfall")
    body = QWidget()
    w.box = QVBoxLayout(body)
    w.setCentralWidget(body)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_element_appears(window, qtbot):
    late = QPushButton("Done")
    late.hide()
    window.box.addWidget(late)
    QTimer.singleShot(300, late.show)
    r = in_worker(qtbot, lambda: wait("element", 5, role="button", name="Done"))
    assert r["met"] and r["waited_s"] >= 0.2


def test_element_gone(window, qtbot):
    b = QPushButton("Busy")
    window.box.addWidget(b)
    QTimer.singleShot(200, b.hide)
    assert in_worker(qtbot, lambda: wait("element", 5, name="Busy", gone=True))["met"]


def test_timeout_is_an_answer_not_an_error(window, qtbot):
    r = in_worker(qtbot, lambda: wait("element", 0.3, name="Never"))
    assert r == {"met": False, "waited_s": r["waited_s"], "detail": {"still_present": False}}


def test_toast_seen_even_after_it_closed(window, qtbot):
    from lightfall.ui.toast import ToastManager

    mgr = ToastManager.get_instance()
    mgr.set_parent_widget(window)

    def later():
        t = mgr.error("Device not connected", "sample_lift")
        t.hide()

    QTimer.singleShot(200, later)
    r = in_worker(qtbot, lambda: wait("toast", 5, text="not connected", kind="error"))
    assert r["met"] and 'error "Device not connected"' in r["detail"]["toast"]


def test_idle_without_engine(window, qtbot, monkeypatch):
    monkeypatch.setattr(wait_mod, "_engine", lambda: None)
    assert in_worker(qtbot, lambda: wait("idle", 5))["met"]


class _Engine:
    def __init__(self):
        self.busy_until = time.monotonic() + 0.4
        self.queue_size = 0

    @property
    def is_idle(self):
        return time.monotonic() >= self.busy_until


def test_run_finished_with_stub_engine(window, qtbot, monkeypatch):
    eng = _Engine()
    monkeypatch.setattr(wait_mod, "_engine", lambda: eng)
    r = in_worker(qtbot, lambda: wait("run_finished", 5))
    assert r["met"] and r["waited_s"] >= 0.3


def test_run_finished_never_started(window, qtbot, monkeypatch):
    monkeypatch.setattr(wait_mod, "_engine", lambda: None)
    r = in_worker(qtbot, lambda: wait("run_finished", 0.3))
    assert not r["met"] and r["detail"]["engine"] == "never started"


def test_unknown_condition(window, qtbot):
    with pytest.raises(WaitError):
        in_worker(qtbot, lambda: wait("sunrise"))


def test_toast_manager_recent(window):
    from lightfall.ui.toast import ToastManager

    t0 = time.monotonic()
    mgr = ToastManager.get_instance()
    mgr.set_parent_widget(window)
    mgr.warning("Low disk", "3 GB left").hide()
    recent = mgr.recent(t0)
    assert recent[-1]["kind"] == "warning" and recent[-1]["title"] == "Low disk"


def test_icon_strip_buttons_are_named(qtbot):
    from lightfall.ui.docking.icon_sidebar import IconStripButton

    w = QWidget()
    w.setWindowTitle("Lightfall")
    qtbot.addWidget(w)
    b = IconStripButton("devices", QIcon(), "Devices", w)
    QVBoxLayout(w).addWidget(b)
    w.show()
    qtbot.waitExposed(w)
    assert 'toggle "Devices"' in snapshot()

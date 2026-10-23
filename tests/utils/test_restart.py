"""request_restart / relaunch: Lightfall restarts itself after a normal quit."""

from __future__ import annotations

import sys

import pytest

from lightfall.utils import restart


@pytest.fixture(autouse=True)
def _clear_flag():
    restart._RESTART_REQUESTED = False
    yield
    restart._RESTART_REQUESTED = False


def test_relaunch_uses_orig_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "orig_argv", ["py", "-m", "lightfall"])
    assert restart.relaunch(popen=lambda args, **kw: calls.append(args)) is True
    assert calls == [["py", "-m", "lightfall"]]


def test_relaunch_failure_is_logged():
    def _fail(args, **kw):
        raise OSError("no such file")

    assert restart.relaunch(["nope"], popen=_fail) is False


def test_request_restart_sets_flag_and_quits(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    quits = []
    monkeypatch.setattr(QCoreApplication, "quit", staticmethod(lambda: quits.append(1)))
    restart.request_restart()
    qtbot.waitUntil(lambda: quits == [1], timeout=1000)
    assert restart.restart_requested() is True

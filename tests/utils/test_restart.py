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


def test_relaunch_uses_launch_env_not_current_env(monkeypatch):
    # C1: the env a session mode (or CA tunnel) wrote must not reach the child.
    monkeypatch.setattr(restart, "_LAUNCH_ENV", None)
    monkeypatch.delenv("EPICS_CA_ADDR_LIST", raising=False)
    restart.remember_launch_env()
    monkeypatch.setenv("EPICS_CA_ADDR_LIST", "127.0.0.1")
    seen = []
    restart.relaunch(["py"], popen=lambda args, **kw: seen.append(kw.get("env")))
    assert seen and seen[0] is not None and "EPICS_CA_ADDR_LIST" not in seen[0]


def test_relaunch_if_requested_runs_once(monkeypatch):
    # I1: the watchdog and main() may both try; only one child starts.
    calls = []
    monkeypatch.setattr(restart, "relaunch", lambda *a, **k: calls.append(1) or True)
    monkeypatch.setattr(restart, "_RELAUNCHED", False)
    assert restart.relaunch_if_requested() is False  # nothing requested
    restart._RESTART_REQUESTED = True
    assert restart.relaunch_if_requested() is True
    assert restart.relaunch_if_requested() is False
    assert calls == [1]


def test_cancel_restart_clears_flag():
    restart._RESTART_REQUESTED = True
    restart.cancel_restart()
    assert restart.restart_requested() is False

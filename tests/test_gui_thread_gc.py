"""install_gui_thread_gc: cyclic GC runs only on the GUI thread (pyqtgraph's collector)."""
from __future__ import annotations

import gc
import threading

import pytest

from lightfall.utils import crash_diagnostics as cd


@pytest.fixture
def gui_gc(qapp, monkeypatch):
    monkeypatch.delenv(cd.GC_OPT_OUT_ENV, raising=False)
    was_enabled = gc.isenabled()
    collector = cd.install_gui_thread_gc(interval=0.05)
    yield collector
    cd.uninstall_gui_thread_gc()
    if not was_enabled:  # the suite's own conftest runs with automatic GC off
        gc.disable()


def test_disables_automatic_gc_and_runs_a_gui_thread_timer(gui_gc):
    assert gui_gc is not None
    assert not gc.isenabled()
    assert gui_gc.timer.isActive()
    assert gui_gc.timer.interval() == 50
    assert cd.install_gui_thread_gc() is gui_gc  # idempotent


def test_collections_happen_on_the_gui_thread(gui_gc, qtbot):
    # gc.callbacks fires for every collection, automatic ones included, so a
    # worker-thread collection would show up here as False.
    threads = []

    def on_gc(phase, _info):
        if phase == "start":
            threads.append(threading.current_thread() is threading.main_thread())

    gc.callbacks.append(on_gc)
    try:
        _churn_and_wait(qtbot, threads)
    finally:
        gc.callbacks.remove(on_gc)
    assert threads and all(threads)


def _churn_and_wait(qtbot, threads):

    class Cycle:
        def __init__(self):
            self.me = self

    def churn():
        for _ in range(5000):
            Cycle()

    worker = threading.Thread(target=churn)
    worker.start()
    worker.join()
    churn()  # push generation 0 over its threshold
    qtbot.waitUntil(lambda: bool(threads), timeout=2000)


def test_slow_collection_is_logged(gui_gc, monkeypatch):
    messages = []
    from lightfall.utils.logging import logger

    sink = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
    try:
        monkeypatch.setattr(cd, "SLOW_GC_MS", -1.0)
        gui_gc.check()
    finally:
        logger.remove(sink)
    assert any("garbage collection took" in m for m in messages)


def test_opt_out_leaves_automatic_gc_alone(qapp, monkeypatch):
    monkeypatch.setenv(cd.GC_OPT_OUT_ENV, "1")
    was_enabled = gc.isenabled()
    assert cd.install_gui_thread_gc() is None
    assert gc.isenabled() == was_enabled

"""The threads monitor must not sample the thread list on the GUI thread, and
must not poll at all while the Threads panel is hidden.

``psutil.Process.threads()`` on Windows took 60-130 ms with the GIL held and
ran every 100 ms from a GUI-thread QTimer, even with the panel collapsed in
its auto-hide sidebar.
"""

from __future__ import annotations

import sys
import threading

import pytest

from lightfall.ui.models import thread_model
from lightfall.ui.models.thread_model import ThreadManagerObserver


def test_snapshot_is_gathered_off_the_gui_thread(qtbot):
    observer = ThreadManagerObserver(poll_interval_ms=50)
    seen: list[threading.Thread] = []
    orig = observer._cpu_tracker.update

    def recording_update():
        seen.append(threading.current_thread())
        orig()

    observer._cpu_tracker.update = recording_update
    try:
        with qtbot.waitSignal(observer.cpu_updated, timeout=3000):
            observer.start()
        assert seen
        assert all(t is not threading.main_thread() for t in seen)
    finally:
        observer.shutdown()


def test_poll_does_not_queue_a_second_sample_while_one_is_in_flight(qtbot):
    observer = ThreadManagerObserver(poll_interval_ms=10_000)
    gate = threading.Event()
    calls = []

    def slow_update():
        calls.append(1)
        gate.wait(2)

    observer._cpu_tracker.update = slow_update
    try:
        observer._poll()
        observer._poll()
        observer._poll()
        gate.set()
        qtbot.waitUntil(lambda: not observer._sample_in_flight, timeout=3000)
        assert len(calls) == 1
    finally:
        observer.shutdown()


def test_no_psutil_thread_walk_on_windows(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows-only fast path")

    def boom(*a, **kw):
        raise AssertionError("psutil.Process.threads() must not be called on Windows")

    tracker = thread_model.ThreadCpuTracker()
    monkeypatch.setattr(tracker._process, "threads", boom)
    tracker.update()
    tracker.update()
    assert tracker.get_cpu_percent(threading.main_thread().name) is not None


# -- ThreadsPanel visibility gating --------------------------------------------


@pytest.fixture
def panel(qtbot):
    from lightfall.ui.panels.threads_panel import ThreadsPanel

    p = ThreadsPanel()
    qtbot.addWidget(p)
    yield p
    p._observer.shutdown()


def test_hidden_panel_never_polls(panel, qtbot):
    qtbot.wait(50)
    assert not panel._observer.is_polling
    assert panel._observer._pool is None  # no sampler thread was ever started
    assert not panel._model._refresh_timer.isActive()


def test_polls_only_while_visible(panel, qtbot):
    panel.show()
    qtbot.waitUntil(lambda: panel._observer.is_polling, timeout=1000)
    assert panel._model._refresh_timer.isActive()
    assert panel._details_timer.isActive()

    panel.hide()
    assert not panel._observer.is_polling
    assert not panel._model._refresh_timer.isActive()
    assert not panel._details_timer.isActive()

    panel.show()
    assert panel._observer.is_polling


def test_hidden_with_its_container(panel, qtbot):
    """Auto-hide sidebars hide the dock container, not the panel itself."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    container = QWidget()
    QVBoxLayout(container).addWidget(panel)
    try:
        container.show()
        qtbot.waitUntil(lambda: panel._observer.is_polling, timeout=1000)
        container.hide()
        assert not panel._observer.is_polling
    finally:
        panel.setParent(None)  # the panel fixture owns its teardown
        container.deleteLater()


def test_auto_refresh_off_stops_polling_while_visible(panel, qtbot):
    panel.show()
    qtbot.waitUntil(lambda: panel._observer.is_polling, timeout=1000)
    panel._auto_refresh_check.setChecked(False)
    assert not panel._observer.is_polling
    panel._auto_refresh_check.setChecked(True)
    assert panel._observer.is_polling


# -- no QThreadFuture crosses to the sampler thread ----------------------------


def _contains_qobject(value) -> bool:
    from PySide6.QtCore import QObject

    if isinstance(value, QObject):
        return True
    if isinstance(value, dict):
        return any(_contains_qobject(k) or _contains_qobject(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_qobject(v) for v in value)
    if hasattr(value, "__dataclass_fields__"):
        return any(_contains_qobject(getattr(value, f)) for f in value.__dataclass_fields__)
    return False


def test_sampler_receives_and_returns_plain_data_only(qtbot):
    from lightfall.utils.threads import QThreadFuture

    gate = threading.Event()
    f = QThreadFuture(gate.wait, 5, name="plain-data-probe")
    f.start()
    observer = ThreadManagerObserver(poll_interval_ms=10_000)
    submitted = []
    try:
        observer._poll()  # creates the pool
        qtbot.waitUntil(lambda: not observer._sample_in_flight, timeout=3000)
        orig_submit = observer._pool.submit

        def spy(fn, *args, **kw):
            submitted.append((args, kw))
            return orig_submit(fn, *args, **kw)

        observer._pool.submit = spy
        with qtbot.waitSignal(observer._snapshot_ready, timeout=3000) as blocker:
            observer._poll()
        assert submitted
        assert not _contains_qobject(submitted)
        assert not _contains_qobject(blocker.args)
        assert any(r.name == "plain-data-probe" for r in observer.get_records())
    finally:
        gate.set()
        f.wait(3000)
        observer.shutdown()


def test_in_flight_sample_does_not_keep_a_finished_thread_alive(qtbot):
    import gc
    import weakref

    from lightfall.utils.threads import QThreadFuture, thread_manager

    observer = ThreadManagerObserver(poll_interval_ms=10_000)
    gate = threading.Event()
    entered = threading.Event()

    def blocking_lookup(name):  # runs on the sampler, after thread states exist
        entered.set()
        gate.wait(5)

    observer._cpu_tracker.get_cpu_percent = blocking_lookup
    f = QThreadFuture(lambda: None, name="fire-and-forget")
    f.start()
    try:
        observer._poll()  # sample now blocked on the worker, holding its args
        assert observer._sample_in_flight
        assert entered.wait(3)
        f.wait(3000)
        fid = id(f)
        qtbot.waitUntil(lambda: fid not in thread_manager._threads, timeout=3000)
        ref = weakref.ref(f)
        del f
        gc.collect()
        assert ref() is None  # neither the sampler nor the pending states held it
    finally:
        gate.set()
        qtbot.waitUntil(lambda: not observer._sample_in_flight, timeout=3000)
        observer.shutdown()

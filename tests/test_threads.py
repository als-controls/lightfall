"""Tests for the threading module."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from lightfall.utils.threads import (
    QThreadFuture,
    QThreadFutureIterator,
    get_thread_manager,
    invoke_in_main_thread,
    is_main_thread,
    iterator,
    method,
    thread_manager,
)


@pytest.fixture
def qapp():
    """Ensure QApplication exists for Qt threading."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


class TestThreadManager:
    """Tests for ThreadManager."""

    def test_singleton(self) -> None:
        """ThreadManager should be a singleton."""
        manager1 = get_thread_manager()
        manager2 = get_thread_manager()
        assert manager1 is manager2
        assert manager1 is thread_manager

    def test_register_unregister(self, qapp) -> None:
        """Test thread registration and unregistration."""
        def dummy():
            time.sleep(0.1)

        future = QThreadFuture(dummy, register=False)
        thread_manager.register(future, key="test_thread")

        assert thread_manager.get_by_key("test_thread") is future

        thread_manager.unregister(future)
        assert thread_manager.get_by_key("test_thread") is None

    def test_cancel_by_key(self, qapp) -> None:
        """Test cancelling a thread by key."""
        cancelled = []

        def slow_task():
            for _ in range(100):
                if QThreadFuture.currentThread().isInterruptionRequested():
                    cancelled.append(True)
                    return
                time.sleep(0.01)

        future = QThreadFuture(slow_task, key="cancellable")
        future.start()
        time.sleep(0.05)

        result = thread_manager.cancel("cancellable", timeout_ms=1000)
        assert result is True
        assert len(cancelled) == 1


class TestQThreadFuture:
    """Tests for QThreadFuture."""

    def test_basic_execution(self, qapp) -> None:
        """Test basic thread execution."""
        def compute(x):
            return x * 2

        future = QThreadFuture(compute, 5)
        result = future.result(timeout_ms=1000)
        assert result == 10

    def test_callback_slot(self, qapp) -> None:
        """Test callback slot is called with result."""
        results = []

        def callback(value):
            results.append(value)

        def compute():
            return 42

        future = QThreadFuture(compute, callback_slot=callback)
        future.start()
        future.wait(1000)

        # Process events to ensure callback is invoked
        qapp.processEvents()
        time.sleep(0.1)
        qapp.processEvents()

        assert 42 in results

    def test_exception_handling(self, qapp) -> None:
        """Test exception is captured."""
        def failing_task():
            raise ValueError("Test error")

        future = QThreadFuture(failing_task)
        future.start()
        future.wait(1000)

        assert future.exception is not None
        assert isinstance(future.exception, ValueError)

    def test_cancellation(self, qapp) -> None:
        """Test thread cancellation."""
        def slow_task():
            for _ in range(100):
                time.sleep(0.01)
            return "completed"

        future = QThreadFuture(slow_task)
        future.start()
        time.sleep(0.05)

        success = future.cancel(timeout_ms=1000)
        assert success is True
        assert future.cancelled is True

    def test_context_manager(self, qapp) -> None:
        """Test context manager usage."""
        def quick_task():
            return "done"

        with QThreadFuture(quick_task) as future:
            pass  # Thread starts and we wait on exit

        assert future.done is True

    def test_auto_registration(self, qapp) -> None:
        """Test auto-registration with ThreadManager."""
        def task():
            time.sleep(0.1)

        future = QThreadFuture(task, key="auto_reg_test")
        future.start()

        assert thread_manager.get_by_key("auto_reg_test") is future

        future.wait(1000)
        # Pump Qt event loop so the finished signal delivers the unregister callback
        qapp.processEvents()
        time.sleep(0.05)
        qapp.processEvents()
        assert thread_manager.get_by_key("auto_reg_test") is None


class TestQThreadFutureIterator:
    """Tests for QThreadFutureIterator."""

    def test_yield_slot(self, qapp) -> None:
        """Test yield slot receives yielded values."""
        yielded = []

        def yield_handler(value):
            yielded.append(value)

        def generator():
            for i in range(3):  # noqa: UP028
                yield i
            return "done"

        future = QThreadFutureIterator(generator, yield_slot=yield_handler)
        future.start()
        future.wait(1000)

        # Process events
        qapp.processEvents()
        time.sleep(0.1)
        qapp.processEvents()

        assert yielded == [0, 1, 2]


class TestDecorators:
    """Tests for @method and @iterator decorators."""

    def test_method_decorator(self, qapp) -> None:
        """Test @method decorator."""
        @method()
        def compute(x):
            return x * 2

        future = compute(5)
        assert isinstance(future, QThreadFuture)

        result = future.result(timeout_ms=1000)
        assert result == 10

    def test_method_decorator_override_callback(self, qapp) -> None:
        """Test callback override at call time."""
        results = []

        def default_callback(v):
            results.append(("default", v))

        def override_callback(v):
            results.append(("override", v))

        @method(callback_slot=default_callback)
        def compute(x):
            return x

        # Use override
        future = compute(1, _callback_slot=override_callback)
        future.wait(1000)
        qapp.processEvents()
        time.sleep(0.1)
        qapp.processEvents()

        assert ("override", 1) in results
        assert ("default", 1) not in results

    def test_iterator_decorator(self, qapp) -> None:
        """Test @iterator decorator."""
        yielded = []

        @iterator(yield_slot=lambda v: yielded.append(v))
        def gen():
            for i in range(3):  # noqa: UP028
                yield i

        future = gen()
        assert isinstance(future, QThreadFutureIterator)

        future.wait(1000)
        qapp.processEvents()
        time.sleep(0.1)
        qapp.processEvents()

        assert yielded == [0, 1, 2]


class TestProgress:
    """Tests for progress signals."""

    def test_report_progress_emits_signal(self, qapp) -> None:
        """QThreadFuture.report_progress should emit sigProgress."""
        progress_updates = []

        def progress_handler(thread, current, minimum, maximum):
            progress_updates.append((thread, current, minimum, maximum))

        def task_with_progress():
            thread = QThreadFuture.currentThread()
            thread.report_progress(0, 0, 10)
            thread.report_progress(5, 0, 10)
            thread.report_progress(10, 0, 10)
            return "done"

        future = QThreadFuture(task_with_progress, progress_slot=progress_handler)
        future.start()
        future.wait(2000)
        qapp.processEvents()
        time.sleep(0.1)
        qapp.processEvents()

        assert len(progress_updates) == 3
        assert progress_updates[0] == (future, 0, 0, 10)
        assert progress_updates[1] == (future, 5, 0, 10)
        assert progress_updates[2] == (future, 10, 0, 10)

    def test_thread_manager_relays_progress(self, qapp) -> None:
        """ThreadManager.sigProgress should relay progress from registered threads."""
        manager_updates = []

        def manager_handler(thread, current, minimum, maximum):
            manager_updates.append((thread, current, minimum, maximum))

        thread_manager.sigProgress.connect(manager_handler)

        def task_with_progress():
            thread = QThreadFuture.currentThread()
            thread.report_progress(50, 0, 100)
            return "done"

        try:
            future = QThreadFuture(task_with_progress)
            future.start()
            future.wait(2000)
            qapp.processEvents()
            time.sleep(0.1)
            qapp.processEvents()

            assert len(manager_updates) == 1
            assert manager_updates[0] == (future, 50, 0, 100)
        finally:
            thread_manager.sigProgress.disconnect(manager_handler)

    def test_progress_default_min_max(self, qapp) -> None:
        """report_progress should default to min=0, max=100."""
        progress_updates = []

        def progress_handler(thread, current, minimum, maximum):
            progress_updates.append((current, minimum, maximum))

        def task():
            QThreadFuture.currentThread().report_progress(42)
            return "done"

        future = QThreadFuture(task, progress_slot=progress_handler)
        future.start()
        future.wait(2000)
        qapp.processEvents()
        time.sleep(0.1)
        qapp.processEvents()

        assert progress_updates == [(42, 0, 100)]

    def test_thread_manager_sigFinished(self, qapp) -> None:
        """ThreadManager.sigFinished should emit when a registered thread finishes."""
        finished_threads = []

        def finished_handler(thread):
            finished_threads.append(thread)

        thread_manager.sigFinished.connect(finished_handler)

        def task():
            return "done"

        try:
            future = QThreadFuture(task)
            future.start()
            future.wait(2000)
            qapp.processEvents()
            time.sleep(0.1)
            qapp.processEvents()

            assert future in finished_threads
        finally:
            thread_manager.sigFinished.disconnect(finished_handler)


class TestQThreadFutureSafeRepr:
    """Tests for safe repr in error logging."""

    def test_error_logging_survives_repr_failure(self, qapp, qtbot) -> None:
        """QThreadFuture should not crash when args have broken repr."""

        class BadRepr:
            def __repr__(self):
                raise RuntimeError("repr exploded")

        def failing_task(arg):
            raise ValueError("task failed")

        errors = []
        future = QThreadFuture(
            failing_task,
            BadRepr(),
            except_slot=lambda ex: errors.append(ex),
            name="test_bad_repr",
        )
        future.start()
        qtbot.waitUntil(lambda: len(errors) == 1, timeout=3000)

        # The original ValueError should be captured, not a RuntimeError from repr
        assert isinstance(errors[0], ValueError)
        assert "task failed" in str(errors[0])


class TestUtilities:
    """Tests for utility functions."""

    def test_is_main_thread(self) -> None:
        """Test is_main_thread detection."""
        assert is_main_thread() is True

    def test_invoke_in_main_thread_immediate(self, qapp) -> None:
        """Test invoke_in_main_thread calls immediately when in main thread."""
        results = []

        def callback():
            results.append("called")

        invoke_in_main_thread(callback)
        assert results == ["called"]

    def test_invoke_in_main_thread_force_event(self, qapp) -> None:
        """Test force_event=True posts as event."""
        results = []

        def callback():
            results.append("called")

        invoke_in_main_thread(callback, force_event=True)
        # Not called yet - need to process events
        assert results == []

        qapp.processEvents()
        assert results == ["called"]

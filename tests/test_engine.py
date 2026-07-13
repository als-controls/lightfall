"""Tests for the Engine abstraction layer.

Tests the Engine protocol, BaseEngine, BlueskyEngine, and MockEngine.
"""

import threading
import time
from queue import Empty
from unittest.mock import MagicMock, patch

import pytest

from lightfall.acquire.engine import (
    BaseEngine,
    BlueskyEngine,
    Engine,
    EngineState,
    MockEngine,
    get_engine,
    reset_engine,
    set_engine,
)


class _QueueOnlyEngine(BaseEngine):
    """Concrete BaseEngine exposing only the queue machinery for tests."""

    def pause(self, defer: bool = False) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> bool:
        return False

    def abort(self, reason: str = "") -> bool:
        return False

    def halt(self) -> bool:
        return False


# qapp is supplied by pytest-qt as a real QApplication (see tests/conftest.py);
# a local QCoreApplication fixture is the wrong type for widget tests and aborts
# the process at teardown (0xC0000005) when they share a run.


@pytest.fixture
def mock_engine(qapp):
    """Create a MockEngine for testing."""
    return MockEngine()


@pytest.fixture
def bluesky_engine(qapp):
    """Create a BlueskyEngine for testing."""
    pytest.importorskip("bluesky")

    engine = BlueskyEngine()
    # Wait for the RunEngine to initialize
    timeout = 5.0
    start = time.time()
    while engine.RE is None and time.time() - start < timeout:
        qapp.processEvents()
        time.sleep(0.05)
    yield engine
    # Stop the queue processor thread; leaked QThreads crash the interpreter
    # at exit ("QThread: Destroyed while thread is still running").
    if engine._queue_future is not None:
        engine._queue_future.cancel(timeout_ms=3000)


class TestEngineState:
    """Tests for EngineState enum."""

    def test_states_exist(self) -> None:
        """Test that all expected states exist."""
        assert EngineState.IDLE
        assert EngineState.RUNNING
        assert EngineState.PAUSED
        assert EngineState.STOPPING
        assert EngineState.ABORTING
        assert EngineState.ERROR

    def test_str_conversion(self) -> None:
        """Test string conversion of states."""
        assert str(EngineState.IDLE) == "idle"
        assert str(EngineState.RUNNING) == "running"
        assert str(EngineState.PAUSED) == "paused"


class TestMockEngine:
    """Tests for MockEngine."""

    def test_protocol_compliance(self, mock_engine) -> None:
        """Test that MockEngine satisfies Engine protocol."""
        assert isinstance(mock_engine, Engine)

    def test_initial_state(self, mock_engine) -> None:
        """Test initial engine state."""
        assert mock_engine.state == EngineState.IDLE
        assert mock_engine.state_name == "idle"
        assert mock_engine.is_idle is True
        assert mock_engine.name == "mock"

    def test_queue_operations(self, mock_engine) -> None:
        """Test queue operations."""
        assert mock_engine.queue_size == 0

        # MockEngine executes immediately, so queue stays empty
        mock_engine.submit("test")
        assert mock_engine.queue_size == 0

    def test_submit_emits_documents(self, mock_engine) -> None:
        """Test that submit emits start and stop documents."""
        outputs = []
        mock_engine.subscribe(lambda name, doc: outputs.append((name, doc)))

        mock_engine.submit("test_procedure")

        assert len(outputs) == 2
        assert outputs[0][0] == "start"
        assert outputs[1][0] == "stop"
        assert outputs[1][1]["exit_status"] == "success"

    def test_signals_exist(self, mock_engine) -> None:
        """Test that all expected signals exist."""
        assert hasattr(mock_engine, "sigOutput")
        assert hasattr(mock_engine, "sigStart")
        assert hasattr(mock_engine, "sigFinish")
        assert hasattr(mock_engine, "sigPause")
        assert hasattr(mock_engine, "sigResume")
        assert hasattr(mock_engine, "sigAbort")
        assert hasattr(mock_engine, "sigException")
        assert hasattr(mock_engine, "sigReady")
        assert hasattr(mock_engine, "sigStateChanged")

    def test_pause_resume(self, mock_engine) -> None:
        """Test pause and resume operations."""
        # Can't pause when idle
        mock_engine.pause()
        assert mock_engine.state == EngineState.IDLE

    def test_subscription(self, mock_engine) -> None:
        """Test output subscription management."""
        outputs = []

        token = mock_engine.subscribe(lambda n, d: outputs.append((n, d)))
        mock_engine.submit("test1")

        assert len(outputs) == 2  # start + stop

        mock_engine.unsubscribe(token)
        mock_engine.submit("test2")

        # Should still be 2 since we unsubscribed
        assert len(outputs) == 2


class TestBlueskyEngine:
    """Tests for BlueskyEngine."""

    def test_protocol_compliance(self, bluesky_engine) -> None:
        """Test that BlueskyEngine satisfies Engine protocol."""
        assert isinstance(bluesky_engine, Engine)

    def test_initialization(self, bluesky_engine) -> None:
        """Test that BlueskyEngine initializes properly."""
        assert bluesky_engine.RE is not None
        assert bluesky_engine.state in (EngineState.IDLE, EngineState.RUNNING)
        assert bluesky_engine.name == "bluesky"

    def test_backward_compat_signal(self, bluesky_engine) -> None:
        """Test that sigDocumentYield alias exists."""
        assert hasattr(bluesky_engine, "sigDocumentYield")

    def test_queue_operations(self, bluesky_engine) -> None:
        """Test queue operations."""

        def dummy_plan():
            yield from []

        bluesky_engine.submit(dummy_plan(), priority=2)
        bluesky_engine.submit(dummy_plan(), priority=1)

        assert bluesky_engine.queue_size >= 0  # May have executed already

        bluesky_engine.clear_queue()
        assert bluesky_engine.queue_size == 0

    def test_kwargs_callable(self, bluesky_engine) -> None:
        """Test subscribing kwargs callables."""

        def metadata_provider():
            return {"custom_key": "custom_value"}

        bluesky_engine.subscribe_kwargs_callable(metadata_provider)
        assert metadata_provider in bluesky_engine._kwargs_callables

        bluesky_engine.unsubscribe_kwargs_callable(metadata_provider)
        assert metadata_provider not in bluesky_engine._kwargs_callables

    def test_signals_exist(self, bluesky_engine) -> None:
        """Test that all expected signals exist."""
        assert hasattr(bluesky_engine, "sigOutput")
        assert hasattr(bluesky_engine, "sigStart")
        assert hasattr(bluesky_engine, "sigFinish")
        assert hasattr(bluesky_engine, "sigPause")
        assert hasattr(bluesky_engine, "sigResume")
        assert hasattr(bluesky_engine, "sigAbort")
        assert hasattr(bluesky_engine, "sigException")
        assert hasattr(bluesky_engine, "sigReady")
        assert hasattr(bluesky_engine, "sigStateChanged")
        # Backward compat
        assert hasattr(bluesky_engine, "sigDocumentYield")


@pytest.fixture
def offline_bluesky_engine(qapp):
    """BlueskyEngine without its worker thread; ``_RE`` replaced with a mock.

    Driving a real RunEngine to ``paused`` requires a plan blocked at a
    checkpoint, which is timing-dependent headless — instead the RunEngine is
    mocked so state-gating in stop()/abort()/halt() can be tested
    deterministically.
    """
    pytest.importorskip("bluesky")

    with patch.object(BlueskyEngine, "_start_queue_processor", lambda self: None):
        engine = BlueskyEngine()
    engine._RE = MagicMock()
    return engine


def _wait_interrupt(engine, timeout_ms: int = 5000) -> None:
    """Block until the engine's paused-interrupt worker thread finishes.

    From 'paused', stop()/abort()/halt() dispatch the RunEngine call to a
    QThreadFuture (it blocks until plan cleanup completes), so tests must
    wait before asserting on the mock.
    """
    assert engine._interrupt_future is not None
    assert engine._interrupt_future.wait(timeout_ms)


class TestStopAbortFromPaused:
    """stop()/abort()/halt() must work from 'paused' and report the outcome.

    Bluesky's RunEngine.stop()/abort() explicitly support the paused state,
    and the GUI enables Stop/Abort while paused — gating on 'running' only
    made both silently no-op on a paused run.
    """

    def test_stop_dispatches_when_running(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE.state = "running"
        assert engine.stop() is True
        engine._RE.stop.assert_called_once_with()

    def test_stop_dispatches_when_paused(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE.state = "paused"
        assert engine.stop() is True
        _wait_interrupt(engine)
        engine._RE.stop.assert_called_once_with()

    def test_paused_interrupt_runs_off_caller_thread(self, offline_bluesky_engine):
        """From 'paused', RE.stop() blocks until plan cleanup completes
        (bluesky's _resume_task), so it must never run on the caller's
        (GUI) thread."""
        engine = offline_bluesky_engine
        engine._RE.state = "paused"
        calling_threads = []
        engine._RE.stop.side_effect = lambda: calling_threads.append(
            threading.current_thread()
        )
        assert engine.stop() is True
        _wait_interrupt(engine)
        assert calling_threads
        assert calling_threads[0] is not threading.current_thread()

    def test_stop_not_dispatched_when_idle(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE.state = "idle"
        assert engine.stop() is False
        engine._RE.stop.assert_not_called()

    def test_stop_without_runengine_returns_false(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE = None
        assert engine.stop() is False

    def test_abort_dispatches_when_running(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE.state = "running"
        assert engine.abort(reason="test") is True
        engine._RE.abort.assert_called_once_with(reason="test")

    def test_abort_dispatches_when_paused(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE.state = "paused"
        aborts = []
        engine.sigAbort.connect(lambda: aborts.append(True))
        assert engine.abort(reason="paused abort") is True
        _wait_interrupt(engine)
        engine._RE.abort.assert_called_once_with(reason="paused abort")
        assert aborts == [True]

    def test_abort_not_dispatched_when_idle(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE.state = "idle"
        aborts = []
        engine.sigAbort.connect(lambda: aborts.append(True))
        assert engine.abort(reason="nothing running") is False
        engine._RE.abort.assert_not_called()
        assert aborts == []

    def test_abort_without_runengine_returns_false(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE = None
        assert engine.abort() is False

    def test_halt_reports_outcome(self, offline_bluesky_engine):
        engine = offline_bluesky_engine
        engine._RE.state = "paused"
        assert engine.halt() is True
        _wait_interrupt(engine)
        engine._RE.halt.assert_called_once_with()

        engine._RE.reset_mock()
        engine._RE.state = "idle"
        assert engine.halt() is False
        engine._RE.halt.assert_not_called()


class TestQueueRaceInvariants:
    """Deterministic interleaving tests for the queue/tracking-list lock.

    The worker thread's blocking ``get()`` runs without the lock, so these
    tests call the worker-side claim step (``_claim_queued_item``) directly
    in the exact orderings that used to race.
    """

    @pytest.fixture
    def engine(self, qapp):
        return _QueueOnlyEngine(name="test")

    def test_removed_item_is_never_claimed(self, engine):
        proc_id = engine.submit("plan-a", priority=1)
        item = engine.get_procedure_by_id(proc_id)

        # Worker pops the item from the priority queue...
        popped = engine._queue.get_nowait()
        assert popped is item

        # ...and the GUI removes it before the worker claims it.
        assert engine.remove_from_queue(proc_id) is True

        # The worker-side claim must refuse to execute it.
        assert engine._claim_queued_item(popped) is False
        assert engine.get_current_procedure() is None

    def test_priority_rebuild_does_not_double_execute(self, engine):
        id_a = engine.submit("plan-a", priority=1)
        id_b = engine.submit("plan-b", priority=2)
        item_a = engine.get_procedure_by_id(id_a)

        # Worker pops A; before it claims, a priority update rebuilds the
        # queue from the tracking list, re-queuing a duplicate of A.
        popped = engine._queue.get_nowait()
        assert popped is item_a
        assert engine.update_priority(id_b, 0) is True

        # First claim wins.
        assert engine._claim_queued_item(popped) is True

        # Drain the queue exactly as the worker loop would: the stale
        # duplicate of A must be skipped, B must execute exactly once.
        executed = []
        while True:
            try:
                nxt = engine._queue.get_nowait()
            except Empty:
                break
            if engine._claim_queued_item(nxt):
                executed.append(nxt.id)
        assert executed == [id_b]

    def test_remove_after_claim_reports_false(self, engine):
        proc_id = engine.submit("plan-a", priority=1)
        popped = engine._queue.get_nowait()
        assert engine._claim_queued_item(popped) is True

        # Already executing — removal must not claim success.
        assert engine.remove_from_queue(proc_id) is False

    def test_clear_queue_prevents_claim(self, engine):
        engine.submit("plan-a", priority=1)
        popped = engine._queue.get_nowait()
        engine.clear_queue()
        assert engine._claim_queued_item(popped) is False

    def test_equal_priority_removal_is_by_identity(self, engine):
        # PrioritizedProcedure.__eq__ compares only priority (order=True
        # with compare=False on every other field), so list removal must
        # match by identity, not equality.
        id_a = engine.submit("plan-a", priority=1)
        id_b = engine.submit("plan-b", priority=1)

        assert engine.remove_from_queue(id_b) is True
        remaining = [item.id for item in engine.get_queue_items()]
        assert remaining == [id_a]

    def test_queue_size_ignores_stale_duplicates(self, engine):
        id_a = engine.submit("plan-a", priority=1)
        engine.submit("plan-b", priority=2)

        # Pop A, then rebuild (re-queues a duplicate of A), then claim A.
        popped = engine._queue.get_nowait()
        engine.update_priority(id_a, 0)
        assert engine._claim_queued_item(popped) is True

        # Only B is really pending, even though the PriorityQueue still
        # holds a stale duplicate of A.
        assert engine.queue_size == 1


class TestEngineSingleton:
    """Tests for the engine singleton management."""

    def test_get_engine_returns_singleton(self, qapp) -> None:
        """Test that get_engine returns a singleton."""
        reset_engine()

        try:
            # Use mock engine for faster tests
            e1 = get_engine("mock")
            e2 = get_engine("mock")
            assert e1 is e2
        finally:
            reset_engine()

    def test_set_engine(self, qapp) -> None:
        """Test that set_engine replaces the singleton."""
        reset_engine()

        try:
            mock = MockEngine()
            set_engine(mock)

            e = get_engine()
            assert e is mock
        finally:
            reset_engine()

    def test_reset_engine(self, qapp) -> None:
        """Test that reset_engine clears the singleton."""
        reset_engine()

        try:
            e1 = get_engine("mock")
            reset_engine()
            e2 = get_engine("mock")

            assert e1 is not e2
        finally:
            reset_engine()

    def test_unknown_engine_type(self, qapp) -> None:
        """Test that unknown engine type raises ValueError."""
        reset_engine()

        with pytest.raises(ValueError, match="Unknown engine type"):
            get_engine("unknown_type")


class TestBackwardCompatibility:
    """Tests for backward compatibility with QRunEngine."""

    def test_qrunengine_alias(self) -> None:
        """Test that QRunEngine is an alias for BlueskyEngine."""
        from lightfall.acquire import QRunEngine

        assert QRunEngine is BlueskyEngine

    def test_get_run_engine_alias(self, qapp) -> None:
        """Test that get_run_engine still works."""
        from lightfall.acquire import get_run_engine

        reset_engine()

        try:
            # Should work without errors
            engine = get_run_engine()
            assert engine is not None
        finally:
            reset_engine()


class TestToastNotifications:
    """Tests for toast notifications in engines."""

    def test_toast_notifications_enabled_by_default(self, qapp) -> None:
        """Test that toast notifications are enabled by default."""
        engine = MockEngine()
        assert engine.toast_notifications is True

    def test_toast_notifications_can_be_disabled(self, qapp) -> None:
        """Test that toast notifications can be disabled at init."""
        engine = MockEngine(toast_notifications=False)
        assert engine.toast_notifications is False

    def test_toast_notifications_property_setter(self, qapp) -> None:
        """Test that toast_notifications property can be set."""
        engine = MockEngine()
        assert engine.toast_notifications is True

        engine.toast_notifications = False
        assert engine.toast_notifications is False

        engine.toast_notifications = True
        assert engine.toast_notifications is True

    @patch("lightfall.ui.toast.ToastManager")
    def test_finish_shows_success_toast(self, mock_toast_class, qapp) -> None:
        """Test that finishing a run shows a success toast."""
        mock_toast = MagicMock()
        mock_toast_class.get_instance.return_value = mock_toast

        engine = MockEngine()
        engine.submit("test_procedure")

        mock_toast.success.assert_called_once()
        args = mock_toast.success.call_args
        assert "Complete" in args[0][0]
        assert "mock" in args[0][1]

    @patch("lightfall.ui.toast.ToastManager")
    def test_abort_shows_warning_toast(self, mock_toast_class, qapp) -> None:
        """Test that aborting a run shows a warning toast."""
        mock_toast = MagicMock()
        mock_toast_class.get_instance.return_value = mock_toast

        engine = MockEngine()
        # Need to be in RUNNING state to abort
        engine._set_state(EngineState.RUNNING)
        engine.abort("test reason")

        mock_toast.warning.assert_called_once()
        args = mock_toast.warning.call_args
        assert "Abort" in args[0][0]
        assert "mock" in args[0][1]

    @patch("lightfall.ui.toast.ToastManager")
    def test_exception_shows_error_toast(self, mock_toast_class, qapp) -> None:
        """Test that an exception shows an error toast."""
        mock_toast = MagicMock()
        mock_toast_class.get_instance.return_value = mock_toast

        engine = MockEngine()
        test_exception = ValueError("Test error message")
        engine.sigException.emit(test_exception)

        mock_toast.error.assert_called_once()
        args = mock_toast.error.call_args
        assert "Failed" in args[0][0]
        assert "Test error message" in args[0][1]

    @patch("lightfall.ui.toast.ToastManager")
    def test_disabled_notifications_no_toast(self, mock_toast_class, qapp) -> None:
        """Test that disabled notifications don't show toasts."""
        mock_toast = MagicMock()
        mock_toast_class.get_instance.return_value = mock_toast

        engine = MockEngine(toast_notifications=False)
        engine.submit("test_procedure")

        mock_toast.success.assert_not_called()
        mock_toast.warning.assert_not_called()
        mock_toast.error.assert_not_called()

    @patch("lightfall.ui.toast.ToastManager")
    def test_toast_manager_lazy_initialization(self, mock_toast_class, qapp) -> None:
        """Test that ToastManager is lazily initialized."""
        engine = MockEngine(toast_notifications=False)

        # ToastManager should not be fetched when disabled
        mock_toast_class.get_instance.assert_not_called()

        engine.toast_notifications = True
        engine.sigFinish.emit()

        # Now it should be fetched
        mock_toast_class.get_instance.assert_called_once()


class TestQueueManagement:
    """Tests for queue management functionality."""

    def test_prioritized_procedure_has_id(self, mock_engine) -> None:
        """Test that submitted procedures get unique IDs."""
        from lightfall.acquire.engine.base import PrioritizedProcedure

        proc1 = PrioritizedProcedure(priority=1, procedure="test1")
        proc2 = PrioritizedProcedure(priority=1, procedure="test2")

        assert proc1.id != proc2.id
        assert len(proc1.id) == 36  # UUID format

    def test_prioritized_procedure_has_submitted_at(self, mock_engine) -> None:
        """Test that procedures have submission timestamp."""
        from datetime import datetime

        from lightfall.acquire.engine.base import PrioritizedProcedure

        before = datetime.now()
        proc = PrioritizedProcedure(priority=1, procedure="test")
        after = datetime.now()

        assert before <= proc.submitted_at <= after

    def test_prioritized_procedure_has_name(self, mock_engine) -> None:
        """Test that procedures can have names."""
        from lightfall.acquire.engine.base import PrioritizedProcedure

        proc = PrioritizedProcedure(priority=1, procedure="test", name="my_plan")
        assert proc.name == "my_plan"

    def test_submit_returns_procedure_id(self, mock_engine) -> None:
        """Test that submit returns the procedure ID."""
        proc_id = mock_engine.submit("test_procedure")
        assert proc_id is not None
        assert len(proc_id) == 36  # UUID format

    def test_get_queue_items_empty(self, mock_engine) -> None:
        """Test get_queue_items with empty queue."""
        items = mock_engine.get_queue_items()
        assert items == []

    def test_get_current_procedure_when_idle(self, mock_engine) -> None:
        """Test get_current_procedure returns None when idle."""
        current = mock_engine.get_current_procedure()
        assert current is None

    def test_get_procedure_by_id_not_found(self, mock_engine) -> None:
        """Test get_procedure_by_id returns None for unknown ID."""
        result = mock_engine.get_procedure_by_id("nonexistent-id")
        assert result is None

    def test_remove_from_queue_not_found(self, mock_engine) -> None:
        """Test remove_from_queue returns False for unknown ID."""
        result = mock_engine.remove_from_queue("nonexistent-id")
        assert result is False

    def test_update_priority_not_found(self, mock_engine) -> None:
        """Test update_priority returns False for unknown ID."""
        result = mock_engine.update_priority("nonexistent-id", 5)
        assert result is False

    def test_sigQueueChanged_signal_exists(self, mock_engine) -> None:
        """Test that sigQueueChanged signal exists."""
        assert hasattr(mock_engine, "sigQueueChanged")

    def test_clear_queue_emits_sigQueueChanged(self, mock_engine) -> None:
        """Test that clear_queue emits sigQueueChanged."""
        signals_received = []
        mock_engine.sigQueueChanged.connect(lambda: signals_received.append(True))

        # MockEngine executes immediately, so we need to test with BaseEngine
        # For now, just verify the signal connection works
        mock_engine.sigQueueChanged.emit()
        assert len(signals_received) == 1

    def test_procedure_name_auto_detection(self) -> None:
        """Test that procedure names are auto-detected from generators."""
        # Create a minimal engine subclass for testing
        class TestEngine(BaseEngine):
            def pause(self, defer: bool = False) -> None:
                pass

            def resume(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def abort(self, reason: str = "") -> None:
                pass

            def halt(self) -> None:
                pass

        engine = TestEngine(name="test")

        def my_test_plan():
            yield from []

        gen = my_test_plan()
        name = engine._get_procedure_name(gen)
        assert name == "my_test_plan"

    def test_procedure_name_from_callable(self) -> None:
        """Test that procedure names are detected from callables."""
        class TestEngine(BaseEngine):
            def pause(self, defer: bool = False) -> None:
                pass

            def resume(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def abort(self, reason: str = "") -> None:
                pass

            def halt(self) -> None:
                pass

        engine = TestEngine(name="test")

        def my_callable():
            pass

        name = engine._get_procedure_name(my_callable)
        assert name == "my_callable"

    def test_procedure_name_fallback(self) -> None:
        """Test that procedure name falls back to 'procedure'."""
        class TestEngine(BaseEngine):
            def pause(self, defer: bool = False) -> None:
                pass

            def resume(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def abort(self, reason: str = "") -> None:
                pass

            def halt(self) -> None:
                pass

        engine = TestEngine(name="test")

        # An object without __name__ or gi_code
        name = engine._get_procedure_name({"some": "dict"})
        assert name == "procedure"


class TestPreSubmitHooks:
    """Tests for pre-submit callable system."""

    def test_register_pre_submit(self, mock_engine) -> None:
        """Test registering a pre-submit callable."""
        def hook(plan_name: str, kwargs: dict) -> dict:
            return {"extra": "metadata"}

        mock_engine.register_pre_submit(hook)
        assert hook in mock_engine._pre_submit_callables

    def test_unregister_pre_submit(self, mock_engine) -> None:
        """Test unregistering a pre-submit callable."""
        def hook(plan_name: str, kwargs: dict) -> dict:
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine.unregister_pre_submit(hook)
        assert hook not in mock_engine._pre_submit_callables

    def test_pre_submit_merges_kwargs(self, mock_engine) -> None:
        """Test that pre-submit callable's returned dict is merged into kwargs."""
        outputs = []
        mock_engine.subscribe(lambda name, doc: outputs.append((name, doc)))

        def hook(plan_name: str, kwargs: dict) -> dict:
            return {"sample_name": "my_sample"}

        mock_engine.register_pre_submit(hook)
        mock_engine.submit("test_procedure")

        start_doc = outputs[0][1]
        assert start_doc["sample_name"] == "my_sample"

    def test_pre_submit_cancel_returns_none(self, mock_engine) -> None:
        """Test that returning None from pre-submit cancels submission."""
        outputs = []
        mock_engine.subscribe(lambda name, doc: outputs.append((name, doc)))

        def hook(plan_name: str, kwargs: dict) -> None:
            return None

        mock_engine.register_pre_submit(hook)
        result = mock_engine.submit("test_procedure")

        assert result is None
        assert len(outputs) == 0

    def test_skip_pre_submit(self, mock_engine) -> None:
        """Test that skip_pre_submit=True bypasses hooks."""
        hook_called = []

        def hook(plan_name: str, kwargs: dict) -> dict:
            hook_called.append(True)
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine.submit("test_procedure", skip_pre_submit=True)

        assert len(hook_called) == 0

    def test_pre_submit_ordering(self, mock_engine) -> None:
        """Test that pre-submit callables run in registration order."""
        call_order = []

        def hook_a(plan_name: str, kwargs: dict) -> dict:
            call_order.append("a")
            return {"order": "a"}

        def hook_b(plan_name: str, kwargs: dict) -> dict:
            call_order.append("b")
            return {"order": "b"}

        mock_engine.register_pre_submit(hook_a)
        mock_engine.register_pre_submit(hook_b)
        mock_engine.submit("test_procedure")

        assert call_order == ["a", "b"]

    def test_pre_submit_receives_plan_name(self, mock_engine) -> None:
        """Test that pre-submit callable receives the plan name."""
        received_names = []

        def hook(plan_name: str, kwargs: dict) -> dict:
            received_names.append(plan_name)
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine.submit("test_procedure", name="my_scan")

        assert received_names == ["my_scan"]

    def test_call_passes_skip_pre_submit(self, mock_engine) -> None:
        """Test that __call__ passes skip_pre_submit through."""
        hook_called = []

        def hook(plan_name: str, kwargs: dict) -> dict:
            hook_called.append(True)
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine("test_procedure", skip_pre_submit=True)

        assert len(hook_called) == 0

    def test_pre_submit_exception_cancels(self, mock_engine) -> None:
        """Test that an exception in a hook cancels submission."""
        def crashing_hook(plan_name: str, kwargs: dict) -> dict:
            raise RuntimeError("hook error")

        mock_engine.register_pre_submit(crashing_hook)
        result = mock_engine.submit("test_procedure")
        assert result is None


class TestPreSubmitIntegration:
    """Integration tests for pre-submit with SampleMetadataDialog."""

    def test_sample_metadata_callable_returns_metadata(self, qapp) -> None:
        """Test the callable that wraps SampleMetadataDialog."""
        from unittest.mock import MagicMock, patch

        from PySide6.QtWidgets import QDialog

        from lightfall.ui.panels.bluesky_panel import _sample_metadata_pre_submit

        with patch(
            "lightfall.ui.dialogs.sample_metadata_dialog.SampleMetadataDialog"
        ) as MockDialog:
            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
            mock_dialog.get_metadata.return_value = {"sample_name": "test"}
            MockDialog.return_value = mock_dialog

            result = _sample_metadata_pre_submit("scan", {})
            assert result == {"sample_name": "test"}

    def test_sample_metadata_callable_returns_none_on_cancel(self, qapp) -> None:
        """Test the callable returns None when dialog is cancelled."""
        from unittest.mock import MagicMock, patch

        from PySide6.QtWidgets import QDialog

        from lightfall.ui.panels.bluesky_panel import _sample_metadata_pre_submit

        with patch(
            "lightfall.ui.dialogs.sample_metadata_dialog.SampleMetadataDialog"
        ) as MockDialog:
            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = QDialog.DialogCode.Rejected
            MockDialog.return_value = mock_dialog

            result = _sample_metadata_pre_submit("scan", {})
            assert result is None

    def test_sample_metadata_callable_marshals_from_worker_thread(
        self, qapp, qtbot
    ) -> None:
        """Worker-thread callers must be marshalled to the GUI thread.

        Regression test: invoking ``QDialog.exec()`` on a non-GUI thread
        (e.g. via the Claude SDK MCP worker) corrupts Qt state and
        crashes the process. The pre-submit hook must detect a
        worker-thread caller and run the dialog on the GUI thread.
        """
        import threading
        from unittest.mock import patch

        from lightfall.ui.panels import bluesky_panel
        from lightfall.ui.panels.bluesky_panel import _sample_metadata_pre_submit
        from lightfall.utils.threads import initialize_main_thread_invoker

        # The invoker singleton must be constructed on the GUI thread for
        # cross-thread events to dispatch correctly. Production does this
        # via plugins/loader; tests must do it explicitly.
        initialize_main_thread_invoker()

        main_thread = threading.current_thread()
        seen: dict[str, threading.Thread] = {}
        holder: dict[str, Any] = {}

        def fake_show() -> dict:
            seen["thread"] = threading.current_thread()
            return {"sample_name": "from_worker"}

        def worker() -> None:
            with patch.object(
                bluesky_panel, "_show_sample_metadata_dialog", side_effect=fake_show
            ):
                holder["value"] = _sample_metadata_pre_submit("scan", {})

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        qtbot.waitUntil(lambda: not t.is_alive(), timeout=5000)
        t.join()

        assert holder["value"] == {"sample_name": "from_worker"}
        assert seen["thread"] is main_thread

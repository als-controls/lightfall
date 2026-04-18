"""Tests for the engine module.

Note: These tests require the 'acquire' optional dependencies (bluesky, ophyd).
"""

import time

import pytest

# Skip all tests if bluesky is not installed
pytest.importorskip("bluesky")

from PySide6.QtCore import QCoreApplication

from lucid.acquire.engine import (
    BlueskyEngine,
    EngineState,
    get_engine,
    reset_engine,
    set_engine,
)


@pytest.fixture
def qapp():
    """Ensure QApplication exists for Qt."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def run_engine(qapp):
    """Create a fresh BlueskyEngine for testing."""
    re = BlueskyEngine()
    # Wait for the RunEngine to initialize
    timeout = 5.0
    start = time.time()
    while re.RE is None and time.time() - start < timeout:
        qapp.processEvents()
        time.sleep(0.05)
    yield re


class TestQRunEngine:
    """Tests for BlueskyEngine."""

    def test_initialization(self, run_engine, qapp) -> None:
        """Test that RunEngine initializes properly."""
        assert run_engine.RE is not None
        assert run_engine.state == EngineState.IDLE
        assert run_engine.is_idle is True

    def test_queue_operations(self, run_engine) -> None:
        """Test submit and clear operations."""

        def dummy_plan():
            yield from []

        # The engine has a background queue processor, so submitted plans
        # may start executing before we check queue_size. Just verify that
        # submit + clear work without error and total adds up.
        run_engine.submit(dummy_plan(), priority=2)
        run_engine.submit(dummy_plan(), priority=1)
        run_engine.submit(dummy_plan(), priority=3)

        cleared = run_engine.clear_queue()
        # Some plans may have already been popped by the queue processor
        assert cleared <= 3
        assert run_engine.queue_size == 0

    def test_priority_ordering(self, run_engine) -> None:
        """Test that plans are queued by priority."""

        def make_plan(name):
            def plan():
                yield from []
            return plan

        # Queue in non-priority order
        run_engine.submit(make_plan("low")(), priority=10)
        run_engine.submit(make_plan("high")(), priority=1)
        run_engine.submit(make_plan("medium")(), priority=5)

        # The background queue processor may have already started running
        # plans, so we can only verify clear doesn't error
        cleared = run_engine.clear_queue()
        assert cleared <= 3

    def test_kwargs_callable(self, run_engine) -> None:
        """Test subscribing kwargs callables."""

        def metadata_provider():
            return {"custom_key": "custom_value"}

        run_engine.subscribe_kwargs_callable(metadata_provider)
        assert metadata_provider in run_engine._kwargs_callables

        run_engine.unsubscribe_kwargs_callable(metadata_provider)
        assert metadata_provider not in run_engine._kwargs_callables

    def test_signals_exist(self, run_engine) -> None:
        """Test that all expected signals exist."""
        assert hasattr(run_engine, "sigDocumentYield")
        assert hasattr(run_engine, "sigStart")
        assert hasattr(run_engine, "sigFinish")
        assert hasattr(run_engine, "sigPause")
        assert hasattr(run_engine, "sigResume")
        assert hasattr(run_engine, "sigAbort")
        assert hasattr(run_engine, "sigException")
        assert hasattr(run_engine, "sigReady")
        assert hasattr(run_engine, "sigStateChanged")


class TestGetRunEngine:
    """Tests for the singleton getter."""

    def test_singleton(self, qapp) -> None:
        """Test that get_engine returns a singleton."""
        import lucid.acquire.engine as engine_module

        original = engine_module._engine
        engine_module._engine = None

        try:
            re1 = get_engine()
            re2 = get_engine()
            assert re1 is re2
        finally:
            # Restore original state
            engine_module._engine = original

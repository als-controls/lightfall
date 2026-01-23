"""Tests for the Engine abstraction layer.

Tests the Engine protocol, BaseEngine, BlueskyEngine, and MockEngine.
"""

import time

import pytest

from PySide6.QtCore import QCoreApplication

from ncs.acquire.engine import (
    Engine,
    EngineState,
    BaseEngine,
    BlueskyEngine,
    MockEngine,
    get_engine,
    set_engine,
    reset_engine,
)


@pytest.fixture
def qapp():
    """Ensure QApplication exists for Qt."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


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

        cleared = bluesky_engine.clear_queue()
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
        from ncs.acquire import QRunEngine

        assert QRunEngine is BlueskyEngine

    def test_get_run_engine_alias(self, qapp) -> None:
        """Test that get_run_engine still works."""
        from ncs.acquire import get_run_engine

        reset_engine()

        try:
            # Should work without errors
            engine = get_run_engine()
            assert engine is not None
        finally:
            reset_engine()

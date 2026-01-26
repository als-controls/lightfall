"""Tests for application exit code handling."""

import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QThread

from ncs.utils.threads import QThreadFuture, ThreadManager, get_thread_manager


class TestThreadShutdown:
    """Tests for proper thread shutdown on application exit."""

    def test_thread_responds_to_interruption(self, qapp) -> None:
        """Test that a thread with a while loop responds to interruption."""
        loop_running = False
        loop_exited = False

        def infinite_loop():
            nonlocal loop_running, loop_exited
            loop_running = True
            while not QThread.currentThread().isInterruptionRequested():
                time.sleep(0.01)
            loop_exited = True

        future = QThreadFuture(infinite_loop, key="test_loop", name="Test Loop")
        future.start()

        # Wait for loop to start
        timeout = 1.0
        start = time.monotonic()
        while not loop_running and time.monotonic() - start < timeout:
            time.sleep(0.01)
        assert loop_running, "Loop did not start"

        # Cancel and verify it exits
        result = future.cancel(timeout_ms=2000)
        assert result, "Thread did not stop gracefully"
        assert loop_exited, "Loop did not exit cleanly"

    def test_thread_manager_shutdown_cancels_threads(self, qapp) -> None:
        """Test that ThreadManager.shutdown() cancels all active threads."""
        threads_cancelled = []

        def tracked_loop(name):
            while not QThread.currentThread().isInterruptionRequested():
                time.sleep(0.01)
            threads_cancelled.append(name)

        manager = get_thread_manager()

        # Start multiple threads
        futures = []
        for i in range(3):
            f = QThreadFuture(
                tracked_loop, f"thread_{i}",
                key=f"test_thread_{i}",
                name=f"Test Thread {i}"
            )
            f.start()
            futures.append(f)

        # Wait for threads to start
        time.sleep(0.1)

        # Verify threads are running
        active = manager.get_active()
        assert len(active) >= 3, f"Expected at least 3 active threads, got {len(active)}"

        # Shutdown
        manager.shutdown()

        # Verify all threads were cancelled
        assert len(threads_cancelled) == 3, f"Expected 3 threads cancelled, got {threads_cancelled}"

    def test_thread_manager_connects_to_app_after_creation(self, qapp) -> None:
        """Test ThreadManager connects shutdown even if created before QApplication."""
        # Reset and recreate manager to simulate fresh start
        with patch.object(ThreadManager, '_instance', None):
            with patch.object(ThreadManager, '_lock', ThreadManager._lock):
                # Create manager - at this point it should try to connect
                manager = ThreadManager()
                manager._initialized = False
                manager.__init__()

                # Verify it connected to aboutToQuit (app exists)
                assert manager._shutdown_connected, "Manager should connect when app exists"


class TestBlueskyEngineShutdown:
    """Tests for BlueskyEngine shutdown behavior."""

    def test_bluesky_engine_responds_to_shutdown(self, qapp) -> None:
        """Test that BlueskyEngine queue processor exits on interruption."""
        pytest.importorskip("bluesky")

        from ncs.acquire.engine.bluesky import BlueskyEngine

        engine = BlueskyEngine()

        # Wait for engine to initialize
        time.sleep(0.2)

        # Verify the queue processor is running
        assert engine._queue_future is not None
        assert engine._queue_future.isRunning()

        # Cancel the processor thread
        result = engine._queue_future.cancel(timeout_ms=3000)

        assert result, "BlueskyEngine queue processor did not stop gracefully"


class TestApplicationExitCode:
    """Tests for application exit code."""

    def test_ncs_application_quit_uses_zero(self, qapp) -> None:
        """Test that NCSApplication.quit() defaults to exit code 0."""
        from ncs.core.application import NCSApplication

        # Get or create app (don't reset - use existing qapp)
        app = NCSApplication.get_instance()
        if app._qt_app is None:
            app._qt_app = qapp

        # Verify quit calls exit with 0 by default
        with patch.object(qapp, 'exit') as mock_exit:
            app.quit()
            mock_exit.assert_called_once_with(0)

        # Also verify with explicit code
        with patch.object(qapp, 'exit') as mock_exit:
            app.quit(exit_code=42)
            mock_exit.assert_called_once_with(42)

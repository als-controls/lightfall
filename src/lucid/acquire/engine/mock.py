"""Mock engine implementation.

Provides a simple mock engine for testing without hardware.
"""

from __future__ import annotations

import uuid
from typing import Any

from lucid.acquire.engine.base import BaseEngine, PrioritizedProcedure
from lucid.acquire.engine.state import EngineState
from lucid.utils.logging import logger

__all__ = ["MockEngine"]


class MockEngine(BaseEngine):
    """Mock engine for testing without real hardware.

    This engine executes procedures synchronously and emits mock documents.
    Useful for testing UI components and workflows without Bluesky or hardware.

    The mock engine supports:
    - Synchronous execution (no background thread)
    - Mock start/stop documents with UIDs
    - Simulated pause/resume/stop/abort operations
    - Configurable procedure handling

    Example:
        engine = MockEngine()

        outputs = []
        engine.subscribe(lambda name, doc: outputs.append((name, doc)))

        engine.submit("test_procedure")

        assert len(outputs) == 2  # start + stop documents
        assert outputs[0][0] == "start"
        assert outputs[1][0] == "stop"
    """

    def __init__(self, *, toast_notifications: bool = True) -> None:
        """Initialize the mock engine.

        Args:
            toast_notifications: Whether to show toast notifications on run
                completion, abort, and errors. Defaults to True.
        """
        super().__init__(name="mock", toast_notifications=toast_notifications)
        self._paused = False
        self._current_uid: str | None = None

    def submit(self, procedure: Any, *, priority: int = 1, **kwargs: Any) -> None:
        """Submit a procedure for execution.

        The mock engine executes immediately and synchronously.

        Args:
            procedure: The procedure to execute (ignored in mock).
            priority: Queue priority (ignored in mock).
            **kwargs: Additional parameters (included in start document).
        """
        # Generate a unique ID for this "run"
        self._current_uid = str(uuid.uuid4())

        self._set_state(EngineState.RUNNING)
        self.sigStart.emit()

        logger.debug(f"[mock] Executing mock procedure: {procedure}")

        # Emit mock start document
        start_doc = {
            "uid": self._current_uid,
            "plan_name": str(procedure) if procedure else "mock_plan",
            "time": 0.0,
            **kwargs,
        }
        self._emit_output("start", start_doc)

        # Simulate procedure execution (no-op for mock)
        # In a more sophisticated mock, we could yield events here

        # Emit mock stop document
        stop_doc = {
            "uid": str(uuid.uuid4()),
            "run_start": self._current_uid,
            "exit_status": "success",
            "time": 0.0,
            "num_events": {},
        }
        self._emit_output("stop", stop_doc)

        self._current_uid = None
        self._set_state(EngineState.IDLE)
        self.sigFinish.emit()

    def pause(self, defer: bool = False) -> None:
        """Pause execution.

        Args:
            defer: Ignored in mock (always immediate).
        """
        if self._state == EngineState.RUNNING:
            self._paused = True
            self._set_state(EngineState.PAUSED)
            self.sigPause.emit()
            logger.debug("[mock] Paused")

    def resume(self) -> None:
        """Resume paused execution."""
        if self._state == EngineState.PAUSED:
            self._paused = False
            self._set_state(EngineState.RUNNING)
            self.sigResume.emit()
            logger.debug("[mock] Resumed")

    def stop(self) -> None:
        """Request graceful stop."""
        if self._state in (EngineState.RUNNING, EngineState.PAUSED):
            self._set_state(EngineState.IDLE)
            logger.debug("[mock] Stopped")

    def abort(self, reason: str = "") -> None:
        """Abort execution immediately.

        Args:
            reason: Optional reason for the abort.
        """
        if self._state in (EngineState.RUNNING, EngineState.PAUSED):
            logger.debug(f"[mock] Aborted: {reason or 'No reason'}")
            self._set_state(EngineState.IDLE)
            self.sigAbort.emit()

    def halt(self) -> None:
        """Emergency halt."""
        if self._state != EngineState.IDLE:
            logger.debug("[mock] Halted")
            self._set_state(EngineState.IDLE)
            self.sigAbort.emit()

"""Engine state enumeration.

Defines the possible states for an execution engine.
"""

from __future__ import annotations

from enum import Enum, auto


class EngineState(Enum):
    """State of an execution engine.

    Attributes:
        IDLE: Engine is ready to accept procedures.
        RUNNING: Engine is executing a procedure.
        PAUSED: Execution is paused and can be resumed.
        STOPPING: Graceful stop has been requested.
        ABORTING: Immediate abort has been requested.
        ERROR: Engine is in an error state.
    """

    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    ABORTING = auto()
    ERROR = auto()

    def __str__(self) -> str:
        """Return lowercase state name for display."""
        return self.name.lower()

"""NCS Engine abstraction layer.

Provides a protocol-based abstraction for execution engines,
allowing multiple engine implementations (Bluesky, mock, etc.).

Components:
- Engine: Protocol defining the engine interface
- EngineState: Enumeration of possible engine states
- BaseEngine: Abstract base class with common implementation
- BlueskyEngine: Bluesky RunEngine wrapper
- MockEngine: Simple mock engine for testing
- get_engine(): Singleton accessor for the default engine

Example:
    from ncs.acquire.engine import get_engine, EngineState

    engine = get_engine()

    # Subscribe to output
    engine.sigOutput.connect(lambda name, doc: print(f"{name}: {doc}"))

    # Submit a procedure
    engine.submit(my_plan)

    # Check state
    if engine.state == EngineState.IDLE:
        print("Ready for more procedures")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ncs.acquire.engine.base import BaseEngine, PrioritizedProcedure
from ncs.acquire.engine.bluesky import BlueskyEngine
from ncs.acquire.engine.mock import MockEngine
from ncs.acquire.engine.protocol import Engine
from ncs.acquire.engine.state import EngineState
from ncs.utils.logging import logger

if TYPE_CHECKING:
    pass

__all__ = [
    # Protocol
    "Engine",
    # State
    "EngineState",
    # Base class
    "BaseEngine",
    "PrioritizedProcedure",
    # Implementations
    "BlueskyEngine",
    "MockEngine",
    # Singleton
    "get_engine",
]

# Module-level singleton
_engine: BaseEngine | None = None


def get_engine(engine_type: str = "bluesky", **kwargs: Any) -> BaseEngine:
    """Get the global engine instance.

    Creates the instance on first call. Subsequent calls return the same instance.

    Args:
        engine_type: Type of engine to create. Options:
            - "bluesky": BlueskyEngine (default)
            - "mock": MockEngine
        **kwargs: Arguments passed to engine on first initialization.

    Returns:
        The global engine instance.

    Raises:
        ValueError: If engine_type is not recognized.

    Example:
        # Get default Bluesky engine
        engine = get_engine()

        # Get mock engine for testing
        engine = get_engine("mock")

    Note:
        The engine type is only used on first initialization.
        Subsequent calls return the existing instance regardless of type.
    """
    global _engine

    if _engine is None:
        if engine_type == "bluesky":
            _engine = BlueskyEngine(**kwargs)
        elif engine_type == "mock":
            _engine = MockEngine()
        else:
            raise ValueError(f"Unknown engine type: {engine_type}")

        logger.debug(f"Created global {engine_type} engine instance")

    return _engine


def set_engine(engine: BaseEngine) -> None:
    """Set the global engine instance.

    Allows replacing the default engine with a custom implementation.

    Args:
        engine: The engine instance to use globally.

    Example:
        from ncs.acquire.engine import set_engine, MockEngine

        # Use mock engine for testing
        set_engine(MockEngine())
    """
    global _engine
    _engine = engine
    logger.debug(f"Set global engine to {engine.name}")


def reset_engine() -> None:
    """Reset the global engine instance.

    Clears the singleton, allowing a new engine to be created on next get_engine() call.
    Primarily useful for testing.
    """
    global _engine
    _engine = None
    logger.debug("Reset global engine")

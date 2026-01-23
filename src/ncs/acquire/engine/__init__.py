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


def get_engine(engine_type: str | None = None, **kwargs: Any) -> BaseEngine:
    """Get the global engine instance.

    Uses the plugin system and user preferences to determine which
    engine to create. Creates the instance on first call; subsequent
    calls return the same instance.

    Args:
        engine_type: Override engine type. If None, uses preference or default.
            Options include: "bluesky", "mock", or any registered engine plugin.
        **kwargs: Arguments passed to engine on first initialization.

    Returns:
        The global engine instance.

    Raises:
        ValueError: If engine_type is not recognized.

    Example:
        # Get engine based on user preference (or default)
        engine = get_engine()

        # Get specific engine type
        engine = get_engine("mock")

    Note:
        The engine type is only used on first initialization.
        Subsequent calls return the existing instance regardless of type.
    """
    global _engine

    if _engine is None:
        from ncs.acquire.engine.registry import EngineRegistry

        registry = EngineRegistry.get_instance()

        # Determine engine type
        if engine_type is None:
            # Try to get from preferences
            try:
                from ncs.ui.preferences import PreferencesManager

                prefs = PreferencesManager.get_instance()
                engine_type = prefs.get("engine", registry.default_engine)
            except Exception:
                engine_type = registry.default_engine

        # Get plugin and create engine
        plugin = registry.get(engine_type)
        if plugin is not None:
            _engine = plugin.create_engine(**kwargs)
            logger.debug(f"Created global {engine_type} engine via plugin")
        else:
            # Fallback to direct instantiation for backward compat
            if engine_type == "bluesky":
                _engine = BlueskyEngine(**kwargs)
            elif engine_type == "mock":
                _engine = MockEngine()
            else:
                raise ValueError(f"Unknown engine type: {engine_type}")
            logger.debug(f"Created global {engine_type} engine (direct)")

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

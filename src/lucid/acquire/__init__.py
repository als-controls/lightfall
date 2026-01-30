"""NCS data acquisition module.

Provides Qt-integrated execution engines for scientific data acquisition.

Components:
- Engine: Protocol defining the engine interface
- EngineState: Enumeration of possible engine states
- BaseEngine: Abstract base class with common implementation
- BlueskyEngine: Bluesky RunEngine wrapper (also available as QRunEngine)
- MockEngine: Simple mock engine for testing
- get_engine: Singleton accessor for the default engine
- NCSDevice: Generic wrapper adding metadata/policy to ophyd devices
- SignalConfiguration: UI model for signal selection
- LiveDataBuffer: Thread-safe document streaming to Qt
- PlanRegistry: Central registry of available plans
"""

from lucid.acquire.buffer import LiveDataBuffer, MultiStreamBuffer
from lucid.acquire.device_wrapper import NCSDevice, wrap_device
from lucid.acquire.engine import (
    BaseEngine,
    BlueskyEngine,
    Engine,
    EngineState,
    MockEngine,
    PrioritizedProcedure,
    get_engine,
    reset_engine,
    set_engine,
)
from lucid.acquire.signals import (
    SignalConfiguration,
    SignalDefinition,
    SignalKind,
    SignalPreset,
)

# Backward compatibility aliases
QRunEngine = BlueskyEngine


def get_run_engine(**kwargs):
    """Get the global QRunEngine instance.

    Deprecated: Use get_engine() instead.
    """
    return get_engine(**kwargs)


__all__ = [
    # Engine abstraction
    "Engine",
    "EngineState",
    "BaseEngine",
    "BlueskyEngine",
    "MockEngine",
    "PrioritizedProcedure",
    "get_engine",
    "set_engine",
    "reset_engine",
    # Backward compatibility
    "QRunEngine",
    "get_run_engine",
    # Device wrapper
    "NCSDevice",
    "wrap_device",
    # Signals
    "SignalConfiguration",
    "SignalDefinition",
    "SignalKind",
    "SignalPreset",
    # Buffer
    "LiveDataBuffer",
    "MultiStreamBuffer",
]

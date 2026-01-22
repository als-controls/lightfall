"""NCS data acquisition module.

Provides Qt-integrated Bluesky RunEngine for scientific data acquisition.

Components:
- QRunEngine: Qt-wrapped Bluesky RunEngine with signals
- NCSDevice: Generic wrapper adding metadata/policy to ophyd devices
- SignalConfiguration: UI model for signal selection
- LiveDataBuffer: Thread-safe document streaming to Qt
- PlanRegistry: Central registry of available plans
"""

from ncs.acquire.buffer import LiveDataBuffer, MultiStreamBuffer
from ncs.acquire.device_wrapper import NCSDevice, wrap_device
from ncs.acquire.runengine import QRunEngine, get_run_engine
from ncs.acquire.signals import (
    SignalConfiguration,
    SignalDefinition,
    SignalKind,
    SignalPreset,
)

__all__ = [
    # RunEngine
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

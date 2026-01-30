# src/lucid/devices/sim/__init__.py
"""Simulated ophyd devices for testing and development."""

try:
    from lucid.devices.sim.areadetector import SimDetector
    from lucid.devices.sim.plugins import (
        SimCam,
        SimImagePlugin,
        SimROIPlugin,
        SimStatsPlugin,
        SimTransformPlugin,
    )

    __all__ = [
        "SimDetector",
        "SimCam",
        "SimImagePlugin",
        "SimROIPlugin",
        "SimStatsPlugin",
        "SimTransformPlugin",
    ]
except ImportError as e:
    import warnings

    warnings.warn(f"SimDetector components not available: {e}")
    __all__ = []

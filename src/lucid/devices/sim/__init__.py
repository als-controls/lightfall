"""Simulated ophyd devices for testing and development.

This package provides simulated area detector devices that can be used
for testing UI widgets and Bluesky plans without requiring EPICS.

Exports (will be available after all modules are implemented):
- SimDetector: Main simulated area detector device
- SimCam: Simulated camera component
- SimImagePlugin: Simulated image plugin
- SimROIPlugin: Simulated ROI plugin
- SimStatsPlugin: Simulated statistics plugin
- SimTransformPlugin: Simulated transform plugin

Image Generators (available now):
- ImageGenerator: Abstract base class for generators
- StaticPatternGenerator: Gradient, checker, gaussian patterns
- AnimatedPatternGenerator: Sine wave, rotating patterns
- MotorResponsiveGenerator: Patterns that track motor positions
"""

from lucid.devices.sim.generators import (
    AnimatedPatternGenerator,
    ImageGenerator,
    MotorResponsiveGenerator,
    StaticPatternGenerator,
)

# These imports will be added once the modules are implemented:
# from lucid.devices.sim.areadetector import SimDetector
# from lucid.devices.sim.plugins import (
#     SimCam,
#     SimImagePlugin,
#     SimROIPlugin,
#     SimStatsPlugin,
#     SimTransformPlugin,
# )

__all__ = [
    # Generators (available now)
    "ImageGenerator",
    "StaticPatternGenerator",
    "AnimatedPatternGenerator",
    "MotorResponsiveGenerator",
    # Detector and plugins (will be added later)
    # "SimDetector",
    # "SimCam",
    # "SimImagePlugin",
    # "SimROIPlugin",
    # "SimStatsPlugin",
    # "SimTransformPlugin",
]

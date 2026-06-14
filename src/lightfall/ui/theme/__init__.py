"""Theme management for NCS.

This package provides:
- ThemeManager for application-wide theme control
- ThemeRegistry for managing theme plugins
- Beamline-specific theme customization
- Dark/light mode detection and switching
"""

from lightfall.ui.theme.manager import (
    Theme,
    ThemeColors,
    ThemeManager,
    scaled_pt,
    scaled_px,
)
from lightfall.ui.theme.registry import ThemeRegistry

__all__ = [
    "Theme",
    "ThemeColors",
    "ThemeManager",
    "ThemeRegistry",
    "scaled_pt",
    "scaled_px",
]

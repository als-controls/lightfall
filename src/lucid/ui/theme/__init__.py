"""Theme management for NCS.

This package provides:
- ThemeManager for application-wide theme control
- ThemeRegistry for managing theme plugins
- Beamline-specific theme customization
- Dark/light mode detection and switching
"""

from lucid.ui.theme.manager import Theme, ThemeColors, ThemeManager
from lucid.ui.theme.registry import ThemeRegistry

__all__ = [
    "Theme",
    "ThemeColors",
    "ThemeManager",
    "ThemeRegistry",
]

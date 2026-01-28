"""Theme management for NCS.

This package provides:
- ThemeManager for application-wide theme control
- Beamline-specific theme customization
- Dark/light mode detection and switching
"""

from lucid.ui.theme.manager import Theme, ThemeManager

__all__ = [
    "Theme",
    "ThemeManager",
]

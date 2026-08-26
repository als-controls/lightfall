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

# Docking chrome QSS: contributed to the shared ThemeManager. Before the
# extraction to lightfall-utils this was a lazy import inside
# ThemeManager.generate_stylesheet(); class-level registration preserves the
# always-on behavior (including across ThemeManager.reset() in tests).
from lightfall.ui.docking.theme import generate_docking_stylesheet as _generate_docking_stylesheet

if _generate_docking_stylesheet not in ThemeManager.default_stylesheet_contributors:
    ThemeManager.default_stylesheet_contributors.append(_generate_docking_stylesheet)

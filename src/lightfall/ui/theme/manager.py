"""Compatibility shim: the theme manager now lives in lightfall-utils."""

from lightfall_utils.theming.manager import (  # noqa: F401
    DARKBLUE_COLORS,
    LIGHT_COLORS,
    SLATE_COLORS,
    BeamlineTheme,
    Theme,
    ThemeColors,
    ThemeManager,
    scaled_pt,
    scaled_px,
)

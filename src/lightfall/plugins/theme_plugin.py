"""Theme plugin type: lightfall-utils theming wired into the plugin framework."""

from __future__ import annotations

from typing import ClassVar

from lightfall.plugins.types import PluginType
from lightfall_utils.theming import ThemeDefinition, ThemeProvider  # noqa: F401

__all__ = ["ThemeDefinition", "ThemePlugin"]


class ThemePlugin(ThemeProvider, PluginType):
    """Abstract base class for theme plugins (ThemeProvider + plugin framework)."""

    type_name: ClassVar[str] = "theme"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this theme plugin."""
        return "Application theme plugin"

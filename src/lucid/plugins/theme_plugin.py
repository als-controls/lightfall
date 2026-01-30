"""Theme plugin type for application themes.

ThemePlugin defines the interface for theme plugins that provide color
definitions and optional CSS overrides for styling the application.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from lucid.plugins.types import PluginType


@dataclass
class ThemeDefinition:
    """Color and style definitions for a theme.

    Attributes:
        primary: Primary brand/accent color.
        secondary: Secondary accent color.
        success: Success/positive state color.
        warning: Warning state color.
        error: Error/danger state color.
        info: Informational state color.
        background: Main background color.
        surface: Elevated surface color.
        text: Primary text color.
        text_secondary: Secondary/muted text color.
        border: Border/divider color.
        connected: Connected state color (defaults to success).
        disconnected: Disconnected state color (defaults to error).
        css_overrides: Optional CSS rules to append after base stylesheet.
    """

    primary: str = "#2563eb"  # Blue
    secondary: str = "#7c3aed"  # Purple
    success: str = "#16a34a"  # Green
    warning: str = "#d97706"  # Amber
    error: str = "#dc2626"  # Red
    info: str = "#0891b2"  # Cyan

    background: str = "#ffffff"
    surface: str = "#f3f4f6"
    text: str = "#1f2937"
    text_secondary: str = "#6b7280"
    border: str = "#e5e7eb"

    connected: str = ""
    disconnected: str = ""

    css_overrides: str = ""

    def __post_init__(self) -> None:
        """Set default state colors based on theme."""
        if not self.connected:
            self.connected = self.success
        if not self.disconnected:
            self.disconnected = self.error


class ThemePlugin(PluginType):
    """Abstract base class for theme plugins.

    Theme plugins provide color definitions and optional CSS overrides
    for styling the application. Each theme is registered with the
    ThemeRegistry and can be selected in the Appearance preferences.

    Class Attributes:
        type_name: "theme" - identifies this as a theme plugin.
        is_singleton: True - theme plugins are singletons.

    Example implementation::

        class MyThemePlugin(ThemePlugin):
            @property
            def name(self) -> str:
                return "my_theme"

            @property
            def display_name(self) -> str:
                return "My Theme"

            @property
            def is_dark(self) -> bool:
                return True

            def get_theme_definition(self) -> ThemeDefinition:
                return ThemeDefinition(
                    background="#1a1a1a",
                    surface="#2a2a2a",
                    text="#e0e0e0",
                    # ... other colors
                    css_overrides="/* Custom CSS here */",
                )
    """

    type_name: ClassVar[str] = "theme"
    description: ClassVar[str] = "Application theme plugin"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this theme.

        This is used as the internal name for storage and lookup.
        Should be lowercase with no spaces (e.g., "light", "slate", "darkblue").
        """
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in the theme selector.

        This is what users see in the Appearance preferences
        (e.g., "Light", "Slate (Dark)", "Dark Blue").
        """
        ...

    @property
    @abstractmethod
    def is_dark(self) -> bool:
        """Whether this is a dark theme.

        Used for system theme detection - when "System" is selected,
        a dark or light theme is chosen based on the OS setting.
        """
        ...

    @abstractmethod
    def get_theme_definition(self) -> ThemeDefinition:
        """Get the theme's color definitions.

        Returns:
            ThemeDefinition with all color values and optional CSS overrides.
        """
        ...

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with theme plugin information.
        """
        definition = self.get_theme_definition()
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "is_dark": self.is_dark,
            "has_css_overrides": bool(definition.css_overrides),
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

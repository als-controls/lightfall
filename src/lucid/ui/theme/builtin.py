"""Built-in theme plugins for NCS.

This module contains the default themes that ship with NCS:
- LightThemePlugin: Light/bright theme
- SlateThemePlugin: Neutral gray dark theme
- DarkBlueThemePlugin: Blue-gray dark theme
"""

from __future__ import annotations

from lucid.plugins.theme_plugin import ThemeDefinition, ThemePlugin


class LightThemePlugin(ThemePlugin):
    """Light theme plugin.

    A bright theme with white backgrounds and dark text,
    suitable for well-lit environments.
    """

    @property
    def name(self) -> str:
        return "light"

    @property
    def display_name(self) -> str:
        return "Light"

    @property
    def is_dark(self) -> bool:
        return False

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#2563eb",
            secondary="#7c3aed",
            success="#16a34a",
            warning="#d97706",
            error="#dc2626",
            info="#0891b2",
            background="#ffffff",
            surface="#f3f4f6",
            text="#1f2937",
            text_secondary="#6b7280",
            border="#e5e7eb",
            disconnected="#ffcccc",
        )


class SlateThemePlugin(ThemePlugin):
    """Slate (dark) theme plugin.

    A neutral gray dark theme that reduces eye strain in low-light
    environments. This is the default dark theme.
    """

    @property
    def name(self) -> str:
        return "slate"

    @property
    def display_name(self) -> str:
        return "Slate (Dark)"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#3b82f6",
            secondary="#8b5cf6",
            success="#22c55e",
            warning="#f59e0b",
            error="#ef4444",
            info="#06b6d4",
            background="#1e1e1e",
            surface="#2d2d2d",
            text="#d4d4d4",
            text_secondary="#808080",
            border="#3e3e3e",
            disconnected="#5c2020",
        )


class DarkBlueThemePlugin(ThemePlugin):
    """Dark Blue theme plugin.

    A blue-gray dark theme with slightly warmer tones than Slate.
    """

    @property
    def name(self) -> str:
        return "darkblue"

    @property
    def display_name(self) -> str:
        return "Dark Blue"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#3b82f6",
            secondary="#8b5cf6",
            success="#22c55e",
            warning="#f59e0b",
            error="#ef4444",
            info="#06b6d4",
            background="#1f2937",
            surface="#374151",
            text="#f3f4f6",
            text_secondary="#9ca3af",
            border="#4b5563",
            disconnected="#5c2020",
        )

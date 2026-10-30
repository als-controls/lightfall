"""Login and session settings plugin for NCS.

Provides configuration for:
- Session duration for local authentication
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from lightfall_settings import SettingSpec
from loguru import logger

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import Choice, Section, SettingField
from lightfall.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon


# Session duration options in seconds
SESSION_DURATION_OPTIONS = {
    900: "15 minutes",
    3600: "1 hour",
    7200: "2 hours",
    14400: "4 hours",
    28800: "8 hours",
}

# Default session duration (2 hours)
DEFAULT_SESSION_DURATION = 7200


class LoginSettingsProvider:
    """Helper to access session duration from anywhere in the application."""

    @staticmethod
    def get_session_duration() -> timedelta:
        """Get the configured session duration.

        Returns:
            Session duration as a timedelta. Defaults to 2 hours.
        """
        prefs = PreferencesManager.get_instance()
        seconds = prefs.get("session_duration", DEFAULT_SESSION_DURATION)
        logger.debug("Session duration preference: {} seconds", seconds)
        return timedelta(seconds=seconds)


class LoginSettingsPlugin(SettingsPlugin):
    """Settings plugin for login and session configuration.

    Provides the session duration selection for local authentication.

    Note: Keycloak sessions use server-controlled expiry from JWT tokens,
    so the session duration setting only affects local authentication.
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "login"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Login & Session"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "general"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 5  # After appearance (0)

    def specs(self) -> Sequence[SettingSpec]:
        return [
            SettingSpec(key="session_duration", scopes=frozenset({"user", "facility"}),
                        label="Session duration"),
        ]

    def sections(self) -> Sequence[Section]:
        return [
            Section("Session Settings", [
                SettingField(
                    "session_duration",
                    Choice(SESSION_DURATION_OPTIONS.items()),
                    label="Session duration",
                    description=(
                        "This setting applies to local accounts only. "
                        "Keycloak sessions are controlled by the server."
                    ),
                ),
            ]),
        ]

"""Login and session settings plugin for NCS.

Provides configuration for:
- Session duration for local authentication
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
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

    Provides controls for:
    - Session duration selection for local authentication

    Note: Keycloak sessions use server-controlled expiry from JWT tokens,
    so the session duration setting only affects local authentication.
    """

    def __init__(self) -> None:
        """Initialize the login settings plugin."""
        self._widget: QWidget | None = None
        self._duration_combo: QComboBox | None = None

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

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the login settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Session group
        session_group = QGroupBox("Session Settings")
        session_layout = QFormLayout(session_group)

        # Session duration selector
        self._duration_combo = QComboBox()
        for seconds, label in SESSION_DURATION_OPTIONS.items():
            self._duration_combo.addItem(label, seconds)
        session_layout.addRow("Session Duration:", self._duration_combo)

        # Info label
        info_label = QLabel(
            "This setting applies to local accounts only.\n"
            "Keycloak sessions are controlled by the server."
        )
        info_label.setStyleSheet("color: gray; font-size: 10px;")
        info_label.setWordWrap(True)
        session_layout.addRow("", info_label)

        layout.addWidget(session_group)
        layout.addStretch()

        self._widget = widget
        return widget

    def load_settings(self) -> None:
        """Load current settings into the widget."""
        if not self._duration_combo:
            return

        prefs = PreferencesManager.get_instance()
        duration = prefs.get("session_duration", DEFAULT_SESSION_DURATION)

        # Find and select the matching duration
        index = self._duration_combo.findData(duration)
        if index >= 0:
            self._duration_combo.setCurrentIndex(index)
        else:
            # If saved duration isn't in options, use default
            index = self._duration_combo.findData(DEFAULT_SESSION_DURATION)
            if index >= 0:
                self._duration_combo.setCurrentIndex(index)

    def save_settings(self) -> None:
        """Save widget values to persistent storage."""
        if not self._duration_combo:
            return

        prefs = PreferencesManager.get_instance()
        duration = self._duration_combo.currentData()
        prefs.set("session_duration", duration)

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            Empty list (no validation needed for login settings).
        """
        return []

    def apply_preview(self) -> None:
        """Apply settings temporarily for live preview.

        No preview needed for session settings.
        """
        pass

    def revert_preview(self) -> None:
        """Revert preview changes if user cancels.

        No preview to revert for session settings.
        """
        pass

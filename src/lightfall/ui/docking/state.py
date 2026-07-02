"""DockingState - Layout state persistence for the docking system.

Handles saving and restoring dock widget positions, sizes, and
visibility across application sessions using QMainWindow state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QSettings

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow


# State version for migration handling — bumped for QDockWidget migration
STATE_VERSION = 6


class DockingState:
    """Manages docking layout state persistence.

    Uses QMainWindow.saveState()/restoreState() for native dock layout
    persistence including positions, sizes, and floating state.
    """

    STATE_KEY = "state"

    def __init__(self, main_window: QMainWindow) -> None:
        """Initialize the state manager.

        Args:
            main_window: The QMainWindow instance.
        """
        self._main_window = main_window
        self._settings_group = "docking"

    def save(self, settings: QSettings | None = None) -> QByteArray:
        """Save the current docking state.

        Args:
            settings: Optional QSettings to save to.

        Returns:
            The state as a QByteArray.
        """
        state = self._main_window.saveState()

        if settings is not None:
            settings.beginGroup(self._settings_group)
            settings.setValue(self.STATE_KEY, state)
            settings.setValue("version", STATE_VERSION)
            settings.endGroup()
            logger.debug("Saved docking state to settings")

        return state

    def restore(self, settings: QSettings | None = None) -> bool:
        """Restore docking state.

        Args:
            settings: Optional QSettings to restore from.

        Returns:
            True if state was successfully restored.
        """
        if settings is None:
            return False

        settings.beginGroup(self._settings_group)
        state = settings.value(self.STATE_KEY)
        # Read with an explicit type: QSettings can hand a stored int back as a
        # string (notably IniFormat, the native backend on Linux/ws5), and a
        # bare ``"6" != 6`` would treat a valid saved layout as a version
        # mismatch and silently discard it. ``type=int`` coerces (and falls back
        # to the default on a non-numeric value).
        version = settings.value("version", 0, type=int)
        settings.endGroup()

        # Handle version mismatch
        if version != STATE_VERSION:
            logger.info(
                "Docking state version mismatch (saved={}, current={}), "
                "ignoring saved state",
                version,
                STATE_VERSION,
            )
            return False

        if state is None:
            return False

        if isinstance(state, QByteArray):
            success = self._main_window.restoreState(state)
        else:
            success = self._main_window.restoreState(QByteArray(state))

        if success:
            logger.debug("Restored docking state from settings")
        else:
            logger.warning("Failed to restore docking state")

        return success

    def has_saved_state(self, settings: QSettings | None = None) -> bool:
        """Check whether a saved docking state exists in settings.

        Args:
            settings: Optional QSettings to check.

        Returns:
            True if a previously saved state is present.
        """
        if settings is None:
            return False
        return settings.value(f"{self._settings_group}/{self.STATE_KEY}") is not None

    def clear(self, settings: QSettings | None = None) -> None:
        """Clear saved docking state.

        Args:
            settings: Optional QSettings to clear from.
        """
        if settings is not None:
            settings.beginGroup(self._settings_group)
            settings.remove("")  # Remove all keys in group
            settings.endGroup()
            logger.debug("Cleared saved docking state")

"""DockingState - Layout state persistence for the docking system.

Handles saving and restoring dock widget positions, sizes, and
auto-hide states across application sessions.

Uses a single CDockManager for state persistence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QSettings

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6QtAds import CDockManager


# State version for migration handling
# Bumped to 5 for lazy panel loading (deferred instantiation)
STATE_VERSION = 5


class DockingState:
    """Manages docking layout state persistence for single CDockManager.

    Saves and restores:
    - Dock manager state (positions, sizes, sidebar assignments)
    - Tab groupings
    - Floating window positions
    """

    STATE_KEY = "state"

    def __init__(self, dock_manager: CDockManager) -> None:
        """Initialize the state manager for single dock manager.

        Args:
            dock_manager: The CDockManager instance.
        """
        self._dock_manager = dock_manager
        self._settings_group = "docking"

    def save(self, settings: QSettings | None = None) -> QByteArray:
        """Save the current docking state.

        Args:
            settings: Optional QSettings to save to. If provided,
                state is persisted to settings storage.

        Returns:
            The state as a QByteArray.
        """
        state = self._dock_manager.saveState()

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
        version = settings.value("version", 0)
        settings.endGroup()

        # Handle version mismatch - don't restore incompatible state
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

        # Restore state
        if isinstance(state, QByteArray):
            success = self._dock_manager.restoreState(state)
        else:
            success = self._dock_manager.restoreState(QByteArray(state))

        if success:
            logger.debug("Restored docking state from settings")
        else:
            logger.warning("Failed to restore docking state")

        return success

    def restore_from_bytes(self, state: QByteArray) -> bool:
        """Restore state from a QByteArray.

        Args:
            state: The state bytes to restore.

        Returns:
            True if successful.
        """
        return self._dock_manager.restoreState(state)

    def clear(self, settings: QSettings | None = None) -> None:
        """Clear saved docking state.

        Args:
            settings: Optional QSettings to clear from.
        """
        if settings is not None:
            settings.beginGroup(self._settings_group)
            settings.remove(self.STATE_KEY)
            # Also clear old v1 and v2 keys for clean slate
            settings.remove("state")
            settings.remove("left_state")
            settings.remove("right_state")
            settings.remove("splitter_state")
            settings.remove("version")
            settings.endGroup()
            logger.debug("Cleared saved docking state")

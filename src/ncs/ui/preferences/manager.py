"""Preferences manager for NCS user preferences.

Manages user preferences with two storage mechanisms:
- QSettings: For Qt-specific binary state (window geometry, dock layouts)
- ConfigManager: For typed preferences (theme, font size, etc.)

Supports beamline-specific preference overrides where appropriate.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QObject, QSettings, Signal

from ncs.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow

    from ncs.config.manager import ConfigManager


# Preferences that can have beamline-specific overrides
BEAMLINE_SPECIFIC_PREFS = {
    "default_data_dir",
    "panel_layout",
    "plot_defaults",
    "acquisition_defaults",
}

# Preferences that are always global (never beamline-specific)
GLOBAL_ONLY_PREFS = {
    "theme",
    "font_size",
    "font_family",
    "recent_files",
    "show_statusbar",
    "show_toolbar",
}


class PreferencesManager(QObject):
    """
    Manager for NCS user preferences.

    PreferencesManager provides a unified interface for user preferences,
    using:
    - QSettings for binary Qt state (window geometry, dock arrangements)
    - ConfigManager for typed preferences with validation

    Beamline-Specific Preferences:
        Some preferences can be overridden per-beamline:
        - default_data_dir
        - panel_layout
        - plot_defaults

        These are stored as preferences.beamlines.{name}.{key} and fall
        back to preferences.{key} if no beamline override exists.

    Signals:
        preference_changed: Emitted when a preference changes (key, value).

    Example:
        >>> prefs = PreferencesManager.get_instance()
        >>> prefs.set("theme", "dark")
        >>> prefs.get("theme")
        "dark"
        >>> prefs.save_window_state(main_window)
    """

    preference_changed = Signal(str, object)  # key, value

    _instance: PreferencesManager | None = None
    _lock = threading.RLock()

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        beamline: str | None = None,
    ) -> None:
        """Initialize the preferences manager.

        Args:
            config_manager: ConfigManager for typed preferences.
            beamline: Current beamline identifier for beamline-specific prefs.
        """
        super().__init__()
        self._config_manager = config_manager
        self._beamline = beamline

        # QSettings for Qt binary state
        self._settings = QSettings("ALS", "NCS")

        logger.debug("PreferencesManager initialized (beamline={})", beamline)

    @classmethod
    def get_instance(cls) -> PreferencesManager:
        """Get the singleton PreferencesManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.deleteLater()
            cls._instance = None

    def set_config_manager(self, config_manager: ConfigManager) -> None:
        """Set the ConfigManager to use for typed preferences.

        Args:
            config_manager: The ConfigManager instance.
        """
        self._config_manager = config_manager

    def set_beamline(self, beamline: str | None) -> None:
        """Set the current beamline for beamline-specific preferences.

        Args:
            beamline: Beamline identifier or None for global only.
        """
        self._beamline = beamline
        logger.debug("Beamline set to: {}", beamline)

    @property
    def beamline(self) -> str | None:
        """Current beamline identifier."""
        return self._beamline

    # Typed preferences (via ConfigManager)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a preference value.

        For beamline-specific preferences, checks beamline override first.

        Args:
            key: Preference key.
            default: Default value if not set.

        Returns:
            The preference value.
        """
        if self._config_manager is None:
            logger.warning("ConfigManager not set, returning default for {}", key)
            return default

        # Check if this is a beamline-specific preference
        if key in BEAMLINE_SPECIFIC_PREFS and self._beamline:
            beamline_key = f"preferences.beamlines.{self._beamline}.{key}"
            value = self._config_manager.get(beamline_key)
            if value is not None:
                return value

        # Fall back to global preference
        return self._config_manager.get(f"preferences.{key}", default)

    def set(self, key: str, value: Any, *, persist: bool = True) -> None:
        """Set a preference value.

        Args:
            key: Preference key.
            value: Value to set.
            persist: If True, save to user config file.
        """
        if self._config_manager is None:
            logger.warning("ConfigManager not set, cannot set {}", key)
            return

        # Determine the config key
        if key in BEAMLINE_SPECIFIC_PREFS and self._beamline:
            config_key = f"preferences.beamlines.{self._beamline}.{key}"
        else:
            config_key = f"preferences.{key}"

        self._config_manager.set(config_key, value, persist=persist)
        self.preference_changed.emit(key, value)
        logger.debug("Preference set: {} = {}", key, value)

    def remove(self, key: str) -> None:
        """Remove a preference (revert to default).

        Args:
            key: Preference key to remove.
        """
        if self._config_manager is None:
            return

        config_key = f"preferences.{key}"
        self._config_manager.set(config_key, None, persist=True)
        self.preference_changed.emit(key, None)

    # Recent files management

    def get_recent_files(self) -> list[str]:
        """Get list of recent files.

        Returns:
            List of recent file paths.
        """
        return self.get("recent_files", [])

    def add_recent_file(self, path: str | Path) -> None:
        """Add a file to recent files list.

        Args:
            path: File path to add.
        """
        path_str = str(path)
        recent = self.get_recent_files()

        # Remove if already in list
        if path_str in recent:
            recent.remove(path_str)

        # Add to front
        recent.insert(0, path_str)

        # Limit size
        max_recent = self.get("recent_files_limit", 10)
        recent = recent[:max_recent]

        self.set("recent_files", recent)

    def clear_recent_files(self) -> None:
        """Clear the recent files list."""
        self.set("recent_files", [])

    # Qt window state (via QSettings)

    def save_window_state(
        self,
        window: QMainWindow,
        name: str = "mainwindow",
    ) -> None:
        """Save window geometry and state.

        Args:
            window: The window to save state for.
            name: Key name for this window.
        """
        key_prefix = self._get_settings_key(name)

        self._settings.setValue(f"{key_prefix}/geometry", window.saveGeometry())
        self._settings.setValue(f"{key_prefix}/state", window.saveState())
        self._settings.sync()

        logger.debug("Saved window state: {}", name)

    def restore_window_state(
        self,
        window: QMainWindow,
        name: str = "mainwindow",
    ) -> bool:
        """Restore window geometry and state.

        Args:
            window: The window to restore state to.
            name: Key name for this window.

        Returns:
            True if state was restored.
        """
        key_prefix = self._get_settings_key(name)

        geometry = self._settings.value(f"{key_prefix}/geometry")
        state = self._settings.value(f"{key_prefix}/state")

        if geometry is None or state is None:
            return False

        try:
            if isinstance(geometry, QByteArray):
                window.restoreGeometry(geometry)
            if isinstance(state, QByteArray):
                window.restoreState(state)
            logger.debug("Restored window state: {}", name)
            return True
        except Exception as e:
            logger.warning("Failed to restore window state: {}", e)
            return False

    def save_splitter_state(self, splitter: Any, name: str) -> None:
        """Save splitter sizes.

        Args:
            splitter: QSplitter to save.
            name: Key name for this splitter.
        """
        key_prefix = self._get_settings_key(f"splitter/{name}")
        self._settings.setValue(key_prefix, splitter.saveState())
        self._settings.sync()

    def restore_splitter_state(self, splitter: Any, name: str) -> bool:
        """Restore splitter sizes.

        Args:
            splitter: QSplitter to restore.
            name: Key name for this splitter.

        Returns:
            True if state was restored.
        """
        key_prefix = self._get_settings_key(f"splitter/{name}")
        state = self._settings.value(key_prefix)

        if state is None:
            return False

        try:
            if isinstance(state, QByteArray):
                splitter.restoreState(state)
            return True
        except Exception:
            return False

    def save_value(self, key: str, value: Any) -> None:
        """Save a value to QSettings.

        Use for Qt-specific binary data or simple values.

        Args:
            key: Setting key.
            value: Value to save.
        """
        full_key = self._get_settings_key(key)
        self._settings.setValue(full_key, value)
        self._settings.sync()

    def load_value(self, key: str, default: Any = None) -> Any:
        """Load a value from QSettings.

        Args:
            key: Setting key.
            default: Default value if not set.

        Returns:
            The stored value or default.
        """
        full_key = self._get_settings_key(key)
        value = self._settings.value(full_key)
        return value if value is not None else default

    def _get_settings_key(self, key: str) -> str:
        """Get the full QSettings key, with optional beamline namespace.

        Args:
            key: Base key.

        Returns:
            Full key with namespace.
        """
        if self._beamline:
            return f"beamline/{self._beamline}/{key}"
        return f"global/{key}"

    # Convenience methods for common preferences

    @property
    def theme(self) -> str:
        """Get the current theme preference."""
        return self.get("theme", "system")

    @theme.setter
    def theme(self, value: str) -> None:
        """Set the theme preference."""
        self.set("theme", value)

    @property
    def font_size(self) -> int:
        """Get the font size preference."""
        return self.get("font_size", 10)

    @font_size.setter
    def font_size(self, value: int) -> None:
        """Set the font size preference."""
        self.set("font_size", value)

    @property
    def show_statusbar(self) -> bool:
        """Get statusbar visibility preference."""
        return self.get("show_statusbar", True)

    @show_statusbar.setter
    def show_statusbar(self, value: bool) -> None:
        """Set statusbar visibility preference."""
        self.set("show_statusbar", value)

    @property
    def show_toolbar(self) -> bool:
        """Get toolbar visibility preference."""
        return self.get("show_toolbar", True)

    @show_toolbar.setter
    def show_toolbar(self, value: bool) -> None:
        """Set toolbar visibility preference."""
        self.set("show_toolbar", value)

    def get_all_preferences(self) -> dict[str, Any]:
        """Get all preferences as a dictionary.

        Returns:
            Dictionary of all preferences.
        """
        prefs = {}
        all_keys = list(BEAMLINE_SPECIFIC_PREFS) + list(GLOBAL_ONLY_PREFS)

        for key in all_keys:
            prefs[key] = self.get(key)

        return prefs

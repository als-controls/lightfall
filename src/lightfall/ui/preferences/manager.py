"""Preferences manager for NCS user preferences.

Manages user preferences with two storage mechanisms:
- QSettings: For Qt-specific binary state (window geometry, dock layouts)
- ConfigManager: For typed preferences (theme, font size, etc.)

Supports beamline-specific preference overrides where appropriate.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QObject, QSettings, Signal, Slot

from lightfall.ui.preferences.backend import BEAMLINE_SPECIFIC_PREFS
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow

    from lightfall.config.manager import ConfigManager

# Preferences that are always global (never beamline-specific)
GLOBAL_ONLY_PREFS = {
    "theme",
    "font_size",
    "font_family",
    "recent_files",
    "show_statusbar",
    "show_toolbar",
    "engine",
    # Login & Session settings
    "session_duration",
    # Device backend settings
    "device_backend",
    "device_mock_enabled",
    "device_mock_include_noisy",
    "device_bcs_enabled",
    "device_bcs_host",
    "device_bcs_port",
    "device_bcs_beamline",
    "device_bcs_timeout_ms",
    "device_happi_enabled",
    "device_happi_path",
    "device_happi_beamline",
    "device_happi_instantiate",
    # Tiled settings
    "tiled_enabled",
    "tiled_url",
    "tiled_api_key",
    # Claude settings
    "claude_api_key",
    "claude_endpoint",
    "claude_custom_url",
    "claude_model",
    "claude_max_turns",
    "claude_permission_mode",
    "claude_effort",
    "claude_auto_restore",
    "claude_last_session_id",
    # Plugin settings
    "disabled_plugins",
    # Tool/skill settings (AgentPlugin overrides)
    "disabled_tool_plugins",
    "forced_enabled_tool_plugins",
    # External tools settings (for code navigation)
    "code_editor",  # "vscode" or "pycharm"
    "suppress_pycharm_warning",  # bool - permanently dismiss PyCharm protocol warning
    # Proxy settings
    "proxy_enabled",  # bool (default: False) - master toggle for proxy
    "proxy_type",  # str (default: "socks5") - socks5, socks4, or http
    "proxy_host",  # str (default: "localhost") - proxy server host
    "proxy_port",  # int (default: 1080) - proxy server port
    "proxy_auto_detect",  # bool (default: False) - auto-enable for *.lbl.gov URLs
}

# Keys containing any of these substrings carry credentials; their values
# must never reach the logs (the app runs at DEBUG).
_SENSITIVE_KEY_MARKERS = ("api_key", "token", "secret", "password", "dsn")


def _loggable_value(key: str, value: Any) -> Any:
    """Return `value` for logging, redacted when `key` looks sensitive."""
    key_lower = key.lower()
    if any(marker in key_lower for marker in _SENSITIVE_KEY_MARKERS):
        return "<redacted>"
    return value


class _Topic(QObject):
    """Per-key dispatcher for PreferencesManager subscriptions.

    Holds subscribers as weakrefs for bound methods (so they don't pin
    their owner alive past natural lifetime) and as strong refs for
    plain functions / lambdas (since lambdas typically have no other
    caller-held reference). Dispatch happens through a Qt signal so
    cross-thread emits are auto-marshalled to the topic's thread
    (the GUI thread).
    """

    _emit = Signal(object)  # internal — external callers use emit_value()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        # Each entry: (slot_repr, is_weakref)
        #   - is_weakref=True  → slot_repr is weakref.WeakMethod | weakref.ref
        #   - is_weakref=False → slot_repr is the strong callable itself
        self._slots: list[tuple[Any, bool]] = []
        self._emit.connect(self._dispatch)

    # External API ────────────────────────────────────────────────────

    def emit_value(self, value: Any) -> None:
        """Emit `value` to all subscribers (queued if cross-thread)."""
        self._emit.emit(value)

    def add_subscriber(self, slot: Callable[[Any], None]) -> None:
        """Register `slot`. Bound methods are stored weakly; plain
        callables strongly."""
        if hasattr(slot, "__self__") and hasattr(slot, "__func__"):
            self._slots.append((weakref.WeakMethod(slot), True))
        else:
            self._slots.append((slot, False))

    def remove_subscriber(self, slot: Callable[[Any], None]) -> None:
        """Disconnect `slot`. No-op if not subscribed."""
        if hasattr(slot, "__self__") and hasattr(slot, "__func__"):
            target = weakref.WeakMethod(slot)
            for i, (ref, is_weak) in enumerate(self._slots):
                if is_weak and ref == target:
                    del self._slots[i]
                    return
        else:
            for i, (ref, is_weak) in enumerate(self._slots):
                if not is_weak and ref == slot:
                    del self._slots[i]
                    return

    # Internal ────────────────────────────────────────────────────────

    @Slot(object)
    def _dispatch(self, value: Any) -> None:
        dead: list[int] = []
        for i, (ref, is_weak) in enumerate(self._slots):
            if is_weak:
                target = ref()
                if target is None:
                    dead.append(i)
                    continue
            else:
                target = ref
            try:
                target(value)
            except Exception as exc:
                logger.warning(
                    "Preference subscription slot {!r} raised: {}",
                    target, exc,
                )
        for i in reversed(dead):
            del self._slots[i]


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
        - acquisition_defaults
        - device_favorites

        These are stored as preferences.beamlines.{name}.{key} and fall
        back to preferences.{key} if no beamline override exists.

    Example:
        >>> prefs = PreferencesManager.get_instance()
        >>> prefs.set("theme", "dark")
        >>> prefs.get("theme")
        "dark"
        >>> prefs.subscribe("theme", on_theme_changed)
        >>> prefs.save_window_state(main_window)
    """

    _instance: PreferencesManager | None = None
    _lock = threading.RLock()

    # Keys whose owning (user-portable) backend cache miss should fall
    # through to LocalPreferenceBackend, which preserves the beamline-
    # aware default lookup. Used to persist user-specific values server-
    # side while keeping a site/beamline default in YAML that applies
    # until the user saves their own.
    _USER_PORTABLE_WITH_LOCAL_FALLBACK: frozenset[str] = frozenset({
        "device_favorites",
    })

    _MISSING: Any = object()

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        beamline: str | None = None,
    ) -> None:
        """Initialize the preferences manager."""
        super().__init__()
        self._config_manager = config_manager
        self._beamline = beamline

        # Backend multiplex: user-portable backend takes precedence for
        # keys it owns; local backend handles everything else.
        from lightfall.settings.user_settings_client import UserSettingsClient
        from lightfall.ui.preferences.backend import LocalPreferenceBackend
        from lightfall.ui.preferences.user_portable_backend import UserPortableBackend

        self._local = LocalPreferenceBackend(config_manager, beamline)
        self._user_portable = UserPortableBackend(
            UserSettingsClient.get_instance()
        )
        self._backends: tuple = (self._user_portable, self._local)
        self._topics: dict[str, _Topic] = {}
        for b in self._backends:
            b.changed.connect(self._on_backend_changed)

        # QSettings for Qt binary state (unchanged).
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
        """Set the ConfigManager used by the local backend."""
        self._config_manager = config_manager
        # Rebuild the local backend so it uses the new ConfigManager.
        self._local._cm = config_manager

    def set_beamline(self, beamline: str | None) -> None:
        """Set the current beamline for beamline-specific preferences."""
        self._beamline = beamline
        self._local.set_beamline(beamline)
        logger.debug("Beamline set to: {}", beamline)

    @property
    def beamline(self) -> str | None:
        """Current beamline identifier."""
        return self._beamline

    # Typed preferences (via ConfigManager)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a preference value. Dispatches to the owning backend.

        For keys in `_USER_PORTABLE_WITH_LOCAL_FALLBACK`, a user-portable
        cache miss falls through to LocalPreferenceBackend so site or
        beamline defaults still apply when the user has no saved value.
        """
        if key in self._USER_PORTABLE_WITH_LOCAL_FALLBACK:
            value = self._user_portable.get(key, self._MISSING)
            if value is self._MISSING:
                return self._local.get(key, default)
            return value
        return self._backend_for(key).get(key, default)

    def set(self, key: str, value: Any, *, persist: bool = True) -> None:
        """Set a preference value. Dispatches to the owning backend.
        `persist` retained for back-compat; user-portable backend ignores it."""
        self._backend_for(key).set(key, value)
        logger.debug("Preference set: {} = {}", key, _loggable_value(key, value))

    def remove(self, key: str) -> None:
        """Remove a preference. Dispatches to the owning backend."""
        self._backend_for(key).remove(key)

    def subscribe(self, key: str, slot: Callable[[Any], None]) -> None:
        """Subscribe `slot(value)` to changes for this specific `key` only.

        Bound-method slots are held with a weakref — the subscription
        auto-clears when the owner is garbage-collected. Plain functions
        / lambdas are held with a strong reference (callers must keep
        their own ref if they need to unsubscribe later).

        Call from the GUI thread; slot is invoked on the GUI thread."""
        topic = self._topics.get(key)
        if topic is None:
            topic = _Topic(self)
            self._topics[key] = topic
        topic.add_subscriber(slot)

    def unsubscribe(self, key: str, slot: Callable[[Any], None]) -> None:
        """Disconnect a previously-subscribed slot. No-op if not subscribed."""
        topic = self._topics.get(key)
        if topic is not None:
            topic.remove_subscriber(slot)

    def refresh_user_portable_keys(self) -> None:
        """Trigger an async pull of all user-portable keys from the backend.
        Subscribed slots fire for each key whose value moved."""
        self._user_portable.refresh()

    def _backend_for(self, key: str):
        """Pick the backend that owns `key` (user-portable wins)."""
        for b in self._backends:
            if b.owns(key):
                return b
        return self._local

    @Slot(str, object)
    def _on_backend_changed(self, key: str, value: Any) -> None:
        """Route a backend's per-key change to the matching topic.

        For keys with a local fallback, a None from the user-portable
        backend (cache removal / refresh-saw-key-gone) is rewritten to
        the local fallback so subscribers always see the logical value.
        """
        topic = self._topics.get(key)
        if topic is None:
            return
        if value is None and key in self._USER_PORTABLE_WITH_LOCAL_FALLBACK:
            value = self._local.get(key, None)
        topic.emit_value(value)

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

    @property
    def engine(self) -> str:
        """Get the selected engine preference."""
        return self.get("engine", "bluesky")

    @engine.setter
    def engine(self, value: str) -> None:
        """Set the engine preference."""
        self.set("engine", value)

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

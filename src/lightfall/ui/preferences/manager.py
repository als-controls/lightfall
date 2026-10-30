"""Preferences manager for NCS user preferences.

Manages user preferences with two storage mechanisms:
- QSettings: For Qt-specific binary state (window geometry, dock layouts)
- ConfigManager: For typed preferences (theme, font size, etc.)

Supports beamline-specific preference overrides where appropriate.
"""

from __future__ import annotations

import os
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
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
    "device_backends",
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
    # Caproxy settings (attested lease UX)
    "caproxy_url",
    "caproxy_token",
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
    "agy_last_session_id",
    # Plugin settings
    "disabled_plugins",
    # Tool/skill settings (ToolPlugin overrides)
    "disabled_tool_plugins",
    "forced_enabled_tool_plugins",
    # Agent definitions (agents-as-data)
    "disabled_agents",
    "forced_enabled_agents",
    # Monitor settings (MonitorPlugin overrides + advisor + tick)
    "monitor_advisor_enabled",
    "monitor_tick_interval",
    "disabled_monitor_plugins",
    "forced_enabled_monitor_plugins",
    "disabled_monitor_feeds",
    "monitor_feed_intervals",
    "monitor_feed_advisor_severity",
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


@dataclass(frozen=True)
class ScopeInfo:
    """Which scope chain preferences resolve against."""

    mode: str  # "server" | "local" | "off"
    chain: tuple[str, ...] = ()
    roles: dict[str, str | None] = field(default_factory=dict)


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

    scopes_changed = Signal(object)  # ScopeInfo

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
        self._scoped: Any = None
        self._local_scopes: Any = None
        self._scope_info = ScopeInfo("off")
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
        self.load_local_scopes()

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

        Identity keys (tiled_beamline, ipc_topic_prefix) fall back to values derived
        from the machine's site.bind when nothing sets them; blank or removed (None)
        counts as unset for those keys.
        """
        value = self._get_raw(key, self._MISSING)
        from lightfall.scopes.identity import DERIVED_KEYS

        if key in DERIVED_KEYS and (value is self._MISSING or value in ("", None)):
            derived = self.derived_value(key)
            if derived is not None:
                return derived
        if value is self._MISSING or value is None:
            # Unset typed preferences persist as ``null`` (pydantic field
            # defaults), so a stored None means "no value", not "the value
            # None" — the caller's default must win.
            return default
        return value

    def _site(self) -> Any:
        try:
            return self._config_manager.model.site  # type: ignore[union-attr]
        except Exception:
            return None

    def _get_raw(self, key: str, default: Any = None) -> Any:
        """Get a preference value. Dispatches to the owning backend.

        While scoped settings are active, every non-local key resolves through the
        scope chain. For keys in `_USER_PORTABLE_WITH_LOCAL_FALLBACK`, a user-portable
        cache miss falls through to LocalPreferenceBackend so site or beamline
        defaults still apply when the user has no saved value.
        """
        if self._scoped is not None and self._scoped.owns(key):
            return self._scoped.get(key, default)
        if self._local_scopes is not None and self._local_scopes.owns(key):
            return self._local_scopes.get(key, default)
        if key in self._USER_PORTABLE_WITH_LOCAL_FALLBACK:
            value = self._user_portable.get(key, self._MISSING)
            if value is self._MISSING:
                return self._local.get(key, default)
            return value
        return self._backend_for(key).get(key, default)

    def set(self, key: str, value: Any, *, persist: bool = True, scope: str | None = None) -> None:
        """Set a preference value. Dispatches to the owning backend.

        `scope` (e.g. "branch:7.0.1.1") writes at that scope instead of the user's;
        it requires active scoped settings. `persist` is retained for back-compat.
        """
        backend = self._backend_for(key)
        if scope == "local" and backend is not self._scoped:
            backend.set(key, value)  # no scopes active: every write is this console's
        elif scope is not None:
            if backend is not self._scoped:
                raise ValueError(f"Cannot write {key!r} at {scope}: scoped settings are not active")
            backend.set(key, value, scope=scope)
        else:
            backend.set(key, value)
        logger.debug("Preference set: {} = {}", key, _loggable_value(key, value))

    def remove(self, key: str, *, scope: str | None = None) -> None:
        """Remove a preference (at `scope` when given). Dispatches to the owning backend."""
        backend = self._backend_for(key)
        if scope == "local" and backend is not self._scoped:
            backend.remove(key)
        elif scope is not None:
            if backend is not self._scoped:
                raise ValueError(f"Cannot reset {key!r} at {scope}: scoped settings are not active")
            backend.remove(key, scope=scope)
        else:
            backend.remove(key)

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
        """Pick the backend that owns `key` (scoped, then user-portable, then local)."""
        if self._scoped is not None and self._scoped.owns(key):
            return self._scoped
        if self._local_scopes is not None and self._local_scopes.owns(key):
            return self._local_scopes
        for b in self._backends:
            if b.owns(key):
                return b
        return self._local

    @property
    def scoped_backend(self) -> Any:
        return self._scoped

    def _active_scoped(self) -> Any:
        scoped = self._scoped
        return scoped if scoped is not None and scoped.active else None

    def write_targets(self, key: str) -> list[str]:
        """Where `key` may be written, most specific first ("local" = this console)."""
        scoped = self._active_scoped()
        return scoped.targets(key) if scoped is not None else ["local"]

    def item_origins(self, key: str) -> dict[str, str]:
        """For a union key: item id -> the scope it comes from ("local" = this console)."""
        scoped = self._active_scoped()
        if scoped is not None:
            return scoped.item_origins(key)
        value = self.get(key, None)
        return dict.fromkeys(value, "local") if isinstance(value, dict) else {}

    def layer_value(self, key: str, scope: str) -> Any:
        """The value stored at one layer, or None."""
        scoped = self._active_scoped()
        if scoped is not None:
            from lightfall.scopes.store import MISSING

            value = scoped.layer_value(key, scope)
            return None if value is MISSING else value
        return self.get(key, None) if scope == "local" else None

    def _user_preferences(self) -> dict[str, Any]:
        cm = self._config_manager
        getter = getattr(cm, "user_preferences", None)
        return getter() if callable(getter) else {}

    def activate_scopes(self, identity: str, bind: str) -> bool:
        """Resolve non-local preferences through the scope chain for `identity`.

        Returns False (and stays local) when disabled or lightfall-settings is missing.
        """
        if os.environ.get("LIGHTFALL_SCOPES_DISABLED") == "1":
            return False
        from lightfall.scopes import scopes_available

        if not scopes_available():
            return False
        if self._scoped is not None and self._scoped.serves(identity, bind):
            self._scoped.refresh()  # same user again (e.g. re-login): no teardown flicker
            return True
        self.deactivate_scopes()
        from lightfall.ui.preferences import scoped_backend as sb

        backend = sb.build_scoped_backend(self._local, identity=identity, bind=bind,
                                          user_preferences=self._user_preferences)
        backend.changed.connect(self._on_backend_changed)
        backend.chain_changed.connect(self._on_scoped_chain)
        backend.set_fallback(self._pre_scope_get)
        self._scoped = backend
        backend.activate()
        logger.info("Scoped preferences active for {} at {}", identity, bind)
        return True

    def deactivate_scopes(self) -> None:
        backend = self._scoped
        if backend is None:
            return
        keys = set(backend.store.keys()) if backend.store is not None else set()
        if self._local_scopes is not None:
            keys |= self._local_scopes.keys()
        before = {k: self.get(k) for k in keys}
        # The manager reports the net change itself: one emission per key that moved.
        backend.changed.disconnect(self._on_backend_changed)
        backend.chain_changed.disconnect(self._on_scoped_chain)
        self._scoped = None
        backend.deactivate()
        backend.deleteLater()
        for key in sorted(keys):
            after = self.get(key)
            if after != before[key]:
                self._on_backend_changed(key, after)
        self._set_scope_info(self._local_or_off())

    def _pre_scope_get(self, key: str, default: Any = None) -> Any:
        """What a key reads as without server scopes: local scope files, then the YAML."""
        if self._local_scopes is not None and self._local_scopes.owns(key):
            return self._local_scopes.get(key, default)
        return self._local.get(key, default)

    def explicit(self, key: str, default: Any = None) -> Any:
        """The stored value, without identity derivation (for settings pages)."""
        value = self._get_raw(key, self._MISSING)
        return default if value is self._MISSING or value == "" else value

    def derived_value(self, key: str) -> str | None:
        """What ``key`` derives to from site.bind, or None."""
        from lightfall.scopes.identity import derived_default

        site = self._site()
        return derived_default(key, getattr(site, "bind", None),
                               getattr(site, "facility", "") or "")

    def set_unless_derived(self, key: str, value: str) -> None:
        """Save a settings-page field; blank or the derived value means "use the default"
        and is not stored, so a console's identity never follows the user elsewhere."""
        derived = self.derived_value(key)
        if derived is not None and value in ("", derived):
            self.remove(key)
        else:
            self.set(key, value)

    @property
    def scope_info(self) -> ScopeInfo:
        return self._scope_info

    def _set_scope_info(self, info: ScopeInfo) -> None:
        if info != self._scope_info:
            self._scope_info = info
            self.scopes_changed.emit(info)

    def _on_scoped_chain(self, chain_info: Any) -> None:
        self._set_scope_info(ScopeInfo("server", tuple(chain_info.chain), dict(chain_info.roles)))

    def _local_or_off(self) -> ScopeInfo:
        if self._local_scopes is not None:
            return ScopeInfo("local", self._local_scopes.chain)
        return ScopeInfo("off")

    def load_local_scopes(self, directory: Path | None = None) -> bool:
        loaded = self._load_local_scopes(directory)
        if self._scoped is None:
            self._set_scope_info(self._local_or_off())
        return loaded

    def _load_local_scopes(self, directory: Path | None = None) -> bool:
        """Serve read-only facility/beamline/branch files while server scopes are off."""
        if self._local_scopes is not None:
            self._local_scopes.changed.disconnect(self._on_backend_changed)
            self._local_scopes = None
        if os.environ.get("LIGHTFALL_SCOPES_DISABLED") == "1":
            return False
        from lightfall.scopes import scopes_available

        if not scopes_available():
            return False
        from lightfall.scopes.local_files import load_local_state, local_scopes_dir
        from lightfall.ui.preferences.local_scopes_backend import LocalScopesBackend

        site = self._site()
        state = load_local_state(directory or local_scopes_dir(), getattr(site, "bind", None))
        if state is None:
            return False
        self._local_scopes = LocalScopesBackend(self._local, state)
        self._local_scopes.changed.connect(self._on_backend_changed)
        logger.info("Local scope files active: {}", ", ".join(self._local_scopes.chain))
        return True

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

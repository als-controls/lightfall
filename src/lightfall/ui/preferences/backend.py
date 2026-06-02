"""Preference storage backends used by PreferencesManager.

The ABC declares a small, threading-aware contract; PreferencesManager
multiplexes over concrete backends (LocalPreferenceBackend here,
UserPortableBackend in user_portable_backend.py).
"""
from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.config.manager import ConfigManager


# Beamline-specific preference keys. Single source of truth — manager.py imports this set.
BEAMLINE_SPECIFIC_PREFS: frozenset[str] = frozenset({
    "default_data_dir",
    "panel_layout",
    "plot_defaults",
    "acquisition_defaults",
    "device_favorites",
})


class PreferenceBackend(QObject):
    """Storage backend for `PreferencesManager`.

    Backends own their own threading. `set()`/`remove()` return
    immediately and emit `changed` when the store reflects the new
    value. `get()` is a best-effort synchronous read — backends that
    need I/O may return a cached value or `default`. `refresh()` is an
    optional async pull hook (no-op by default).

    `owns()` is on the hot path (called on every get/set/remove). It
    must be O(1) — precompute any membership structure (set, prefix
    trie, etc.) at __init__ time.

    Note: PySide6's Shiboken metaclass does not honour ABCMeta, so we
    enforce abstractness manually in __init__ rather than using
    abc.ABC as a base.
    """

    changed = Signal(str, object)   # key, value (None on removal)

    # Names of methods that concrete subclasses must override.
    _abstract_method_names: frozenset[str] = frozenset(
        {"owns", "get", "set", "remove"}
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if type(self) is PreferenceBackend:
            raise TypeError(
                "Can't instantiate abstract class PreferenceBackend "
                "with abstract methods: owns, get, set, remove"
            )
        super().__init__(*args, **kwargs)

    @abstractmethod
    def owns(self, key: str) -> bool:
        """True if this backend is the canonical store for `key`.

        Must be O(1) — called on every get/set/remove."""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Return the cached/local value, or `default`."""

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        """Persist `value` for `key`; emit `changed(key, value)` on success."""

    @abstractmethod
    def remove(self, key: str) -> None:
        """Remove `key`; emit `changed(key, None)` on success."""

    def refresh(self) -> None:
        """Optional: re-pull known keys, emitting `changed` per moved key.
        No-op by default."""
        return None


class LocalPreferenceBackend(PreferenceBackend):
    """YAML-backed preferences via ConfigManager.

    Owns every key that is NOT in USER_PORTABLE_KEYS. Beamline-specific
    keys are looked up under preferences.beamlines.{beamline}.{key}
    with a fallback to preferences.{key}.
    """

    def __init__(
        self,
        config_manager: "ConfigManager | None" = None,
        beamline: str | None = None,
    ) -> None:
        super().__init__()
        self._cm = config_manager
        self._beamline = beamline
        # Cache the user-portable set for O(1) `owns()` rejection.
        # Imported lazily to avoid a circular import with
        # user_portable_backend (which itself imports nothing from here).
        from lucid.ui.preferences.user_portable_backend import (
            USER_PORTABLE_KEYS,
        )
        self._user_portable_keys = USER_PORTABLE_KEYS

    def set_beamline(self, beamline: str | None) -> None:
        self._beamline = beamline

    def owns(self, key: str) -> bool:
        return key not in self._user_portable_keys

    def get(self, key: str, default: Any = None) -> Any:
        if self._cm is None:
            logger.warning("ConfigManager not set, returning default for {}", key)
            return default
        if key in BEAMLINE_SPECIFIC_PREFS and self._beamline:
            override = self._cm.get(
                f"preferences.beamlines.{self._beamline}.{key}"
            )
            if override is not None:
                return override
        return self._cm.get(f"preferences.{key}", default)

    def set(self, key: str, value: Any) -> None:
        if self._cm is None:
            logger.warning("ConfigManager not set, cannot set {}", key)
            return
        if key in BEAMLINE_SPECIFIC_PREFS and self._beamline:
            config_key = f"preferences.beamlines.{self._beamline}.{key}"
        else:
            config_key = f"preferences.{key}"
        self._cm.set(config_key, value, persist=True)
        self.changed.emit(key, value)

    def remove(self, key: str) -> None:
        if self._cm is None:
            return
        if key in BEAMLINE_SPECIFIC_PREFS and self._beamline:
            config_key = f"preferences.beamlines.{self._beamline}.{key}"
        else:
            config_key = f"preferences.{key}"
        self._cm.set(config_key, None, persist=True)
        self.changed.emit(key, None)

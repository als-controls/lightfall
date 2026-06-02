"""User-portable preference backend (lucid-logbook via UserSettingsClient).

Owns the set of keys whose canonical store is the user's logbook
account (so they follow the user across machines). All I/O is async:
set()/remove()/refresh() return immediately, do their work on a
QThreadFuture, and emit `changed` from the GUI thread on success.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lucid.ui.preferences.backend import PreferenceBackend
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture

if TYPE_CHECKING:
    from lucid.settings.user_settings_client import UserSettingsClient


USER_PORTABLE_KEYS: frozenset[str] = frozenset({
    "profile_image_id",
    # device_favorites is also listed in BEAMLINE_SPECIFIC_PREFS — that
    # entry governs the LocalPreferenceBackend's beamline-aware lookup,
    # which PreferencesManager.get falls back to when the user has no
    # server-side value (see manager._USER_PORTABLE_WITH_LOCAL_FALLBACK).
    "device_favorites",
})


class UserPortableBackend(PreferenceBackend):
    """Caches user-portable keys locally; round-trips writes via HTTP."""

    def __init__(self, client: "UserSettingsClient") -> None:
        super().__init__()
        self._client = client
        self._keys = USER_PORTABLE_KEYS
        self._cache: dict[str, Any] = {}
        # Keys for which a set/remove is in flight. refresh() skips
        # these so a slow server response can't clobber a recent write.
        self._inflight: set[str] = set()

    # ── Hot-path ────────────────────────────────────────────────────

    def owns(self, key: str) -> bool:
        return key in self._keys

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    # ── Mutations ───────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        self._inflight.add(key)

        def work():
            self._client.set(key, value)
            return value

        f = QThreadFuture(
            work,
            callback_slot=lambda v: self._on_set_ok(key, v),
            except_slot=lambda e: self._on_set_err(key, e),
        )
        f.start()

    def remove(self, key: str) -> None:
        self._inflight.add(key)

        def work():
            self._client.delete(key)
            return None

        f = QThreadFuture(
            work,
            callback_slot=lambda _v: self._on_remove_ok(key),
            except_slot=lambda e: self._on_remove_err(key, e),
        )
        f.start()

    def refresh(self) -> None:
        def work():
            return self._client.get_all()

        f = QThreadFuture(
            work,
            callback_slot=self._on_refresh_ok,
            except_slot=self._on_refresh_err,
        )
        f.start()

    # ── Callbacks (GUI thread) ──────────────────────────────────────

    def _on_set_ok(self, key: str, value: Any) -> None:
        self._inflight.discard(key)
        self._cache[key] = value
        self.changed.emit(key, value)

    def _on_set_err(self, key: str, exc: BaseException) -> None:
        self._inflight.discard(key)
        logger.warning("UserPortableBackend.set({!r}) failed: {}", key, exc)

    def _on_remove_ok(self, key: str) -> None:
        self._inflight.discard(key)
        self._cache.pop(key, None)
        self.changed.emit(key, None)

    def _on_remove_err(self, key: str, exc: BaseException) -> None:
        self._inflight.discard(key)
        logger.warning("UserPortableBackend.remove({!r}) failed: {}", key, exc)

    def _on_refresh_ok(self, all_settings: dict[str, Any]) -> None:
        # Update only keys this backend owns; emit diff against the cache.
        seen: set[str] = set()
        for key, value in all_settings.items():
            if not self.owns(key):
                continue
            seen.add(key)
            if key in self._inflight:
                continue
            if self._cache.get(key) != value:
                self._cache[key] = value
                self.changed.emit(key, value)
        # Keys we knew about but the server no longer reports → removed.
        for key in list(self._cache):
            if key in seen or key in self._inflight:
                continue
            self._cache.pop(key, None)
            self.changed.emit(key, None)

    def _on_refresh_err(self, exc: BaseException) -> None:
        logger.warning("UserPortableBackend.refresh failed: {}", exc)

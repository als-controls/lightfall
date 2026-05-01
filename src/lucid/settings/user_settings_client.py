"""Sync HTTP client for the lucid-logbook /logbook/settings endpoints.

Used for user-scoped settings that must follow a user across machines
(profile picture, future user-level prefs). Local-only preferences
continue to live in PreferencesManager.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from lucid.auth.httpx_auth import SessionAuth
from lucid.logbook.url import get_logbook_base_url
from lucid.utils.logging import logger


_DEFAULT_TIMEOUT = 10.0


class UserSettingsError(Exception):
    """Raised on non-2xx response or network failure for set/delete."""


class UserSettingsClient:
    """Singleton client for /logbook/settings."""

    _instance: "UserSettingsClient | None" = None
    _lock = threading.Lock()

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = SessionAuth()

    # ── Singleton plumbing ───────────────────────────────────────────────

    @classmethod
    def init(cls, base_url: str | None = None) -> None:
        """Initialize the singleton. base_url=None falls back to
        get_logbook_base_url()."""
        url = base_url or get_logbook_base_url()
        with cls._lock:
            cls._instance = cls(url)
        logger.info("UserSettingsClient initialised (base_url={})", url)

    @classmethod
    def get_instance(cls) -> "UserSettingsClient":
        if cls._instance is None:
            cls.init()  # lazy default-init
        assert cls._instance is not None
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ── Helpers ──────────────────────────────────────────────────────────

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=_DEFAULT_TIMEOUT,
            auth=self._auth,
        )

    @staticmethod
    def _bl(beamline: str | None) -> str:
        return beamline if beamline is not None else ""

    # ── Read API ─────────────────────────────────────────────────────────

    def get(
        self,
        key: str,
        default: Any = None,
        *,
        beamline: str | None = None,
    ) -> Any:
        """Get a single setting value. Returns default on 404/connection error."""
        try:
            with self._client() as c:
                r = c.get(
                    f"/logbook/settings/{key}",
                    params={"beamline": self._bl(beamline)},
                )
            if r.status_code == 404:
                return default
            r.raise_for_status()
            return r.json()["value"]
        except (httpx.HTTPError, KeyError) as e:
            logger.debug("UserSettingsClient.get({!r}) failed: {}", key, e)
            return default

    def get_all(self, *, beamline: str | None = None) -> dict[str, Any]:
        """Return {key: value, ...} for the current user in this scope.

        Returns empty dict on connection error (graceful degradation)."""
        try:
            with self._client() as c:
                r = c.get(
                    "/logbook/settings",
                    params={"beamline": self._bl(beamline)},
                )
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            logger.debug("UserSettingsClient.get_all failed: {}", e)
            return {}

    # ── Write API ────────────────────────────────────────────────────────

    def set(
        self,
        key: str,
        value: Any,
        *,
        beamline: str | None = None,
    ) -> None:
        """Upsert a setting. Raises UserSettingsError on failure."""
        body = {"value": value, "beamline": self._bl(beamline)}
        try:
            with self._client() as c:
                r = c.put(f"/logbook/settings/{key}", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise UserSettingsError(
                f"Failed to set setting {key!r}: {e}"
            ) from e

    def delete(self, key: str, *, beamline: str | None = None) -> None:
        """Delete a setting. Raises UserSettingsError on failure."""
        try:
            with self._client() as c:
                r = c.delete(
                    f"/logbook/settings/{key}",
                    params={"beamline": self._bl(beamline)},
                )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise UserSettingsError(
                f"Failed to delete setting {key!r}: {e}"
            ) from e

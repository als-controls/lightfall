"""Sync HTTP client for the lucid-logbook /logbook/settings endpoints.

Used for user-scoped settings that must follow a user across machines
(profile picture, future user-level prefs). Local-only preferences
continue to live in PreferencesManager.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from lucid.auth.service_key_auth import ServiceKeyAuth
from lucid.logbook.url import get_logbook_base_url
from lucid.utils.logging import logger

try:
    from socksio.exceptions import SOCKSError as _SOCKSError
except ImportError:  # pragma: no cover
    class _SOCKSError(Exception):  # type: ignore[no-redef]
        """Stand-in so the except clauses still parse if socksio isn't installed."""

# Transport-level errors that httpx/httpcore don't always wrap.
# A wedged SOCKS tunnel raises socksio.SOCKSError ("Malformed reply") straight
# through the call stack instead of as httpx.ProxyError, so we catch both.
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, _SOCKSError)


def _format_transport_error(operation: str, exc: BaseException) -> str:
    if isinstance(exc, (_SOCKSError, httpx.ProxyError)):
        return (
            f"{operation} failed: proxy error ({exc}). "
            f"Check that the SSH tunnel on localhost:1080 is healthy."
        )
    return f"{operation} failed: {exc}"


_DEFAULT_TIMEOUT = 10.0


class UserSettingsError(Exception):
    """Raised on non-2xx response or network failure for set/delete."""


class UserSettingsClient:
    """Singleton client for /logbook/settings."""

    _instance: "UserSettingsClient | None" = None
    _lock = threading.Lock()

    def __init__(self, base_url: str | None = None) -> None:
        # base_url=None means "resolve from prefs at call time". This matters
        # because UserSettingsClient is constructed during PreferencesManager
        # bootstrap (manager.py: UserPortableBackend(UserSettingsClient.get_instance())),
        # so a pref read at __init__ time recurses back through a half-built
        # PreferencesManager and silently falls back to DEFAULT_LOGBOOK_URL.
        self._explicit_base_url: str | None = (
            base_url.rstrip("/") if base_url else None
        )
        self._auth = ServiceKeyAuth("logbook")

    @property
    def _base_url(self) -> str:
        if self._explicit_base_url is not None:
            return self._explicit_base_url
        return get_logbook_base_url().rstrip("/")

    # ── Singleton plumbing ───────────────────────────────────────────────

    @classmethod
    def init(cls, base_url: str | None = None) -> None:
        """Initialize the singleton. base_url=None defers URL resolution
        to each request, so the user's logbook_url preference is picked up
        even when this client is constructed before PreferencesManager is
        fully initialised."""
        with cls._lock:
            cls._instance = cls(base_url)
        logger.info(
            "UserSettingsClient initialised (base_url={})",
            base_url or "<lazy from prefs>",
        )

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
        client_kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "timeout": _DEFAULT_TIMEOUT,
            "auth": self._auth,
        }
        try:
            from lucid.ui.preferences.proxy_settings import ProxySettingsProvider
            proxy_url = ProxySettingsProvider.should_use_proxy_for_url(self._base_url)
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
                logger.debug("UserSettingsClient using proxy: {}", proxy_url)
        except Exception:
            pass
        return httpx.Client(**client_kwargs)

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
        except (*_TRANSPORT_ERRORS, KeyError) as e:
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
        except _TRANSPORT_ERRORS as e:
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
        except _TRANSPORT_ERRORS as e:
            raise UserSettingsError(
                _format_transport_error(f"Set setting {key!r}", e)
            ) from e

    def delete(self, key: str, *, beamline: str | None = None) -> None:
        """Delete a setting. Idempotent — 404 is treated as success.
        Raises UserSettingsError on any other failure."""
        try:
            with self._client() as c:
                r = c.delete(
                    f"/logbook/settings/{key}",
                    params={"beamline": self._bl(beamline)},
                )
            if r.status_code == 404:
                return  # already gone, treat as success
            r.raise_for_status()
        except _TRANSPORT_ERRORS as e:
            raise UserSettingsError(
                _format_transport_error(f"Delete setting {key!r}", e)
            ) from e

    # ── Image helpers ────────────────────────────────────────────────────

    def upload_image(self, data: bytes, mime_type: str) -> str:
        """POST bytes to /logbook/images, return image_id."""
        try:
            with self._client() as c:
                r = c.post(
                    "/logbook/images",
                    files={"file": ("image", data, mime_type)},
                )
            r.raise_for_status()
            return r.json()["image_id"]
        except KeyError as e:
            raise UserSettingsError(
                f"Image upload failed: malformed response (missing {e})"
            ) from e
        except _TRANSPORT_ERRORS as e:
            raise UserSettingsError(_format_transport_error("Image upload", e)) from e

    def download_image(self, image_id: str) -> tuple[bytes, str]:
        """GET /logbook/images/{id}; return (bytes, content_type).

        Used by clients that want raw image bytes (e.g., a worker thread
        decoding into a QImage)."""
        try:
            with self._client() as c:
                r = c.get(f"/logbook/images/{image_id}")
            r.raise_for_status()
            return r.content, r.headers.get("content-type", "")
        except _TRANSPORT_ERRORS as e:
            raise UserSettingsError(
                _format_transport_error(f"Image download for {image_id!r}", e)
            ) from e

    def image_url(self, image_id: str) -> str:
        """Build the absolute URL for an image (e.g., for QPixmap loaders
        that handle their own auth)."""
        return f"{self._base_url}/logbook/images/{image_id}"

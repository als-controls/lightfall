"""Sync HTTP client for the logbook scoped-settings endpoints (read side)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from lightfall.utils.logging import logger

try:
    from socksio.exceptions import SOCKSError as _SOCKSError
except ImportError:  # pragma: no cover
    class _SOCKSError(Exception):  # type: ignore[no-redef]
        """Stand-in so the except clauses parse without socksio."""

_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, _SOCKSError)
_TIMEOUT = 10.0


class ScopesError(Exception):
    """A scoped-settings request failed. ``status`` is None for transport failures."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ChainInfo:
    chain: tuple[str, ...]
    roles: dict[str, str | None]
    entitled: tuple[str, ...]
    facility_label: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chain"], d["entitled"] = list(self.chain), list(self.entitled)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChainInfo:
        return cls(
            chain=tuple(d["chain"]),
            roles=dict(d["roles"]),
            entitled=tuple(d.get("entitled", ())),
            facility_label=str(d.get("facility_label", "")),
        )


@dataclass(frozen=True)
class Snapshot:
    scope: str
    settings: dict[str, Any] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    locks: dict[str, dict[str, Any]] = field(default_factory=dict)
    etag: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        return cls(
            scope=str(d["scope"]),
            settings=dict(d.get("settings", {})),
            versions=dict(d.get("versions", {})),
            locks=dict(d.get("locks", {})),
            etag=str(d.get("etag", "")),
        )

    @classmethod
    def empty(cls, scope: str) -> Snapshot:
        return cls(scope=scope)


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, dict) and body.get("detail"):
            return str(body["detail"])
    except ValueError:
        pass
    return r.reason_phrase or f"HTTP {r.status_code}"


class ScopesClient:
    def __init__(self, base_url: str | None = None, auth: httpx.Auth | None = None) -> None:
        self._explicit_base_url = base_url.rstrip("/") if base_url else None
        self._auth = auth

    @property
    def base_url(self) -> str:
        if self._explicit_base_url is not None:
            return self._explicit_base_url
        from lightfall.logbook.url import get_logbook_base_url

        return get_logbook_base_url().rstrip("/")

    def _client(self) -> httpx.Client:
        auth = self._auth
        if auth is None:
            from lightfall.auth.service_key_auth import ServiceKeyAuth

            auth = ServiceKeyAuth("logbook")
        kwargs: dict[str, Any] = {"base_url": self.base_url, "timeout": _TIMEOUT, "auth": auth}
        try:
            from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

            proxy = ProxySettingsProvider.should_use_proxy_for_url(self.base_url)
            if proxy:
                kwargs["proxy"] = proxy
        except Exception:
            pass
        return httpx.Client(**kwargs)

    def _get(self, path: str, *, params: dict[str, str] | None = None,
             headers: dict[str, str] | None = None) -> httpx.Response:
        try:
            with self._client() as c:
                return c.get(path, params=params, headers=headers)
        except _TRANSPORT_ERRORS as e:
            logger.debug("Scoped settings GET {} failed: {}", path, e)
            raise ScopesError(f"Logbook server unreachable: {e}") from e

    @staticmethod
    def _raise_for(r: httpx.Response, what: str) -> None:
        if r.status_code >= 400:
            raise ScopesError(f"{what}: {_detail(r)}", status=r.status_code)

    def chain(self, bind: str) -> ChainInfo:
        r = self._get("/logbook/scopes/chain", params={"bind": bind})
        self._raise_for(r, "Scope chain")
        try:
            return ChainInfo.from_dict(r.json())
        except (ValueError, KeyError, TypeError) as e:
            raise ScopesError(f"Scope chain: malformed response ({e})") from e

    def snapshot(self, scope: str, etag: str | None = None) -> Snapshot | None:
        headers = {"If-None-Match": etag} if etag else None
        r = self._get(f"/logbook/scopes/{scope}/settings", headers=headers)
        if r.status_code == 304:
            return None
        self._raise_for(r, f"Settings for {scope}")
        try:
            return Snapshot.from_dict(r.json())
        except (ValueError, KeyError, TypeError) as e:
            raise ScopesError(f"Settings for {scope}: malformed response ({e})") from e

    def setting_keys(self) -> list[dict[str, Any]] | None:
        r = self._get("/logbook/setting-keys/")
        if r.status_code == 403:
            return None
        self._raise_for(r, "Setting keys")
        try:
            body = r.json()
        except ValueError as e:
            raise ScopesError(f"Setting keys: malformed response ({e})") from e
        if not isinstance(body, list):
            raise ScopesError("Setting keys: malformed response")
        return [rec for rec in body if isinstance(rec, dict) and "key" in rec]

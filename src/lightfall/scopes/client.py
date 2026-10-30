"""Sync HTTP client for the logbook scoped-settings endpoints (read side)."""
from __future__ import annotations

import json
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
    """A scoped-settings request failed. ``status`` is None for transport failures;
    ``code`` is the server's machine-readable reason (e.g. ``key_pending``)."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None,
                 key_status: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        # For key_pending: the key's proposal status ("pending", "rejected", "unproposed").
        self.key_status = key_status


# Statuses that say "try again later", not "this write is wrong": transport failures
# (status None), auth not ready, timeouts, rate limits and server-side errors.
_TRANSIENT = frozenset({401, 408, 429})


def is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status", 0)
    return status is None or status in _TRANSIENT or (isinstance(status, int) and status >= 500)


def _extra(r: httpx.Response, field: str) -> str | None:
    try:
        body = r.json()
    except ValueError:
        return None
    extra = body.get("extra") if isinstance(body, dict) else None
    value = extra.get(field) if isinstance(extra, dict) else None
    return str(value) if value else None


def _code(r: httpx.Response) -> str | None:
    return _extra(r, "code")


@dataclass(frozen=True)
class TreeEntry:
    """One beamline or branch of the server's scope tree, with the caller's role."""

    scope: str
    parent: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class ChainInfo:
    chain: tuple[str, ...]
    roles: dict[str, str | None]
    entitled: tuple[str, ...]
    facility_label: str
    tree: tuple[TreeEntry, ...] = ()  # empty from servers before the binding picker

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chain"], d["entitled"] = list(self.chain), list(self.entitled)
        d["tree"] = [asdict(e) for e in self.tree]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChainInfo:
        return cls(
            chain=tuple(d["chain"]),
            roles=dict(d["roles"]),
            entitled=tuple(d.get("entitled", ())),
            facility_label=str(d.get("facility_label", "")),
            tree=tuple(
                TreeEntry(scope=str(e["scope"]), parent=e.get("parent"), role=e.get("role"))
                for e in d.get("tree") or () if isinstance(e, dict) and e.get("scope")
            ),
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
            raise ScopesError(f"{what}: {_detail(r)}", status=r.status_code, code=_code(r),
                              key_status=_extra(r, "status"))

    def _send(self, method: str, path: str, *, json_body: Any = None,
              headers: dict[str, str] | None = None) -> httpx.Response:
        try:
            with self._client() as c:
                return c.request(method, path, json=json_body, headers=headers)
        except _TRANSPORT_ERRORS as e:
            logger.debug("Scoped settings {} {} failed: {}", method, path, e)
            raise ScopesError(f"Logbook server unreachable: {e}") from e

    def put_setting(self, scope: str, key: str, value: Any, *, version: int | None = None,
                    proposed_spec: dict[str, Any] | None = None) -> int:
        headers: dict[str, str] = {}
        if version:
            headers["If-Match"] = str(version)
        if proposed_spec is not None:
            headers["X-Proposed-Spec"] = json.dumps(proposed_spec)
        r = self._send("PUT", f"/logbook/scopes/{scope}/settings/{key}",
                       json_body={"value": value}, headers=headers or None)
        self._raise_for(r, f"Set {key} at {scope}")
        try:
            return int(r.json()["version"])
        except (ValueError, KeyError, TypeError) as e:
            raise ScopesError(f"Set {key} at {scope}: malformed response ({e})") from e

    def delete_setting(self, scope: str, key: str) -> None:
        r = self._send("DELETE", f"/logbook/scopes/{scope}/settings/{key}")
        if r.status_code == 404:
            return
        self._raise_for(r, f"Reset {key} at {scope}")

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

    # -- setting-key review (facility admins; the server enforces the role) -----------

    def _key_record(self, r: httpx.Response, what: str) -> dict[str, Any]:
        self._raise_for(r, what)
        try:
            body = r.json()
        except ValueError as e:
            raise ScopesError(f"{what}: malformed response ({e})") from e
        if not isinstance(body, dict) or "key" not in body:
            raise ScopesError(f"{what}: malformed response")
        return body

    def approve_key(self, key: str, *, edits: dict[str, Any] | None = None,
                    note: str = "") -> dict[str, Any]:
        """Approve ``key``'s proposal, optionally changing its rule fields."""
        r = self._send("POST", f"/logbook/setting-keys/{key}/approve",
                       json_body={"spec": edits or None, "note": note})
        return self._key_record(r, f"Approve {key}")

    def reject_key(self, key: str, *, note: str = "") -> dict[str, Any]:
        r = self._send("POST", f"/logbook/setting-keys/{key}/reject", json_body={"note": note})
        return self._key_record(r, f"Reject {key}")

    def revoke_key(self, key: str) -> None:
        """Forget ``key``'s record: an approved key falls back to user-only."""
        r = self._send("DELETE", f"/logbook/setting-keys/{key}")
        self._raise_for(r, f"Revoke {key}")

    # -- scope tree (facility admins; the server enforces the role) -------------------

    def add_scope(self, scope: str, *, parent: str | None = None, label: str = "") -> None:
        """Add a beamline, or a branch under ``parent`` (a beamline)."""
        r = self._send("POST", "/logbook/scopes/tree",
                       json_body={"scope": scope, "parent": parent, "label": label})
        self._raise_for(r, f"Add {scope}")

    def remove_scope(self, scope: str) -> None:
        """Remove an empty beamline or branch."""
        r = self._send("DELETE", f"/logbook/scopes/tree/{scope}")
        self._raise_for(r, f"Remove {scope}")

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

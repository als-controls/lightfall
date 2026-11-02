"""Sync HTTP client for the logbook scoped-settings endpoints (read side)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from lightfall.content.labels import describe_source
from lightfall.utils.logging import logger

try:
    from socksio.exceptions import SOCKSError as _SOCKSError
except ImportError:  # pragma: no cover
    class _SOCKSError(Exception):  # type: ignore[no-redef]
        """Stand-in so the except clauses parse without socksio."""

_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, _SOCKSError)
_TIMEOUT = 10.0
_UPLOAD_TIMEOUT = 120.0


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


# ``ScopesError.code`` for a reply that arrived but couldn't be read (status None too):
# unlike a transport failure, the server may have done the write.
MALFORMED_RESPONSE = "malformed_response"


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
class ContentPolicy:
    """The server's limits on a published bundle (defaults match today's server)."""

    max_bundle_bytes: int = 52428800
    max_uncompressed_bytes: int = 209715200
    max_files: int = 2000

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ContentPolicy:
        base = cls()
        if not isinstance(d, dict):
            return base

        def pick(key: str) -> int:
            try:
                v = int(d[key])
            except (KeyError, TypeError, ValueError):
                return getattr(base, key)
            return v if v > 0 else getattr(base, key)

        return cls(pick("max_bundle_bytes"), pick("max_uncompressed_bytes"), pick("max_files"))


@dataclass(frozen=True)
class ChainInfo:
    chain: tuple[str, ...]
    roles: dict[str, str | None]
    entitled: tuple[str, ...]
    facility_label: str
    tree: tuple[TreeEntry, ...] = ()  # empty from servers before the binding picker
    specs: tuple[dict[str, Any], ...] = ()  # the server's approved (non-core) specs
    content_policy: ContentPolicy = ContentPolicy()  # defaults from servers without one

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chain"], d["entitled"] = list(self.chain), list(self.entitled)
        d["tree"] = [asdict(e) for e in self.tree]
        d["specs"] = [dict(s) for s in self.specs]
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
            specs=tuple(s for s in d.get("specs") or () if isinstance(s, dict) and s.get("key")),
            content_policy=ContentPolicy.from_dict(d.get("content_policy")),
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


@dataclass(frozen=True)
class ContentItem:
    """One published item at a scope, at its current revision."""

    type: str
    name: str
    revision: int
    sha256: str
    size: int
    published_by: str = ""
    published_at: str = ""
    author: str | None = None
    file_count: int | None = None  # declared by the publisher; None from older servers
    uncompressed_size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContentItem:
        def opt_int(v: Any) -> int | None:
            return None if v is None else int(v)

        return cls(type=str(d["type"]), name=str(d["name"]), revision=int(d["revision"]),
                   sha256=str(d["sha256"]), size=int(d["size"]),
                   published_by=str(d.get("published_by") or ""),
                   published_at=str(d.get("published_at") or ""),
                   author=None if d.get("author") is None else str(d["author"]),
                   file_count=opt_int(d.get("file_count")),
                   uncompressed_size=opt_int(d.get("uncompressed_size")))



@dataclass(frozen=True)
class ContentListing:
    scope: str
    items: tuple[ContentItem, ...] = ()
    etag: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "items": [i.to_dict() for i in self.items],
                "etag": self.etag}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContentListing:
        items = []
        for raw in d.get("items") or ():
            try:
                items.append(ContentItem.from_dict(raw))
            except (KeyError, TypeError, ValueError, AttributeError):
                logger.warning("Ignoring malformed content item at {}: {!r}", d.get("scope"), raw)
        return cls(scope=str(d["scope"]), items=tuple(items), etag=str(d.get("etag") or ""))


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

    def _client(self, timeout: float = _TIMEOUT) -> httpx.Client:
        auth = self._auth
        if auth is None:
            from lightfall.auth.service_key_auth import ServiceKeyAuth

            auth = ServiceKeyAuth("logbook")
        kwargs: dict[str, Any] = {"base_url": self.base_url, "timeout": timeout, "auth": auth}
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

    # -- scoped content (read side) --------------------------------------------------

    def content_listing(self, scope: str, etag: str | None = None) -> ContentListing | None:
        """``scope``'s current content items; None when unchanged since ``etag``."""
        headers = {"If-None-Match": etag} if etag else None
        r = self._get(f"/logbook/scopes/{scope}/content/", headers=headers)
        if r.status_code == 304:
            return None
        self._raise_for(r, f"Content for {scope}")
        try:
            return ContentListing.from_dict(r.json())
        except (ValueError, KeyError, TypeError) as e:
            raise ScopesError(f"Content for {scope}: malformed response ({e})") from e

    def retracted_content(self, scope: str) -> list[ContentItem] | None:
        """``scope``'s retracted items, each at its last revision.

        None when the server doesn't list retracted items: an older server ignores
        ``include_retracted`` and its reply lacks the top-level ``include_retracted: true``
        (without that marker an empty list can't mean "nothing retracted").
        """
        what = f"Retracted content for {scope}"
        r = self._get(f"/logbook/scopes/{scope}/content/", params={"include_retracted": "1"})
        self._raise_for(r, what)
        try:
            body = r.json()
            if body.get("include_retracted") is not True:
                return None
            raws = [raw for raw in body.get("items") or [] if isinstance(raw, dict)]
        except (ValueError, AttributeError, TypeError) as e:
            raise ScopesError(f"{what}: malformed response ({e})",
                              code=MALFORMED_RESPONSE) from e
        items = []
        for raw in raws:
            if raw.get("retracted") is not True:
                continue
            try:
                items.append(ContentItem.from_dict(raw))
            except (KeyError, TypeError, ValueError, AttributeError):
                logger.warning("Ignoring malformed retracted item at {}: {!r}", scope, raw)
        return items

    def download_bundle(self, scope: str, type_: str, name: str, revision: int, dest: Path,
                        *, expected_size: int) -> str:
        """Stream one revision's zip into ``dest``; returns the sha256 of what was written.

        Stops with ScopesError as soon as more than ``expected_size`` bytes arrive.
        """
        what = f"{type_} {name} at {scope}"
        path = f"/logbook/scopes/{scope}/content/{type_}/{name}/bundle"
        digest = hashlib.sha256()
        written = 0
        try:
            with self._client() as c, c.stream("GET", path,
                                               params={"revision": str(revision)}) as r:
                if r.status_code >= 400:
                    r.read()
                    self._raise_for(r, f"Download {what}")
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes():
                        written += len(chunk)
                        if written > expected_size:
                            raise ScopesError(f"Download {what}: larger than the listed "
                                              f"{expected_size} bytes")
                        digest.update(chunk)
                        f.write(chunk)
        except _TRANSPORT_ERRORS as e:
            logger.debug("Content download {} failed: {}", path, e)
            raise ScopesError(f"Logbook server unreachable: {e}") from e
        return digest.hexdigest()

    # -- scoped content (write side) -------------------------------------------------

    def _content_path(self, scope: str, type_: str, name: str) -> str:
        return f"/logbook/scopes/{scope}/content/{type_}/{name}"

    @staticmethod
    def _agent_headers(agent: bool,
                       expected_revision: int | Literal["none"] | None = None,
                       ) -> dict[str, str] | None:
        """Headers for a write: the agent flag, and ``If-Match`` when the caller says which
        revision it expects to be current (``"none"``: it expects the item retracted)."""
        headers = {"X-Lightfall-Agent": "1"} if agent else {}
        if expected_revision is not None:
            headers["If-Match"] = ("none" if expected_revision == "none"
                                   else str(int(expected_revision)))
        return headers or None

    def _record(self, r: httpx.Response, what: str) -> dict[str, Any]:
        self._raise_for(r, what)
        try:
            body = r.json()
        except ValueError as e:
            raise ScopesError(f"{what}: malformed response ({e})",
                              code=MALFORMED_RESPONSE) from e
        if not isinstance(body, dict):
            raise ScopesError(f"{what}: malformed response", code=MALFORMED_RESPONSE)
        return body

    def publish_content(self, scope: str, type_: str, name: str, manifest: dict[str, Any],
                        bundle: Path, *, agent: bool = False) -> dict[str, Any]:
        """Upload ``bundle`` (a zip) as a new revision; returns the server's record."""
        what = f"Publish {type_} {name} to {scope}"
        path = self._content_path(scope, type_, name)
        try:
            with open(bundle, "rb") as f, self._client(_UPLOAD_TIMEOUT) as c:
                r = c.post(path, data={"manifest": json.dumps(manifest)},
                           files={"bundle": (f"{name}.zip", f, "application/zip")},
                           headers=self._agent_headers(agent))
        except _TRANSPORT_ERRORS as e:
            logger.debug("Content publish {} failed: {}", path, e)
            raise ScopesError(f"Logbook server unreachable: {e}") from e
        return self._record(r, what)

    def retract_content(self, scope: str, type_: str, name: str, *, agent: bool = False,
                        expected_revision: int | None = None) -> dict[str, Any]:
        """Retract the item; with ``expected_revision`` the server refuses (412) unless
        that revision is still current."""
        r = self._send("POST", f"{self._content_path(scope, type_, name)}/retract",
                       headers=self._agent_headers(agent, expected_revision))
        return self._record(r, f"Retract {type_} {name} at {scope}")

    def restore_content(self, scope: str, type_: str, name: str, revision: int, *,
                        agent: bool = False,
                        expected_revision: int | Literal["none"] | None = None,
                        ) -> dict[str, Any]:
        """Make ``revision`` current; with ``expected_revision`` (a revision, or ``"none"``
        for "currently retracted") the server refuses (412) if that's no longer so."""
        r = self._send("POST", f"{self._content_path(scope, type_, name)}/restore",
                       json_body={"revision": revision},
                       headers=self._agent_headers(agent, expected_revision))
        return self._record(r, f"Restore {type_} {name} at {scope}")

    def content_history(self, scope: str, type_: str, name: str) -> list[dict[str, Any]]:
        what = f"History of {type_} {name} at {scope}"
        r = self._get(f"{self._content_path(scope, type_, name)}/history")
        self._raise_for(r, what)
        try:
            body = r.json()
        except ValueError as e:
            raise ScopesError(f"{what}: malformed response ({e})",
                              code=MALFORMED_RESPONSE) from e
        if not isinstance(body, list):
            raise ScopesError(f"{what}: malformed response", code=MALFORMED_RESPONSE)
        return [rec for rec in body if isinstance(rec, dict)]


def describe_error(exc: ScopesError, *, scope: str = "") -> str:
    """A plain-language reason for a failed content write."""
    if exc.status == 403:
        return f"You're not a maintainer of {describe_source(scope)}."
    if exc.status == 409:
        return "Someone changed it at the same time — try again."
    if exc.status == 412:
        return "Someone changed it while you were deciding — look again."
    if exc.status == 413:
        return str(exc)
    if exc.status == 422:
        return f"The server refused it: {exc}"
    if exc.status is None and exc.code == MALFORMED_RESPONSE:
        return "The server's reply couldn't be read; check History before retrying."
    if exc.status is None:
        return "The logbook server is unreachable."
    return str(exc)

# Lightfall Auth v2 Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Lightfall-side Auth v2 plumbing (primitives, SessionManager cache, login mint) and migrate every Tiled consumer + the exporter payload format. End state: Tiled goes through per-(user, service) API keys; logbook still uses bearer (migrated in a later plan); refresh machinery still alive (deleted in a later plan).

**Architecture:** `lightfall.auth.service_key` ships the mint helper (renamed from `job_key`). `lightfall.auth.service_key_auth` ships `ServiceKeyAuth` (cache-backed, in-process) and `StaticApiKeyAuth` (literal-secret, for executor subprocesses). `SessionManager` grows a per-service key cache + `_mint_all_service_keys(bearer)` invoked from `login()`. Every site that currently builds `Authorization: Bearer <session.token>` for Tiled gets switched to `Authorization: Apikey <secret>` via the new auth classes. The exporter's NATS payload renames `auth_token` → `tiled_api_key`.

**Tech Stack:** Python 3.11+, httpx, PySide6 (Qt), pytest + pytest-asyncio, loguru, pydantic v2.

**Spec reference:** [`docs/superpowers/specs/2026-05-17-lightfall-auth-v2-design.md`](../specs/2026-05-17-lightfall-auth-v2-design.md)

**Coordination plan:** [`docs/superpowers/plans/2026-05-17-lightfall-auth-v2-coordination.md`](2026-05-17-lightfall-auth-v2-coordination.md)

**What this plan does NOT do:**

- Delete the refresh machinery in `SessionManager` (deferred to the cleanup plan — logbook still uses the bearer).
- Discard the bearer post-mint (same reason).
- Add the logout-RE-gate dialog (deferred to cleanup plan).
- Migrate `lightfall.logbook.client` — it keeps using `SessionAuth` for now.
- Add the lightfall-logbook server's mint endpoint (separate per-repo plan).
- Pipelines or tsuchinoko payload migration (separate per-repo plans; they depend on the cache this plan adds).

**Test command (from memory `feedback_lightfall_test_command`):**

```bash
.venv/Scripts/python -m pytest <test_path> -v
```

Use this everywhere `pytest` appears below; the bare `pytest` invocation resolves to system Python 3.10, which can't import lightfall.

---

## File structure

| File                                              | Action  | Responsibility                                                                                       |
| ------------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------- |
| `src/lightfall/auth/service_key.py`                   | Create  | Renamed `job_key.py`: `MintedKey` dataclass, `mint_service_key()`, `revoke_service_key()`            |
| `src/lightfall/auth/job_key.py`                       | Delete  | Replaced by `service_key.py`                                                                          |
| `src/lightfall/auth/service_key_auth.py`              | Create  | `ServiceKeyAuth(service_name)` (cache-backed httpx.Auth) + `StaticApiKeyAuth(secret)` (literal)       |
| `src/lightfall/auth/session.py`                       | Modify  | Add `_service_keys` cache, `get_api_key()`, `get_minted_key()`, `_mint_all_service_keys()`; invoke from `login()` |
| `src/lightfall/auth/__init__.py`                      | Modify  | Re-export public names                                                                                |
| `src/lightfall/services/tiled_auth.py`                | Delete  | `KeycloakTiledAuth` replaced by `ServiceKeyAuth("tiled")`                                             |
| `src/lightfall/services/tiled_service.py`             | Modify  | Stop building `Bearer` header; use `ServiceKeyAuth("tiled")`                                          |
| `src/lightfall/services/access_stamper.py`            | Modify  | Read user identity from `session.user.attributes` instead of decoding `session.token`                 |
| `src/lightfall/ui/preferences/tiled_settings.py`      | Modify  | Read groups from `session.user.attributes` (already-broken pattern fixed in passing)                  |
| `src/lightfall/exporter/service.py`                   | Modify  | Rename `auth_token` → `tiled_api_key`                                                                 |
| `src/lightfall/exporter/tiled_utils.py`               | Modify  | Replace `BearerAuth` with `StaticApiKeyAuth`                                                          |
| `src/lightfall/ui/dialogs/export_dialog.py`           | Modify  | Embed Tiled API key from cache, not bearer                                                            |
| `src/lightfall/acquire/plans/adaptive.py`             | Modify  | Same, for tsuchinoko-bound payloads                                                                   |
| `src/lightfall/ipc/service.py`                        | Modify  | Rename `tiled_token` payload field → `tiled_api_key`                                                  |
| `tests/auth/test_service_key.py`                  | Create  | Unit tests for mint/revoke (port from existing `tests/auth/test_job_key.py` if present)               |
| `tests/auth/test_service_key_auth.py`             | Create  | `ServiceKeyAuth` + `StaticApiKeyAuth` unit tests                                                       |
| `tests/auth/test_session_manager_mint.py`         | Create  | `SessionManager._mint_all_service_keys` + cache behavior tests                                         |
| `tests/services/test_tiled_auth_migration.py`     | Create  | Asserts the new auth class is in use; replaces `tests/services/test_tiled_auth.py` if present         |
| `tests/exporter/test_service_payload.py`          | Modify  | Update for `tiled_api_key` field                                                                       |

No major restructure — focused changes against the existing module layout.

---

### Task 1: Rename `lightfall.auth.job_key` → `lightfall.auth.service_key`

**Files:**
- Create: `src/lightfall/auth/service_key.py`
- Delete: `src/lightfall/auth/job_key.py`
- Create: `tests/auth/test_service_key.py`

Per spec: the existing `mint_job_key`/`revoke_job_key` primitives don't change in behavior, just in name and home. Rename happens first so later tasks reference the canonical name.

- [ ] **Step 1: Check for existing job_key tests so they can be renamed too**

Run: `ls tests/auth/ | grep -i job_key`

If `tests/auth/test_job_key.py` exists, plan to delete it after Task 1 Step 7. If not, skip the file but keep the new test file in Step 6.

- [ ] **Step 2: Create the new `src/lightfall/auth/service_key.py`**

Copy `src/lightfall/auth/job_key.py` to `src/lightfall/auth/service_key.py`, then edit it in place to:

1. Rename `MintedJobKey` → `MintedKey`.
2. Rename `mint_job_key` → `mint_service_key`.
3. Rename `revoke_job_key` → `revoke_service_key`.
4. Add an `is_expired` property to `MintedKey`.
5. Update the module docstring.

Final content of `src/lightfall/auth/service_key.py`:

```python
"""Per-service API key minting.

Provides `mint_service_key()` and `revoke_service_key()` — thin wrappers over
the Tiled-shape /api/v1/auth/apikey endpoint contract documented in the auth-v2
spec. Used by SessionManager at login to obtain a per-(user, service) API key
that outlives the Keycloak access token.

Every service implementing the contract (als-tiled today, lightfall-logbook next)
accepts the same request shape and returns the same response shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from loguru import logger


@dataclass(frozen=True)
class MintedKey:
    secret: str
    first_eight: str
    expires_at: datetime | None
    scopes: tuple[str, ...]
    note: str | None

    @property
    def is_expired(self) -> bool:
        """Return True if the key's expiry is in the past.

        A None expiry is treated as "no expiry known" and reports False — the
        server may have omitted the field, and a 401 will catch real expiry on
        next request.
        """
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at


def mint_service_key(
    service_url: str,
    bearer_token: str,
    *,
    expires_in: int,
    scopes: list[str],
    note: str,
    timeout: float = 10.0,
) -> MintedKey:
    """Mint a user-scoped API key for a Lightfall-protected service.

    Args:
        service_url: Base URL of the service's API root
            (e.g. "https://bcgtiled.../api/v1").
        bearer_token: Caller's Keycloak access token. Used only for this one
            call; the server validates it and discards.
        expires_in: TTL in seconds. Maps to the request body's `expires_in`
            field. The auth-v2 spec calls for 604800 (1 week) by default.
        scopes: Scopes to grant. Subset of the caller's scopes; the server
            enforces "may only grant scopes you have."
        note: Free-form audit string surfaced in the service's apikey table /
            JWT claims.
        timeout: httpx timeout in seconds.

    Returns:
        MintedKey with secret and metadata.

    Raises:
        httpx.HTTPStatusError on a 4xx/5xx from the service.
    """
    url = service_url.rstrip("/") + "/auth/apikey"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {bearer_token}"},
        json={"expires_in": expires_in, "scopes": scopes, "note": note},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()

    expires_at: datetime | None = None
    raw_expires = body.get("expiration_time")
    if raw_expires:
        try:
            # ISO-8601 with trailing Z or +00:00; fromisoformat handles both
            # in 3.11+ when Z is replaced.
            expires_at = datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "could not parse expiration_time from mint response: {}",
                raw_expires,
            )

    minted = MintedKey(
        secret=body["secret"],
        first_eight=body["first_eight"],
        expires_at=expires_at,
        scopes=tuple(body.get("scopes", scopes)),
        note=body.get("note"),
    )
    logger.info("minted service key first_eight={} note='{}'", minted.first_eight, minted.note)
    return minted


def revoke_service_key(
    service_url: str,
    bearer_token: str,
    *,
    first_eight: str,
    timeout: float = 10.0,
) -> None:
    """Revoke a previously-minted service key.

    Best-effort: any error talking to the service is logged and swallowed
    (the key's TTL is the backstop). Callers can safely place this in a
    `finally` block without worrying about a transient revoke failure masking
    the original exception.
    """
    url = service_url.rstrip("/") + "/auth/apikey"
    try:
        response = httpx.delete(
            url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={"first_eight": first_eight},
            timeout=timeout,
        )
        response.raise_for_status()
        logger.info("revoked service key first_eight={}", first_eight)
    except (httpx.HTTPError, httpx.HTTPStatusError) as e:
        logger.warning("revoke failed first_eight={} err={}", first_eight, e)
```

- [ ] **Step 3: Delete `src/lightfall/auth/job_key.py`**

Run: `git rm src/lightfall/auth/job_key.py`

- [ ] **Step 4: Update the package's `__init__.py`**

Read `src/lightfall/auth/__init__.py`. If it re-exports `mint_job_key` / `MintedJobKey` / `revoke_job_key`, replace those names with `mint_service_key` / `MintedKey` / `revoke_service_key`. If the module has no `__init__.py` exports for these (job_key may have been imported by-path), skip and the next task's grep catches stragglers.

- [ ] **Step 5: Update any in-repo callers of the old names**

Run: `grep -rn "mint_job_key\|revoke_job_key\|MintedJobKey\|lightfall\.auth\.job_key" src/lightfall/ tests/`

For each match, replace the symbol with its new name. Expected call sites:
- `src/lightfall/pipelines/` (Stage 1 of pipelines code) — replace per the new name.
- `src/lightfall/exporter/` (if any) — replace.
- Test files referencing the old names.

If no callers exist in this repo (the pipelines repo lives elsewhere), the grep returns nothing and you can move on. The remote pipelines repo updates as part of its own plan (coordination plan step 3).

- [ ] **Step 6: Write `tests/auth/test_service_key.py`**

Create the file:

```python
"""Unit tests for lightfall.auth.service_key — mint/revoke against a stub transport."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lightfall.auth.service_key import (
    MintedKey,
    mint_service_key,
    revoke_service_key,
)


def _stub_transport(handler):
    """Wrap an httpx.MockTransport-style handler into a real httpx.Client."""
    return httpx.MockTransport(handler)


def test_mint_service_key_posts_expected_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "secret": "s" * 64,
                "first_eight": "ssssssss",
                "expiration_time": "2026-05-24T20:14:00+00:00",
                "scopes": ["read:metadata", "read:data"],
                "note": "lightfall bcg-ws-3 user123",
            },
        )

    with httpx.Client(transport=_stub_transport(handler)) as client:
        # mint_service_key uses module-level httpx.post; monkeypatch it for the test
        import lightfall.auth.service_key as mod
        original = mod.httpx
        class _Mod:
            def post(self_inner, url, **kwargs):
                return client.post(url, **kwargs)
            def delete(self_inner, url, **kwargs):
                return client.delete(url, **kwargs)
            HTTPError = httpx.HTTPError
            HTTPStatusError = httpx.HTTPStatusError
        mod.httpx = _Mod()
        try:
            minted = mint_service_key(
                "https://example/api/v1",
                "bearer-token-xyz",
                expires_in=604800,
                scopes=["read:metadata", "read:data"],
                note="lightfall bcg-ws-3 user123",
            )
        finally:
            mod.httpx = original

    assert captured["url"] == "https://example/api/v1/auth/apikey"
    assert captured["headers"]["authorization"] == "Bearer bearer-token-xyz"
    assert b'"expires_in":604800' in captured["body"]
    assert minted.secret == "s" * 64
    assert minted.first_eight == "ssssssss"
    assert minted.expires_at == datetime(2026, 5, 24, 20, 14, tzinfo=UTC)
    assert minted.scopes == ("read:metadata", "read:data")
    assert minted.note == "lightfall bcg-ws-3 user123"


def test_mint_service_key_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "no create:apikeys"})

    with httpx.Client(transport=_stub_transport(handler)) as client:
        import lightfall.auth.service_key as mod
        original = mod.httpx
        class _Mod:
            def post(self_inner, url, **kwargs):
                return client.post(url, **kwargs)
            def delete(self_inner, url, **kwargs):
                return client.delete(url, **kwargs)
            HTTPError = httpx.HTTPError
            HTTPStatusError = httpx.HTTPStatusError
        mod.httpx = _Mod()
        try:
            with pytest.raises(httpx.HTTPStatusError):
                mint_service_key(
                    "https://example/api/v1",
                    "bearer-token-xyz",
                    expires_in=600,
                    scopes=["read:metadata"],
                    note="t",
                )
        finally:
            mod.httpx = original


def test_revoke_service_key_swallows_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "kaboom"})

    with httpx.Client(transport=_stub_transport(handler)) as client:
        import lightfall.auth.service_key as mod
        original = mod.httpx
        class _Mod:
            def post(self_inner, url, **kwargs):
                return client.post(url, **kwargs)
            def delete(self_inner, url, **kwargs):
                return client.delete(url, **kwargs)
            HTTPError = httpx.HTTPError
            HTTPStatusError = httpx.HTTPStatusError
        mod.httpx = _Mod()
        try:
            # Must NOT raise
            revoke_service_key(
                "https://example/api/v1",
                "bearer-token-xyz",
                first_eight="aaaaaaaa",
            )
        finally:
            mod.httpx = original


def test_minted_key_is_expired():
    past = MintedKey(
        secret="x",
        first_eight="x",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        scopes=(),
        note=None,
    )
    future = MintedKey(
        secret="x",
        first_eight="x",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        scopes=(),
        note=None,
    )
    no_exp = MintedKey(secret="x", first_eight="x", expires_at=None, scopes=(), note=None)
    assert past.is_expired
    assert not future.is_expired
    assert not no_exp.is_expired
```

- [ ] **Step 7: If a `tests/auth/test_job_key.py` existed, delete it**

Run: `git rm tests/auth/test_job_key.py` (skip if it didn't exist).

- [ ] **Step 8: Run the new tests**

Run: `.venv/Scripts/python -m pytest tests/auth/test_service_key.py -v`

Expected: 4 PASS.

- [ ] **Step 9: Commit**

```bash
git add src/lightfall/auth/service_key.py src/lightfall/auth/__init__.py tests/auth/test_service_key.py
git add -u src/lightfall/auth/job_key.py tests/auth/test_job_key.py 2>/dev/null
git commit -m "refactor(auth): rename job_key → service_key, add is_expired"
```

---

### Task 2: Add `ServiceKeyAuth` and `StaticApiKeyAuth`

**Files:**
- Create: `src/lightfall/auth/service_key_auth.py`
- Create: `tests/auth/test_service_key_auth.py`

Two httpx.Auth flavors: one reads from `SessionManager`'s cache (in-process consumers); one captures a literal secret (executor subprocesses with no SessionManager).

- [ ] **Step 1: Write the failing tests**

Create `tests/auth/test_service_key_auth.py`:

```python
"""Unit tests for ServiceKeyAuth + StaticApiKeyAuth."""
from __future__ import annotations

import httpx
import pytest

from lightfall.auth.service_key_auth import ServiceKeyAuth, StaticApiKeyAuth


def test_static_apikey_auth_sets_header():
    auth = StaticApiKeyAuth("the-secret")
    request = httpx.Request("GET", "https://example/data")
    flow = auth.sync_auth_flow(request)
    out = next(flow)
    assert out.headers["Authorization"] == "Apikey the-secret"


def test_service_key_auth_reads_from_session_manager(monkeypatch):
    # Stand up a fake SessionManager that returns a known secret
    captured: dict = {}

    class _FakeSM:
        @classmethod
        def get_instance(cls):
            return cls()
        def get_api_key(self, service):
            captured["service"] = service
            return "tiled-secret-xyz"

    monkeypatch.setattr(
        "lightfall.auth.service_key_auth.SessionManager", _FakeSM
    )

    auth = ServiceKeyAuth("tiled")
    request = httpx.Request("GET", "https://example/data")
    flow = auth.sync_auth_flow(request)
    out = next(flow)

    assert captured["service"] == "tiled"
    assert out.headers["Authorization"] == "Apikey tiled-secret-xyz"


def test_service_key_auth_skips_header_when_no_key(monkeypatch):
    """When the cache slot is empty (mint failed at login), yield the request
    without an Authorization header. Downstream call will fail 401 → UI
    surfaces the re-login prompt."""

    class _FakeSM:
        @classmethod
        def get_instance(cls):
            return cls()
        def get_api_key(self, service):
            return None

    monkeypatch.setattr(
        "lightfall.auth.service_key_auth.SessionManager", _FakeSM
    )

    auth = ServiceKeyAuth("logbook")
    request = httpx.Request("GET", "https://example/data")
    flow = auth.sync_auth_flow(request)
    out = next(flow)

    assert "Authorization" not in out.headers
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/auth/test_service_key_auth.py -v`

Expected: ImportError on `lightfall.auth.service_key_auth` (module doesn't exist yet).

- [ ] **Step 3: Implement `src/lightfall/auth/service_key_auth.py`**

```python
"""httpx.Auth adapters for Lightfall's per-service API keys.

ServiceKeyAuth pulls the current API key from SessionManager's cache on
every request — used by in-process consumers (Lightfall's own data-browser,
RE callback writers, etc.) that share the singleton SessionManager.

StaticApiKeyAuth captures a literal secret at construction time — used by
out-of-process consumers (lightfall.exporter executor, lightfall-pipelines
executor, tsuchinoko executor) that receive the key in their NATS job
payload and have no SessionManager singleton.

Both produce the same wire behavior: `Authorization: Apikey <secret>`.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import httpx

from lightfall.auth.session import SessionManager


class ServiceKeyAuth(httpx.Auth):
    """httpx.Auth that reads a service's API key from SessionManager.

    Construct one instance per service name:
        ServiceKeyAuth("tiled")
        ServiceKeyAuth("logbook")

    Reads on every request so a re-login that refreshes the cache is picked
    up without rebuilding the underlying client.
    """

    def __init__(self, service: str) -> None:
        self._service = service

    def _set_header(self, request: httpx.Request) -> bool:
        """Set Authorization if a key is cached; return True if set."""
        secret = SessionManager.get_instance().get_api_key(self._service)
        if secret is None:
            return False
        request.headers["Authorization"] = f"Apikey {secret}"
        return True

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        self._set_header(request)
        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        self._set_header(request)
        yield request


class StaticApiKeyAuth(httpx.Auth):
    """httpx.Auth that injects a captured literal API key.

    Used by executor subprocesses (exporter, pipelines, tsuchinoko) that
    receive the key in their job payload and have no SessionManager
    singleton.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Apikey {self._secret}"
        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Apikey {self._secret}"
        yield request
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/auth/test_service_key_auth.py -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/auth/service_key_auth.py tests/auth/test_service_key_auth.py
git commit -m "feat(auth): ServiceKeyAuth (cache-backed) + StaticApiKeyAuth (literal)"
```

---

### Task 3: SessionManager grows a service-key cache

**Files:**
- Modify: `src/lightfall/auth/session.py` (add cache fields, `get_api_key`, `get_minted_key`)
- Create: `tests/auth/test_session_manager_mint.py`

This task adds the cache infrastructure WITHOUT wiring it into `login()` yet. Task 4 wires the mint round; this task is the storage half.

- [ ] **Step 1: Write the failing tests for the cache surface**

Create `tests/auth/test_session_manager_mint.py`:

```python
"""Tests for SessionManager's service-key cache + login mint round.

Covers Task 3 (cache surface, this file initially) and Task 4 (login mint).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lightfall.auth.service_key import MintedKey
from lightfall.auth.session import SessionManager


@pytest.fixture(autouse=True)
def reset_singleton():
    SessionManager.reset()
    yield
    SessionManager.reset()


def _minted(secret: str = "abc123", expires_in_s: int = 3600) -> MintedKey:
    return MintedKey(
        secret=secret,
        first_eight=secret[:8].ljust(8, "x"),
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_s),
        scopes=("read:metadata",),
        note="test",
    )


def test_get_api_key_returns_none_when_no_cache():
    sm = SessionManager.get_instance()
    assert sm.get_api_key("tiled") is None


def test_get_api_key_returns_cached_secret():
    sm = SessionManager.get_instance()
    sm._service_keys["tiled"] = _minted(secret="tiled-key")
    assert sm.get_api_key("tiled") == "tiled-key"


def test_get_api_key_returns_none_when_expired():
    sm = SessionManager.get_instance()
    sm._service_keys["tiled"] = _minted(secret="old", expires_in_s=-60)
    assert sm.get_api_key("tiled") is None


def test_get_minted_key_returns_full_record():
    sm = SessionManager.get_instance()
    key = _minted(secret="full")
    sm._service_keys["tiled"] = key
    assert sm.get_minted_key("tiled") is key


def test_cache_cleared_on_logout(monkeypatch):
    import asyncio

    sm = SessionManager.get_instance()
    sm._service_keys["tiled"] = _minted()
    assert sm.get_api_key("tiled") is not None

    # logout() is async; run it
    asyncio.run(sm.logout())

    assert sm.get_api_key("tiled") is None
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/Scripts/python -m pytest tests/auth/test_session_manager_mint.py -v`

Expected: all four fail with `AttributeError` on `_service_keys` or `get_api_key`.

- [ ] **Step 3: Add cache fields to `SessionManager.__init__`**

In `src/lightfall/auth/session.py`, find the `__init__` method (around line 164). Locate the existing token-refresh state block:

```python
        # Token refresh state
        self._refresh_in_progress = False
        self._fast_retry_count = 0
        self._refresh_timer_id: int | None = None
```

**Leave those lines intact** — they're still needed for the logbook bearer flow until the cleanup plan retires them. Add the new cache fields right after, before the `state_changed.connect` block:

```python
        # Service-key cache (auth-v2): per-(user, service) API keys minted at
        # login. See docs/superpowers/specs/2026-05-17-lightfall-auth-v2-design.md.
        # The refresh state above is transitionally kept alive for the logbook
        # bearer flow until lightfall-logbook ships its mint endpoint.
        self._service_keys: dict[str, MintedKey] = {}
        self._keys_lock = threading.RLock()
```

Add the import at the top of `session.py` (alongside the existing imports):

```python
from lightfall.auth.service_key import MintedKey
```

- [ ] **Step 4: Add `get_api_key()` and `get_minted_key()` methods**

In `src/lightfall/auth/session.py`, add after the `policy_engine` property (around line 235):

```python
    def get_api_key(self, service: str) -> str | None:
        """Return the cached API-key secret for a service, or None if absent or expired.

        Consumers (e.g. ServiceKeyAuth) call this on every request so that a
        re-login that refreshes the cache is picked up immediately.
        """
        with self._keys_lock:
            minted = self._service_keys.get(service)
        if minted is None:
            return None
        if minted.is_expired:
            return None
        return minted.secret

    def get_minted_key(self, service: str) -> MintedKey | None:
        """Return the full cached record (for NATS payload embedding).

        Unlike get_api_key, this returns the whole MintedKey including
        first_eight and expiry so the dispatcher can pass the metadata along
        with the secret. Returns None if the slot is empty.
        """
        with self._keys_lock:
            return self._service_keys.get(service)
```

- [ ] **Step 5: Update `logout()` to clear the cache**

In `src/lightfall/auth/session.py`, find `async def logout(self)` (around line 305). Locate the line `self._session = None` and add the cache-clear just after it:

```python
        old_user = self._session.user
        self._session = None
        with self._keys_lock:
            self._service_keys.clear()
        self._set_state(AuthState.UNAUTHENTICATED)
```

(The logout-RE-gate dialog is added in the cleanup plan, not here.)

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/auth/test_session_manager_mint.py -v`

Expected: all 5 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lightfall/auth/session.py tests/auth/test_session_manager_mint.py
git commit -m "feat(auth): SessionManager service-key cache (no-mint-yet)"
```

---

### Task 4: Wire `_mint_all_service_keys` into `login()`

**Files:**
- Modify: `src/lightfall/auth/session.py`
- Modify: `tests/auth/test_session_manager_mint.py`

Mints in parallel via `asyncio.to_thread` for each configured service; failures log + leave the slot empty (per spec: login degrades, doesn't fail).

For Task 4, only Tiled is on the list. Logbook will be added by the logbook consumer plan. Configuration: read the Tiled URL via `lightfall.services.tiled_service` helpers (existing) — no new schema field.

- [ ] **Step 1: Read the Tiled URL resolver**

Run: `grep -n "DEFAULT_TILED\|def get_tiled\|tiled_url" src/lightfall/services/tiled_service.py | head -20`

You should find a function or constant that resolves the configured Tiled base URL from `PreferencesManager`. Note its name — `_mint_all_service_keys` will call it.

If no helper exists, write one in `src/lightfall/services/tiled_service.py`:

```python
DEFAULT_TILED_URL = "http://bcgtiled.dhcp.lbl.gov:8000"  # adjust to actual default

def get_tiled_base_url() -> str:
    """Return the configured Tiled base URL (matches get_logbook_base_url shape)."""
    try:
        from lightfall.ui.preferences.manager import PreferencesManager
        prefs = PreferencesManager.get_instance()
        value = prefs.get("tiled_url", None)
    except Exception:
        value = None
    return value or DEFAULT_TILED_URL
```

If a helper exists, skip writing one.

- [ ] **Step 2: Add failing tests for the mint round**

Append to `tests/auth/test_session_manager_mint.py`:

```python
def test_mint_all_service_keys_populates_cache(monkeypatch):
    """A successful mint populates the cache slot."""
    sm = SessionManager.get_instance()
    called: list = []

    def fake_mint(service_url, bearer, *, expires_in, scopes, note, timeout=10.0):
        called.append((service_url, bearer, expires_in, tuple(scopes), note))
        return _minted(secret=f"key-for-{service_url}")

    monkeypatch.setattr("lightfall.auth.session.mint_service_key", fake_mint)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test/api/v1",
    )

    sm._mint_all_service_keys("bearer-xyz")

    assert "tiled" in sm._service_keys
    assert sm.get_api_key("tiled") == "key-for-https://tiled.test/api/v1"
    # Verify the request shape
    assert len(called) == 1
    url, bearer, expires_in, scopes, note = called[0]
    assert url == "https://tiled.test/api/v1"
    assert bearer == "bearer-xyz"
    assert expires_in == 604800
    assert "read:metadata" in scopes and "create:apikeys" not in scopes
    assert "lightfall" in note


def test_mint_all_service_keys_tolerates_failure(monkeypatch, caplog):
    """A failed mint logs but leaves the slot empty; other services unaffected."""
    sm = SessionManager.get_instance()

    def boom(service_url, bearer, **kwargs):
        raise httpx.ConnectError("unreachable")

    import httpx
    monkeypatch.setattr("lightfall.auth.session.mint_service_key", boom)
    monkeypatch.setattr(
        "lightfall.services.tiled_service.get_tiled_base_url",
        lambda: "https://tiled.test/api/v1",
    )

    # MUST NOT raise
    sm._mint_all_service_keys("bearer-xyz")

    assert sm.get_api_key("tiled") is None
```

Add `import httpx` to the test file's imports.

- [ ] **Step 3: Run tests, confirm failure**

Run: `.venv/Scripts/python -m pytest tests/auth/test_session_manager_mint.py::test_mint_all_service_keys_populates_cache -v`

Expected: AttributeError on `_mint_all_service_keys`.

- [ ] **Step 4: Implement `_mint_all_service_keys`**

In `src/lightfall/auth/session.py`, add an import near the top:

```python
from lightfall.auth.service_key import MintedKey, mint_service_key
```

(MintedKey is already imported from Task 3; merge into a single import.)

Then add the method to `SessionManager` (just below `get_minted_key`):

```python
    # Default scopes per service. See spec §"Scopes".
    _SERVICE_SCOPES: dict[str, list[str]] = {
        "tiled": [
            "read:metadata", "read:data",
            "write:metadata", "write:data",
            "register", "create:node",
        ],
        # logbook entry added by the logbook consumer plan; empty scopes
        # (logbook has no granular scope model).
    }

    _SESSION_KEY_LIFETIME = 604800  # 7 days, per spec

    def _mint_all_service_keys(self, bearer_token: str) -> None:
        """Mint a session key per configured service in sequence.

        Called by login() once authentication succeeds. Failures are logged
        + toasted, never raised — login degrades but does not fail per spec.

        Phase 1 (this plan): only Tiled. Logbook is added by the logbook
        consumer plan.

        This is synchronous httpx + a small N; running serially keeps the
        code simple. If N grows beyond a handful of services or any mint
        ever blocks for noticeable wall time, switch to a thread pool here.
        """
        from lightfall.services.tiled_service import get_tiled_base_url

        # Resolve per-service URL. Add new services here as they migrate.
        urls = {"tiled": get_tiled_base_url().rstrip("/") + "/api/v1"}

        hostname = self._hostname_for_note()
        sub = (
            self._session.user.attributes.get("sub", "unknown")
            if self._session and self._session.user
            else "unknown"
        )
        note = f"lightfall {hostname} {sub}"

        for service, url in urls.items():
            scopes = self._SERVICE_SCOPES.get(service, [])
            try:
                minted = mint_service_key(
                    url,
                    bearer_token,
                    expires_in=self._SESSION_KEY_LIFETIME,
                    scopes=scopes,
                    note=note,
                )
            except Exception as e:
                logger.warning(
                    "mint failed for service={} url={}: {}", service, url, e
                )
                # Slot stays empty; subsequent get_api_key() returns None.
                continue

            with self._keys_lock:
                self._service_keys[service] = minted
            logger.info("session key cached for service={}", service)

    @staticmethod
    def _hostname_for_note() -> str:
        """Return the local hostname for mint audit notes; falls back gracefully."""
        import socket
        try:
            return socket.gethostname()
        except Exception:
            return "unknown-host"
```

- [ ] **Step 5: Invoke `_mint_all_service_keys` from `login()`**

In `src/lightfall/auth/session.py`, find `async def login(...)` (around line 255). Locate the block where the session is set and state transitions to AUTHENTICATED:

```python
            if session:
                self._session = session
                self._set_state(AuthState.AUTHENTICATED)
                self.user_changed.emit(session.user)
                logger.info("User '{}' authenticated", session.user.username)
```

Insert the mint round AFTER `self._session = session` and BEFORE `self._set_state(AuthState.AUTHENTICATED)`. Wrap in `asyncio.to_thread` so the synchronous httpx calls don't block the event loop:

```python
            if session:
                self._session = session

                # Mint per-service API keys before transitioning to AUTHENTICATED
                # so the keys are available to any AUTHENTICATED-state listeners.
                # Failure is non-fatal: individual slots may be empty.
                if session.token:
                    import asyncio
                    await asyncio.to_thread(
                        self._mint_all_service_keys, session.token
                    )

                self._set_state(AuthState.AUTHENTICATED)
                self.user_changed.emit(session.user)
                logger.info("User '{}' authenticated", session.user.username)
```

Do NOT discard `session.token` afterward — the bearer is kept for the still-bearer-based logbook flow. The cleanup plan discards it once all consumers have migrated.

- [ ] **Step 6: Run the new tests**

Run: `.venv/Scripts/python -m pytest tests/auth/test_session_manager_mint.py -v`

Expected: all PASS (now 7 tests total).

- [ ] **Step 7: Run the broader auth test suite for regressions**

Run: `.venv/Scripts/python -m pytest tests/auth/ -v`

Expected: all PASS. If a test on the existing token-refresh path fails, triage — the refresh machinery is intentionally still alive, and existing tests should keep passing.

- [ ] **Step 8: Commit**

```bash
git add src/lightfall/auth/session.py tests/auth/test_session_manager_mint.py
git commit -m "feat(auth): mint per-service keys at login (Tiled only, phase 1)"
```

---

### Task 5: Migrate `KeycloakTiledAuth` consumers to `ServiceKeyAuth("tiled")`

**Files:**
- Modify: `src/lightfall/services/tiled_auth.py` (or delete)
- Modify: `src/lightfall/services/tiled_service.py:312-313`
- Modify: `src/lightfall/ui/preferences/tiled_settings.py:252-254`
- Modify: `src/lightfall/ipc/service.py:321`
- Create: `tests/services/test_tiled_auth_migration.py`

Site-by-site replacement. Start with the central Tiled auth class, then the call sites.

- [ ] **Step 1: Inventory the consumers of `KeycloakTiledAuth`**

Run: `grep -rn "KeycloakTiledAuth\|from lightfall\.services\.tiled_auth" src/lightfall/ tests/`

Capture the list. Expected sites:
- `src/lightfall/services/tiled_service.py` — the most likely import.
- Possibly `src/lightfall/exporter/` (executor side — keeps `BearerAuth` until Task 7).
- Tests that exercise the auth class.

- [ ] **Step 2: Rewrite `src/lightfall/services/tiled_auth.py` as a thin compat shim**

Replace the file's contents with:

```python
"""Compatibility shim: previously held KeycloakTiledAuth (bearer-based).

Replaced by lightfall.auth.service_key_auth.ServiceKeyAuth("tiled") in auth-v2.
KeycloakTiledAuth is now a subclass of ServiceKeyAuth that hard-codes the
service name. Existing call sites (`KeycloakTiledAuth()`, `isinstance(x,
KeycloakTiledAuth)`, subclassing) all keep working.

This shim WILL be deleted in the auth cleanup plan once no in-tree code
imports the old name. Internal Lightfall code should import ServiceKeyAuth
from lightfall.auth.service_key_auth directly going forward.
"""
from __future__ import annotations

from lightfall.auth.service_key_auth import ServiceKeyAuth


class KeycloakTiledAuth(ServiceKeyAuth):
    """Deprecated alias for ServiceKeyAuth(\"tiled\")."""

    def __init__(self) -> None:
        super().__init__("tiled")
```

- [ ] **Step 3: Switch in-tree call sites to `ServiceKeyAuth("tiled")` directly**

For each in-tree import of `KeycloakTiledAuth`, replace with `ServiceKeyAuth`:

```python
# Before
from lightfall.services.tiled_auth import KeycloakTiledAuth
client = from_uri(url, auth=KeycloakTiledAuth())

# After
from lightfall.auth.service_key_auth import ServiceKeyAuth
client = from_uri(url, auth=ServiceKeyAuth("tiled"))
```

Run `grep -rn` to confirm no in-tree `KeycloakTiledAuth` references remain. The shim function itself stays for out-of-tree consumers.

- [ ] **Step 4: Update `src/lightfall/services/tiled_service.py:312-313`**

Read the surrounding context:

```bash
sed -n '300,325p' src/lightfall/services/tiled_service.py
```

You should see something like:

```python
        if session and session.token:
            return {"Authorization": f"Bearer {session.token}"}
```

Replace with a call to the SessionManager API-key cache:

```python
        from lightfall.auth.session import SessionManager
        secret = SessionManager.get_instance().get_api_key("tiled")
        if secret:
            return {"Authorization": f"Apikey {secret}"}
```

(The exact surrounding control flow may differ — preserve it; only the bearer-vs-apikey is what changes.)

- [ ] **Step 5: Fix the broken claims-read in `tiled_settings.py:252-254`**

Read the surrounding code:

```bash
sed -n '245,260p' src/lightfall/ui/preferences/tiled_settings.py
```

You should see:

```python
            if not session or not session.token:
                return
            groups = getattr(session.token, "claims", {}).get("groups", []) or []
```

The `session.token` is a string, not an object with a `.claims` attribute — this pattern was already broken silently. Replace with reading from the JWT claims stored on the user:

```python
            if not session or not session.user:
                return
            groups = session.user.attributes.get("groups", []) or []
```

- [ ] **Step 6: Update `src/lightfall/ipc/service.py:321`**

Read the surrounding context:

```bash
sed -n '310,335p' src/lightfall/ipc/service.py
```

You should find a payload-construction block that sets `"tiled_token": session.token`. Rename the field and source it from the cache:

```python
        # Before:
        # "tiled_token": session.token,

        # After:
        from lightfall.auth.session import SessionManager
        "tiled_api_key": SessionManager.get_instance().get_api_key("tiled"),
```

If the receiver-side parsing also lives in `ipc/service.py`, update it too. If it lives in a different file (e.g., another service that consumes the payload), follow the import and update the consumer in the same task (or note for Task 7 if it's the exporter).

- [ ] **Step 7: Write tests asserting the migration is in place**

Create `tests/services/test_tiled_auth_migration.py`:

```python
"""Asserts the Tiled auth path uses ServiceKeyAuth (auth-v2), not the bearer."""
from __future__ import annotations

import httpx

from lightfall.auth.service_key_auth import ServiceKeyAuth, StaticApiKeyAuth


def test_keycloaktiledauth_shim_returns_service_key_auth():
    from lightfall.services.tiled_auth import KeycloakTiledAuth
    obj = KeycloakTiledAuth()
    assert isinstance(obj, ServiceKeyAuth)


def test_service_key_auth_for_tiled_pulls_from_cache(monkeypatch):
    class _SM:
        @classmethod
        def get_instance(cls):
            return cls()
        def get_api_key(self, service):
            assert service == "tiled"
            return "the-tiled-key"

    monkeypatch.setattr("lightfall.auth.service_key_auth.SessionManager", _SM)

    auth = ServiceKeyAuth("tiled")
    req = httpx.Request("GET", "https://tiled.test/api/v1/metadata/")
    out = next(auth.sync_auth_flow(req))
    assert out.headers["Authorization"] == "Apikey the-tiled-key"
```

- [ ] **Step 8: Run all auth + service tests**

Run: `.venv/Scripts/python -m pytest tests/auth/ tests/services/ -v`

Expected: all PASS. Existing tests that asserted the bearer header may need updating — if they fail, the assertion should now look for `Apikey …`. Update them in place.

- [ ] **Step 9: Commit**

```bash
git add src/lightfall/services/tiled_auth.py src/lightfall/services/tiled_service.py src/lightfall/ui/preferences/tiled_settings.py src/lightfall/ipc/service.py tests/services/test_tiled_auth_migration.py
git commit -m "refactor(services): Tiled consumers use ServiceKeyAuth, claims via user.attributes"
```

---

### Task 6: Verify `access_stamper` is already auth-v2-compatible

**Files:**
- Modify: `src/lightfall/services/access_stamper.py` (audit only, no code change in this plan)

The spec listed access_stamper as a JWT-claim-consumer that needs migrating. On inspection, the current implementation **already reads claims from `session.user.attributes`** (see `_operator_identity` at `access_stamper.py:81-98`); only the `session.token is None` presence-check on line 92 remains a bearer-coupling. Since this plan keeps the bearer alive (it's only discarded by the cleanup plan), no code change is needed here.

- [ ] **Step 1: Confirm by reading the file**

```bash
sed -n '81,100p' src/lightfall/services/access_stamper.py
```

Verify you see:
- `claims = getattr(user, "attributes", None) or {}` — reading from `user.attributes`, not from `session.token`.
- `if session is None or session.token is None:` on line 92 — a session-presence check, not a claim read.

If the file diverges from this (e.g., someone introduced a JWT decode since the spec was written), then this task expands to "replace the decode with `user.attributes` reads" — but expect no work needed.

- [ ] **Step 2: Add a forward-reference comment to the cleanup plan**

In `src/lightfall/services/access_stamper.py:91-93`, change the presence-check comment to flag the upcoming cleanup-plan change:

```python
        session = self._session_provider()
        # auth-v2 cleanup plan will change this to `session.user is None` when
        # the bearer is discarded post-mint. Until then, session.token presence
        # is still a valid "logged in" signal.
        if session is None or session.token is None:
            raise MissingSessionError("No Keycloak session — refusing to stamp")
```

- [ ] **Step 3: Commit if anything changed**

```bash
git add src/lightfall/services/access_stamper.py
git commit -m "docs(access_stamper): flag bearer presence-check for cleanup-plan removal"
```

If nothing changed (the file was already as expected), skip the commit. **Note** in the audit log that this task was a no-op.

---

### Task 7: Migrate `lightfall.exporter` NATS payload to `tiled_api_key`

**Files:**
- Modify: `src/lightfall/exporter/service.py:26, 72-77, 149`
- Modify: `src/lightfall/exporter/tiled_utils.py:15-45`
- Modify: `src/lightfall/ui/dialogs/export_dialog.py:484-485`
- Modify: `src/lightfall/acquire/plans/adaptive.py:128-129`
- Modify: `tests/exporter/test_service_payload.py` (if exists)

The exporter is the Lightfall-side service that processes export NATS jobs. Its payload currently carries `auth_token` (the bearer) and the executor uses `BearerAuth`. This task renames the field and switches the executor to `StaticApiKeyAuth`.

The same payload migration happens in lightfall-pipelines and tsuchinoko in their own plans, but those are remote repos — see coordination plan steps 3 and 4.

- [ ] **Step 1: Update `ExportJob` dataclass**

In `src/lightfall/exporter/service.py`, line 26:

```python
# Before
@dataclass
class ExportJob:
    job_id: str
    tiled_url: str
    auth_token: str | None
    run_uids: list[str]
    ...

# After
@dataclass
class ExportJob:
    job_id: str
    tiled_url: str
    tiled_api_key: str | None
    run_uids: list[str]
    ...
```

- [ ] **Step 2: Update `_parse_job` to read the new field**

In `src/lightfall/exporter/service.py`, line 72:

```python
# Before
            auth_token=data.get("auth_token"),

# After
            tiled_api_key=data.get("tiled_api_key"),
```

- [ ] **Step 3: Update the executor's `connect_tiled` call**

In `src/lightfall/exporter/service.py`, line 149:

```python
# Before
                client = await asyncio.to_thread(
                    connect_tiled, job.tiled_url, job.auth_token, job.proxy_url
                )

# After
                client = await asyncio.to_thread(
                    connect_tiled, job.tiled_url, job.tiled_api_key, job.proxy_url
                )
```

- [ ] **Step 4: Update `connect_tiled` and `BearerAuth` in `tiled_utils.py`**

Read current `src/lightfall/exporter/tiled_utils.py`. Replace the `BearerAuth` class and the `connect_tiled` function:

```python
"""Executor-side Tiled connection helpers.

Used by lightfall.exporter (and copy-pasted historically into other executor
services). Constructs a tiled.client with StaticApiKeyAuth so the executor
authenticates with the Lightfall session key it received in the NATS job
payload.
"""
from __future__ import annotations

from typing import Optional

from lightfall.auth.service_key_auth import StaticApiKeyAuth


def connect_tiled(
    tiled_url: str,
    api_key: Optional[str],
    proxy_url: Optional[str] = None,
):
    """Return a tiled.client connected to tiled_url, optionally authenticated.

    Args:
        tiled_url: Base URL of the Tiled API root.
        api_key: Optional API key secret. When None, the client is anonymous.
        proxy_url: Optional SOCKS proxy URL.

    Returns:
        A tiled.client.from_uri-constructed client.
    """
    from tiled.client import from_uri

    kwargs: dict = {}
    if api_key:
        kwargs["auth"] = StaticApiKeyAuth(api_key)
    if proxy_url:
        kwargs["transport_options"] = {"proxy": proxy_url}
    return from_uri(tiled_url, **kwargs)


def get_run(client, uid: str):
    """Look up a run by UID. Kept here for back-compat with the old import path."""
    return client[uid]
```

(If the old `get_run` had different logic, preserve it — the snippet above is a minimal replacement.)

- [ ] **Step 5: Update `export_dialog.py:484` Lightfall-side dispatcher**

Read `src/lightfall/ui/dialogs/export_dialog.py:475-495`. You should find a block that reads `session.token` to embed in the job payload. Replace with the cache lookup:

```python
# Before
            session = SessionManager.get_instance().session
            if session and session.token:
                return session.token

# After
            return SessionManager.get_instance().get_api_key("tiled")
```

(Adjust the surrounding control flow as needed; the goal is that the payload field `tiled_api_key` is sourced from the cache.)

In the same file, find where the NATS payload dict is constructed and rename `auth_token` → `tiled_api_key`.

- [ ] **Step 6: Update `adaptive.py:128` for tsuchinoko-bound payload**

In `src/lightfall/acquire/plans/adaptive.py`, lines 125-135 likely look like:

```python
        session = SessionManager.get_instance().session
        if session and session.token:
            auth_token = session.token
```

Replace with:

```python
        auth_token = SessionManager.get_instance().get_api_key("tiled")
```

If the variable is later put into a payload dict under a key named `auth_token`, also rename the key to `tiled_api_key`. **Note:** the actual tsuchinoko executor must consume `tiled_api_key` for this to work end-to-end — that's the tsuchinoko-repo plan (coordination step 4). Until that lands, this side of the payload is correctly named but the executor still expects the old name. Document this in the commit message.

- [ ] **Step 7: Update payload tests**

If `tests/exporter/test_service_payload.py` (or similar) exists, update it to use `tiled_api_key`:

```python
def test_parse_job_reads_tiled_api_key():
    svc = ExporterService("nats://test", "host")
    job = svc._parse_job({
        "job_id": "j1",
        "tiled_url": "http://tiled.test",
        "tiled_api_key": "apikey-secret",
        "run_uids": ["u1"],
        "export_type": "noop",
        "params": {"output_dir": "/tmp"},
    })
    assert job.tiled_api_key == "apikey-secret"
```

If no test file exists, add one with the above.

- [ ] **Step 8: Run exporter tests**

Run: `.venv/Scripts/python -m pytest tests/exporter/ -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/lightfall/exporter/service.py src/lightfall/exporter/tiled_utils.py src/lightfall/ui/dialogs/export_dialog.py src/lightfall/acquire/plans/adaptive.py tests/exporter/
git commit -m "feat(exporter): NATS payload field auth_token → tiled_api_key

Executor consumes the session key as StaticApiKeyAuth instead of BearerAuth.
Lightfall-side dispatchers (export dialog + adaptive plan) source the key from
SessionManager's cache, not from session.token. Tsuchinoko-bound payload
field is also renamed here; the tsuchinoko executor lands in its own repo
plan (see coordination plan step 4)."
```

---

### Task 8: Audit `core/application.py:647` and any other `session.token` reads

**Files:**
- Modify: `src/lightfall/core/application.py` (audit only — may not need changes)

The spec listed this site as "purpose to verify." Triage it directly.

- [ ] **Step 1: Read the context**

```bash
sed -n '635,665p' src/lightfall/core/application.py
```

Determine what `session.token` is being used for:

| Found purpose | Action |
| --- | --- |
| Building an `Authorization: Bearer` header for an HTTP call | Replace with `SessionManager.get_instance().get_api_key("tiled")` (or "logbook") matching the target service. |
| Identity check ("is there a session?") | Replace with `session.user is not None` or `SessionManager.get_instance().is_authenticated`. |
| Forwarding to a subprocess / IPC payload | Treat as Task 7's exporter case — embed the api key, not the bearer. |
| Reading JWT claims | Replace with `session.user.attributes`. |

Apply the appropriate replacement. If the call genuinely needs a Keycloak bearer (e.g., calling Keycloak's userinfo endpoint), leave it alone — that's a legitimate mint-window use and is fine until cleanup.

- [ ] **Step 2: Re-grep for any other stragglers**

Run: `grep -rn "session\.token" src/lightfall/ | grep -v session\.py | grep -v providers/`

Each remaining hit is either:
1. A logbook call site (leave for the logbook consumer plan).
2. A JWT-claims-read masquerading as a token-read (replace with `user.attributes`).
3. A Keycloak provider internal call (leave; it's in the mint window).

If a hit doesn't fit any category, triage individually and update.

- [ ] **Step 3: Run the full test suite**

Run: `.venv/Scripts/python -m pytest tests/ -v --timeout=60`

Expected: all PASS. If a test fails on a path you didn't touch, triage — it may be a flake or an unrelated regression.

- [ ] **Step 4: Commit (if any code changed)**

```bash
git add -p src/lightfall/core/application.py  # accept only your changes
git commit -m "refactor(core): align stray session.token reads with auth-v2 conventions"
```

If no code changed in this task, skip the commit.

---

### Task 9: End-to-end smoke test against `bcgtiled`

**Files:**
- Manual / scripted verification only.

Confirm the in-memory plumbing works against the live `bcgtiled` deployment (which has Plan A's mint endpoint shipped).

- [ ] **Step 1: Run Lightfall locally with Keycloak**

```bash
.venv/Scripts/python -m lightfall
```

Log in via Keycloak. The login should succeed in the same time as today.

- [ ] **Step 2: Verify the cache populated**

Open a debug console (the existing Lightfall dev tooling). Run:

```python
from lightfall.auth.session import SessionManager
sm = SessionManager.get_instance()
print(sm.get_api_key("tiled"))
print(sm.get_minted_key("tiled"))
```

Expected: a non-None secret, and a MintedKey with `expires_at` ~1 week out.

- [ ] **Step 3: Verify a Tiled call uses the new auth**

Trigger a data-browser action (open a run). In a packet sniffer or Lightfall's debug HTTP log, confirm requests to `bcgtiled` carry `Authorization: Apikey <secret>` instead of `Authorization: Bearer <jwt>`.

- [ ] **Step 4: Logout and re-login**

Logout (no RE active). Confirm the cache is empty:

```python
print(sm.get_api_key("tiled"))  # → None
```

Re-login. Confirm a fresh key landed. The `first_eight` should differ from the previous mint (sanity check that we minted again).

- [ ] **Step 5: Verify the bearer flow still works for logbook**

(Logbook still uses `SessionAuth` and the bearer until its own plan migrates it.) Send a logbook entry. Confirm it succeeds. Refresh should still be running on its existing schedule (since we didn't delete it).

- [ ] **Step 6: Document the smoke-test result**

Append to `docs/superpowers/specs/2026-05-17-lightfall-auth-v2-design.md`:

```markdown
## Smoke test results — Task 9 of Lightfall core plan

- Date: <YYYY-MM-DD>
- Lightfall commit: <git rev-parse HEAD>
- bcgtiled commit: <ssh bcgtiled "cd /opt/als-tiled && git log -1 --oneline">
- Tiled key minted: yes (first_eight=<…>, expires_at=<…>)
- Tiled calls use Apikey header: yes
- Logbook bearer flow still works: yes
- Re-login replaces cache: yes
```

- [ ] **Step 7: Commit the smoke-test note**

```bash
git add docs/superpowers/specs/2026-05-17-lightfall-auth-v2-design.md
git commit -m "docs(auth-v2): record core-plan smoke test against bcgtiled"
```

---

## Completion criteria

- [ ] All unit tests pass: `.venv/Scripts/python -m pytest tests/auth/ tests/services/ tests/exporter/ -v`
- [ ] Full test suite passes (no regressions): `.venv/Scripts/python -m pytest tests/ -v --timeout=60`
- [ ] `grep -rn "KeycloakTiledAuth" src/lightfall/` shows only the compat shim in `services/tiled_auth.py`.
- [ ] `grep -rn "BearerAuth" src/lightfall/` shows zero hits (or only in non-Tiled-related code).
- [ ] `grep -rn "session\.token" src/lightfall/` shows only: `auth/session.py` (the field itself), `auth/providers/keycloak.py` (mint window), `auth/providers/pam.py` + `local.py` (non-Keycloak providers, unaffected), and `logbook/client.py` (deferred to logbook consumer plan).
- [ ] Smoke test against `bcgtiled` succeeds end-to-end.
- [ ] The spec's smoke-test results section is appended.

Once these are ticked, this plan is done. The Tiled side of Lightfall is fully on API keys; logbook side is unchanged (intentionally); the refresh machinery still runs (intentionally — keeps logbook alive). Next plans (per coordination):

- **lightfall-pipelines payload migration** (coordination step 3).
- **tsuchinoko payload migration** (coordination step 4).
- **lightfall-logbook mint endpoint** (coordination step 5).
- **Lightfall logbook consumer migration** (coordination step 6).
- **Lightfall auth cleanup** (coordination step 7) — finally deletes the refresh machinery and adds the logout-RE-gate dialog.

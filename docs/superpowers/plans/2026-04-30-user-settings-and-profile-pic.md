# User Settings & Profile-Pic SettingsPlugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic per-user settings KV store to the `lightfall-logbook` backend, expose it from Lightfall via a new `UserSettingsClient`, and ship a `UserProfileSettingsPlugin` that lets a user set their profile picture end-to-end.

**Architecture:** Backend gets a `user_settings(user_id, beamline, key, value)` KV table behind four CRUD endpoints under `/logbook/settings`, plus a write hook on `profile_image_id` that deletes the previous image's bytes from disk. Lightfall gets a sync `httpx`-based `UserSettingsClient` singleton that mirrors `LogbookClient`'s shape, sharing a refactored `SessionAuth` adapter and a base-URL helper. The new `UserProfileSettingsPlugin` calls the client directly (no `PreferencesManager` integration).

**Tech Stack:** Python 3.11+, Litestar, SQLAlchemy 2.x async, Pydantic v2, SQLite/Postgres on the server; `httpx`, PySide6, `pytest-httpx` on the client.

**Spec:** [`docs/superpowers/specs/2026-04-30-user-settings-and-profile-pic-design.md`](../specs/2026-04-30-user-settings-and-profile-pic-design.md)

**Repos:**
- Backend: `~/PycharmProjects/ncs/lightfall-logbook` (currently on branch `feat/logbook-images` — create a new branch `feat/user-settings` from `master`).
- Lightfall client: `~/PycharmProjects/ncs/ncs` (currently on `master` — create a new branch `feature/user-profile-settings`).

---

## Pre-flight

- [ ] **Step P1: Create backend feature branch**

```bash
cd ~/PycharmProjects/ncs/lightfall-logbook
git fetch origin
git checkout -b feat/user-settings origin/master
```

- [ ] **Step P2: Create Lightfall feature branch**

```bash
cd ~/PycharmProjects/ncs/ncs
git fetch origin
git checkout -b feature/user-profile-settings origin/master
```

- [ ] **Step P3: Verify dev installs work in both repos**

```bash
cd ~/PycharmProjects/ncs/lightfall-logbook
.venv/Scripts/python -m pytest -q          # all green pre-change
cd ~/PycharmProjects/ncs/ncs
.venv/Scripts/python -m pytest -q -k logbook   # logbook-related tests green
```

Expected: existing tests pass; if not, do not start.

---

# Phase 1 — Backend (`lightfall-logbook`)

## Task 1: Add `UserSettingRow` ORM model + Pydantic schemas

**Files:**
- Modify: `src/lightfall_logbook/models.py`
- Test: `tests/test_user_settings_model.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_user_settings_model.py
"""Round-trip tests for UserSettingRow ORM model and schemas."""
from __future__ import annotations

from datetime import datetime
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lightfall_logbook.models import (
    Base,
    UserSettingRow,
    UserSettingSchema,
    UserSettingWrite,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.mark.asyncio
async def test_round_trip(session):
    row = UserSettingRow(
        user_id="alice",
        beamline="",
        key="profile_image_id",
        value="abc-123",
    )
    session.add(row)
    await session.commit()

    result = await session.execute(
        select(UserSettingRow).where(UserSettingRow.user_id == "alice")
    )
    fetched = result.scalar_one()
    assert fetched.key == "profile_image_id"
    assert fetched.value == "abc-123"
    assert fetched.beamline == ""
    assert isinstance(fetched.updated_at, datetime)


@pytest.mark.asyncio
async def test_pk_uniqueness(session):
    """Same (user_id, beamline, key) cannot be inserted twice."""
    row1 = UserSettingRow(user_id="alice", beamline="", key="theme", value="dark")
    session.add(row1)
    await session.commit()

    row2 = UserSettingRow(user_id="alice", beamline="", key="theme", value="light")
    session.add(row2)
    with pytest.raises(Exception):  # IntegrityError, but vendor-specific
        await session.commit()


def test_write_schema_accepts_any_json():
    UserSettingWrite(value="string")
    UserSettingWrite(value=42)
    UserSettingWrite(value={"nested": [1, 2, 3]})
    UserSettingWrite(value=None)


def test_schema_round_trip():
    row = UserSettingRow(
        user_id="bob",
        beamline="11.0.1",
        key="favorite_devices",
        value=["d1", "d2"],
    )
    schema = UserSettingSchema.model_validate(row, from_attributes=True)
    assert schema.user_id == "bob"
    assert schema.beamline == "11.0.1"
    assert schema.value == ["d1", "d2"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/PycharmProjects/ncs/lightfall-logbook
.venv/Scripts/python -m pytest tests/test_user_settings_model.py -v
```

Expected: ImportError on `UserSettingRow` / `UserSettingSchema` / `UserSettingWrite`.

- [ ] **Step 3: Add the model + schemas**

Append to `src/lightfall_logbook/models.py` (after `FragmentRow`, before the Pydantic section):

```python
class UserSettingRow(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    beamline: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "beamline", "key"),
    )
```

At the top of the file, add `PrimaryKeyConstraint` to the SQLAlchemy import:

```python
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, String, Text, Uuid
```

Append to the Pydantic section:

```python
class UserSettingWrite(BaseModel):
    """Payload for PUT /logbook/settings/{key}."""
    model_config = ConfigDict(extra="ignore")

    value: Any
    beamline: str = ""


class UserSettingSchema(BaseModel):
    """Read representation."""
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    beamline: str
    key: str
    value: Any
    updated_at: datetime
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/test_user_settings_model.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Run the full backend test suite to confirm no regression**

```bash
.venv/Scripts/python -m pytest -q
```

Expected: full suite passes.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall_logbook/models.py tests/test_user_settings_model.py
git commit -m "feat(settings): add UserSettingRow ORM model and Pydantic schemas"
```

---

## Task 2: Add `SettingsController` GET endpoints

**Files:**
- Modify: `src/lightfall_logbook/api.py`
- Modify: `src/lightfall_logbook/app.py` (register the controller)
- Test: `tests/test_settings_api.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_settings_api.py
"""Tests for /logbook/settings CRUD endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from litestar.testing import AsyncTestClient

from lightfall_logbook.app import create_app


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("IMAGE_STORAGE_DIR", str(tmp_path / "images"))
    app = create_app()
    async with AsyncTestClient(app=app) as tc:
        yield tc


ALICE = {"X-User-Id": "alice"}
BOB = {"X-User-Id": "bob"}


@pytest.mark.asyncio
async def test_get_unknown_key_returns_404(client):
    resp = await client.get("/logbook/settings/missing", headers=ALICE)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_all_empty(client):
    resp = await client.get("/logbook/settings", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_get_all_scopes_to_user(client):
    """alice's settings are not visible to bob."""
    await client.put(
        "/logbook/settings/theme",
        json={"value": "dark"},
        headers=ALICE,
    )
    resp = await client.get("/logbook/settings", headers=BOB)
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_get_all_with_beamline_filter(client):
    """Default scope is global (beamline=''); ?beamline=X returns only that scope."""
    await client.put("/logbook/settings/k", json={"value": "global"}, headers=ALICE)
    await client.put(
        "/logbook/settings/k",
        json={"value": "bl-specific", "beamline": "11.0.1"},
        headers=ALICE,
    )

    resp = await client.get("/logbook/settings", headers=ALICE)
    assert resp.json() == {"k": "global"}

    resp = await client.get(
        "/logbook/settings?beamline=11.0.1", headers=ALICE
    )
    assert resp.json() == {"k": "bl-specific"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/test_settings_api.py -v
```

Expected: 404 / 405 because the route doesn't exist yet (or import fails because `SettingsController` isn't registered).

- [ ] **Step 3: Implement the GET endpoints**

Add to `src/lightfall_logbook/api.py` (after `ImageController`):

```python
class SettingsController(Controller):
    """Per-user key/value settings, optionally scoped to a beamline."""

    path = "/logbook/settings"

    @get("/")
    async def list_settings(
        self,
        request: Any,
        db_session: AsyncSession,
        beamline: str = "",
    ) -> dict[str, Any]:
        """Return {key: value, ...} for the requesting user in this scope."""
        user_id = _get_user_id(request)
        result = await db_session.execute(
            select(UserSettingRow)
            .where(UserSettingRow.user_id == user_id)
            .where(UserSettingRow.beamline == beamline)
        )
        rows = result.scalars().all()
        await db_session.commit()
        return {row.key: row.value for row in rows}

    @get("/{key:str}")
    async def get_setting(
        self,
        key: str,
        request: Any,
        db_session: AsyncSession,
        beamline: str = "",
    ) -> UserSettingSchema:
        user_id = _get_user_id(request)
        result = await db_session.execute(
            select(UserSettingRow).where(
                UserSettingRow.user_id == user_id,
                UserSettingRow.beamline == beamline,
                UserSettingRow.key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundException(f"Setting {key!r} not found")
        await db_session.commit()
        return UserSettingSchema.model_validate(row)
```

Update the imports at the top of `api.py`:

```python
from lightfall_logbook.models import (
    EntryCreate,
    EntryRow,
    EntrySchema,
    EntryUpdate,
    FragmentCreate,
    FragmentRow,
    FragmentSchema,
    FragmentUpdate,
    LogbookRow,
    LogbookSchema,
    UserSettingRow,
    UserSettingSchema,
    UserSettingWrite,
)
```

Wire it into `src/lightfall_logbook/app.py` — change the import line and the `route_handlers=` argument:

```python
from lightfall_logbook.api import (
    ImageController,
    LogbookController,
    SearchController,
    SettingsController,
)
...
    app = Litestar(
        route_handlers=[
            health_check,
            LogbookController,
            SearchController,
            ImageController,
            SettingsController,
        ],
        ...
    )
```

- [ ] **Step 4: Run tests; PUT-using tests still fail (no PUT yet); GET tests should pass**

```bash
.venv/Scripts/python -m pytest tests/test_settings_api.py -v
```

Expected: `test_get_unknown_key_returns_404` and `test_get_all_empty` pass; the two PUT-dependent tests fail because PUT isn't implemented. That's fine — keep going.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_logbook/api.py src/lightfall_logbook/app.py tests/test_settings_api.py
git commit -m "feat(settings): add GET /logbook/settings endpoints"
```

---

## Task 3: Add `SettingsController` PUT (upsert)

**Files:**
- Modify: `src/lightfall_logbook/api.py`
- Modify: `tests/test_settings_api.py`

- [ ] **Step 1: Write additional failing tests**

Append to `tests/test_settings_api.py`:

```python
@pytest.mark.asyncio
async def test_put_creates_then_updates(client):
    """Second PUT for the same (user, beamline, key) updates rather than inserts."""
    r1 = await client.put(
        "/logbook/settings/theme",
        json={"value": "dark"},
        headers=ALICE,
    )
    assert r1.status_code == 200
    assert r1.json()["value"] == "dark"

    r2 = await client.put(
        "/logbook/settings/theme",
        json={"value": "light"},
        headers=ALICE,
    )
    assert r2.status_code == 200
    assert r2.json()["value"] == "light"

    # And read confirms only one row exists
    r3 = await client.get("/logbook/settings", headers=ALICE)
    assert r3.json() == {"theme": "light"}


@pytest.mark.asyncio
async def test_put_arbitrary_json_value(client):
    body = {"value": {"nested": [1, 2, {"k": "v"}]}}
    r = await client.put("/logbook/settings/blob", json=body, headers=ALICE)
    assert r.status_code == 200
    assert r.json()["value"] == body["value"]


@pytest.mark.asyncio
async def test_put_does_not_leak_across_users(client):
    await client.put(
        "/logbook/settings/theme", json={"value": "alice-dark"}, headers=ALICE
    )
    await client.put(
        "/logbook/settings/theme", json={"value": "bob-light"}, headers=BOB
    )

    a = await client.get("/logbook/settings/theme", headers=ALICE)
    b = await client.get("/logbook/settings/theme", headers=BOB)
    assert a.json()["value"] == "alice-dark"
    assert b.json()["value"] == "bob-light"
```

- [ ] **Step 2: Run tests; new ones fail**

```bash
.venv/Scripts/python -m pytest tests/test_settings_api.py -v
```

Expected: the three new tests fail (405 Method Not Allowed).

- [ ] **Step 3: Implement PUT inside `SettingsController`**

Add method to `SettingsController` in `src/lightfall_logbook/api.py`:

```python
    @put("/{key:str}")
    async def put_setting(
        self,
        key: str,
        data: UserSettingWrite,
        request: Any,
        db_session: AsyncSession,
    ) -> UserSettingSchema:
        user_id = _get_user_id(request)
        result = await db_session.execute(
            select(UserSettingRow).where(
                UserSettingRow.user_id == user_id,
                UserSettingRow.beamline == data.beamline,
                UserSettingRow.key == key,
            )
        )
        row = result.scalar_one_or_none()
        old_value = row.value if row is not None else None

        if row is None:
            row = UserSettingRow(
                user_id=user_id,
                beamline=data.beamline,
                key=key,
                value=data.value,
            )
            db_session.add(row)
        else:
            row.value = data.value
            # updated_at updates via onupdate hook on commit

        await db_session.commit()
        await db_session.refresh(row)

        # Run any post-write hook for this key (Task 4 wires the registry).
        await _run_settings_post_write_hook(
            request=request,
            user_id=user_id,
            beamline=data.beamline,
            key=key,
            old_value=old_value,
            new_value=data.value,
        )
        return UserSettingSchema.model_validate(row)
```

At module level in `api.py`, add a placeholder hook (Task 4 fills it in):

```python
async def _run_settings_post_write_hook(
    *,
    request: Any,
    user_id: str,
    beamline: str,
    key: str,
    old_value: Any,
    new_value: Any,
) -> None:
    """Dispatch to any registered post-write hook for a setting key.

    Task 4 plugs the profile_image_id hook in here.
    """
    return None
```

- [ ] **Step 4: Run tests; PUT tests pass**

```bash
.venv/Scripts/python -m pytest tests/test_settings_api.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_logbook/api.py tests/test_settings_api.py
git commit -m "feat(settings): add PUT /logbook/settings/{key} with upsert"
```

---

## Task 4: Add `SettingsController` DELETE + profile-pic write hook

**Files:**
- Modify: `src/lightfall_logbook/api.py`
- Modify: `tests/test_settings_api.py`
- Test: `tests/test_user_profile_flow.py` (create)

- [ ] **Step 1: Write failing DELETE tests**

Append to `tests/test_settings_api.py`:

```python
@pytest.mark.asyncio
async def test_delete_removes_row(client):
    await client.put("/logbook/settings/k", json={"value": "v"}, headers=ALICE)
    r = await client.delete("/logbook/settings/k", headers=ALICE)
    assert r.status_code == 204

    r2 = await client.get("/logbook/settings/k", headers=ALICE)
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_returns_404(client):
    r = await client.delete("/logbook/settings/never-set", headers=ALICE)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_does_not_affect_other_users(client):
    await client.put("/logbook/settings/k", json={"value": "v"}, headers=ALICE)
    r = await client.delete("/logbook/settings/k", headers=BOB)
    assert r.status_code == 404
    # Alice's still there
    r2 = await client.get("/logbook/settings/k", headers=ALICE)
    assert r2.status_code == 200
```

- [ ] **Step 2: Write failing profile-pic flow tests**

```python
# tests/test_user_profile_flow.py
"""Profile-pic two-step flow: image upload then settings update.

Verifies the server-side hook deletes the previously-set image's bytes
when profile_image_id is updated."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from litestar.testing import AsyncTestClient

from lightfall_logbook.app import create_app


def _make_minimal_png() -> bytes:
    """Return bytes for a 1x1 white PNG."""
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return (
            struct.pack(">I", len(data))
            + c
            + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        )

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = _chunk(b"IDAT", raw)
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend


@pytest.fixture
async def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    image_dir = tmp_path / "images"
    monkeypatch.setenv("IMAGE_STORAGE_DIR", str(image_dir))
    yield image_dir


@pytest.fixture
async def client(env):
    app = create_app()
    async with AsyncTestClient(app=app) as tc:
        yield tc


HEADERS = {"X-User-Id": "alice"}


@pytest.mark.asyncio
async def test_replacing_profile_image_deletes_old_bytes(client, env: Path):
    # Upload first image
    png = _make_minimal_png()
    up1 = await client.post(
        "/logbook/images",
        files={"file": ("a.png", png, "image/png")},
        headers=HEADERS,
    )
    id1 = up1.json()["image_id"]
    assert (env / f"{id1}.png").exists()

    # Set as profile_image_id
    r = await client.put(
        "/logbook/settings/profile_image_id",
        json={"value": id1},
        headers=HEADERS,
    )
    assert r.status_code == 200

    # Upload a second image
    up2 = await client.post(
        "/logbook/images",
        files={"file": ("b.png", png, "image/png")},
        headers=HEADERS,
    )
    id2 = up2.json()["image_id"]
    assert id2 != id1

    # Update profile_image_id
    r = await client.put(
        "/logbook/settings/profile_image_id",
        json={"value": id2},
        headers=HEADERS,
    )
    assert r.status_code == 200

    # First image bytes are gone, second remain
    assert not (env / f"{id1}.png").exists()
    assert (env / f"{id2}.png").exists()


@pytest.mark.asyncio
async def test_first_set_profile_image_id_no_hook_failure(client, env: Path):
    """Setting profile_image_id when no prior value exists must not error."""
    png = _make_minimal_png()
    up = await client.post(
        "/logbook/images",
        files={"file": ("a.png", png, "image/png")},
        headers=HEADERS,
    )
    image_id = up.json()["image_id"]

    r = await client.put(
        "/logbook/settings/profile_image_id",
        json={"value": image_id},
        headers=HEADERS,
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_old_image_delete_failure_does_not_fail_put(
    client, env: Path, monkeypatch, caplog
):
    """If image_store.delete raises, the PUT still succeeds and a warning is logged."""
    png = _make_minimal_png()
    up1 = await client.post(
        "/logbook/images",
        files={"file": ("a.png", png, "image/png")},
        headers=HEADERS,
    )
    id1 = up1.json()["image_id"]
    await client.put(
        "/logbook/settings/profile_image_id",
        json={"value": id1},
        headers=HEADERS,
    )

    # Patch the ImageStore.delete bound to the app
    app = client.app
    original_delete = app.state.image_store.delete

    def boom(image_id: str) -> bool:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app.state.image_store, "delete", boom)

    up2 = await client.post(
        "/logbook/images",
        files={"file": ("b.png", png, "image/png")},
        headers=HEADERS,
    )
    id2 = up2.json()["image_id"]

    r = await client.put(
        "/logbook/settings/profile_image_id",
        json={"value": id2},
        headers=HEADERS,
    )
    assert r.status_code == 200
    # Restore so cleanup at fixture teardown is sane
    monkeypatch.setattr(app.state.image_store, "delete", original_delete)
```

- [ ] **Step 3: Run tests; new ones fail**

```bash
.venv/Scripts/python -m pytest tests/test_settings_api.py tests/test_user_profile_flow.py -v
```

Expected: DELETE tests fail (405); profile-pic-flow tests fail (the second PUT succeeds but first image bytes are NOT deleted yet).

- [ ] **Step 4: Implement DELETE + the hook**

Add the DELETE method to `SettingsController` in `api.py`:

```python
    @delete("/{key:str}", status_code=204)
    async def delete_setting(
        self,
        key: str,
        request: Any,
        db_session: AsyncSession,
        beamline: str = "",
    ) -> None:
        user_id = _get_user_id(request)
        result = await db_session.execute(
            select(UserSettingRow).where(
                UserSettingRow.user_id == user_id,
                UserSettingRow.beamline == beamline,
                UserSettingRow.key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundException(f"Setting {key!r} not found")
        await db_session.delete(row)
        await db_session.commit()
```

Replace the placeholder `_run_settings_post_write_hook` in `api.py` with the real registry + profile_image_id hook:

```python
async def _profile_image_id_post_write(
    *, request: Any, old_value: Any, new_value: Any, **_: Any
) -> None:
    """When profile_image_id is changed, delete the previous image's bytes."""
    if not old_value or old_value == new_value:
        return
    image_store: ImageStore = request.app.state.image_store
    try:
        image_store.delete(old_value)
    except Exception as e:
        logger.warning(
            "Failed to delete old profile image {}: {}", old_value, e
        )


_SETTINGS_POST_WRITE_HOOKS: dict[str, Any] = {
    "profile_image_id": _profile_image_id_post_write,
}


async def _run_settings_post_write_hook(
    *,
    request: Any,
    user_id: str,
    beamline: str,
    key: str,
    old_value: Any,
    new_value: Any,
) -> None:
    hook = _SETTINGS_POST_WRITE_HOOKS.get(key)
    if hook is None:
        return
    await hook(
        request=request,
        user_id=user_id,
        beamline=beamline,
        old_value=old_value,
        new_value=new_value,
    )
```

- [ ] **Step 5: Run tests; everything in Phase 1 should pass**

```bash
.venv/Scripts/python -m pytest tests/test_settings_api.py tests/test_user_profile_flow.py tests/test_user_settings_model.py -v
.venv/Scripts/python -m pytest -q   # full suite, no regressions
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall_logbook/api.py tests/test_settings_api.py tests/test_user_profile_flow.py
git commit -m "feat(settings): add DELETE endpoint and profile_image_id post-write hook"
```

---

## Task 5: Push backend branch (review checkpoint)

- [ ] **Step 1: Push the branch and stop**

```bash
git push -u origin feat/user-settings
```

This is a natural pause point: the backend is feature-complete and tested. The Lightfall-side work in Phase 2+ does not need the backend to be merged, only deployed (or run locally) for manual end-to-end testing in Phase 4.

---

# Phase 2 — Lightfall-side shared HTTP plumbing

These two tasks are pure refactors. No new behavior, no new tests beyond confirming the existing ones still pass.

## Task 6: Extract `SessionAuth` to `lightfall/auth/httpx_auth.py`

**Files:**
- Create: `src/lightfall/auth/httpx_auth.py`
- Modify: `src/lightfall/logbook/client.py`

- [ ] **Step 1: Create the new module**

```python
# src/lightfall/auth/httpx_auth.py
"""Shared httpx.Auth adapter that pulls the Bearer token fresh from the
SessionManager on every request.

Used by any client that talks to a Keycloak-protected service: LogbookClient,
UserSettingsClient, etc. Reading per-request keeps refreshed tokens working
during long-running operations.
"""
from __future__ import annotations

import httpx


class SessionAuth(httpx.Auth):
    """httpx auth that injects the current Bearer token per request.

    Optionally also sets ``X-User-Id`` for dev/testing when a fixed user
    id is desired (e.g., when Keycloak is disabled on the server).
    """

    def __init__(self, user_id: str | None = None) -> None:
        self._user_id = user_id

    def sync_auth_flow(self, request):
        try:
            from lightfall.auth.session import SessionManager
            session = SessionManager.get_instance().session
            if session and session.token:
                request.headers["Authorization"] = f"Bearer {session.token}"
        except Exception:
            pass
        if self._user_id:
            request.headers["X-User-Id"] = self._user_id
        yield request
```

- [ ] **Step 2: Replace `_SessionAuth` in `lightfall/logbook/client.py`**

Open `src/lightfall/logbook/client.py`. Delete the `_SessionAuth` class (currently around lines 93–113). Update the import block at the top to add:

```python
from lightfall.auth.httpx_auth import SessionAuth
```

In `_run_sync` (currently around line 131), replace:

```python
    client_kwargs["auth"] = _SessionAuth(user_id=user_id)
```

with:

```python
    client_kwargs["auth"] = SessionAuth(user_id=user_id)
```

- [ ] **Step 3: Run logbook tests**

```bash
cd ~/PycharmProjects/ncs/ncs
.venv/Scripts/python -m pytest tests/test_tiled_auth.py tests/ -k "logbook" -v
```

Expected: any test that exercised `_SessionAuth` continues to pass; nothing new.

- [ ] **Step 4: Commit**

```bash
git add src/lightfall/auth/httpx_auth.py src/lightfall/logbook/client.py
git commit -m "refactor(auth): extract SessionAuth from logbook client to lightfall.auth.httpx_auth"
```

---

## Task 7: Extract `get_logbook_base_url()` helper

**Files:**
- Create: `src/lightfall/logbook/url.py`
- Modify: `src/lightfall/logbook/client.py`
- Test: `tests/test_logbook_url.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logbook_url.py
"""Test the logbook base-URL lookup helper."""
from __future__ import annotations

import pytest


def test_default_when_no_pref(monkeypatch):
    """If PreferencesManager isn't initialised or has no value, return the
    fallback base URL."""
    from lightfall.logbook.url import get_logbook_base_url, DEFAULT_LOGBOOK_URL

    # Force the prefs lookup to raise (simulate uninitialised manager)
    import lightfall.logbook.url as mod

    def boom():
        raise RuntimeError("no prefs in test")

    monkeypatch.setattr(mod, "_load_pref", boom)
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL


def test_pref_value_overrides_default(monkeypatch):
    from lightfall.logbook.url import get_logbook_base_url
    import lightfall.logbook.url as mod

    monkeypatch.setattr(mod, "_load_pref", lambda: "https://custom.example/lb")
    assert get_logbook_base_url() == "https://custom.example/lb"


def test_pref_returning_empty_falls_back(monkeypatch):
    """A blank/None pref must yield the default, not an empty URL."""
    from lightfall.logbook.url import get_logbook_base_url, DEFAULT_LOGBOOK_URL
    import lightfall.logbook.url as mod

    monkeypatch.setattr(mod, "_load_pref", lambda: "")
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL
    monkeypatch.setattr(mod, "_load_pref", lambda: None)
    assert get_logbook_base_url() == DEFAULT_LOGBOOK_URL
```

- [ ] **Step 2: Run; fails on import**

```bash
.venv/Scripts/python -m pytest tests/test_logbook_url.py -v
```

Expected: ImportError on `lightfall.logbook.url`.

- [ ] **Step 3: Implement the helper**

```python
# src/lightfall/logbook/url.py
"""Resolve the logbook base URL once for any client that needs it.

LogbookClient and UserSettingsClient both talk to the same backend, so
both should ask this helper for the URL rather than reimplementing the
prefs lookup.
"""
from __future__ import annotations

DEFAULT_LOGBOOK_URL = "http://bcglightfalllogbook.dhcp.lbl.gov"


def _load_pref() -> str | None:
    """Read the configured logbook URL from PreferencesManager.

    Returns None on any failure (manager uninitialised, ConfigManager
    missing, etc.). Wrapped so it's trivially monkeypatchable in tests.
    """
    from lightfall.ui.preferences.manager import PreferencesManager
    prefs = PreferencesManager.get_instance()
    return prefs.get("logbook_url", None)


def get_logbook_base_url() -> str:
    """Return the configured logbook base URL, or the default fallback."""
    try:
        value = _load_pref()
    except Exception:
        return DEFAULT_LOGBOOK_URL
    return value or DEFAULT_LOGBOOK_URL
```

- [ ] **Step 4: Use it from `LogbookClient._load_preferences`**

In `src/lightfall/logbook/client.py`, replace `_load_preferences`:

```python
    def _load_preferences(self) -> None:
        from lightfall.logbook.url import get_logbook_base_url
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            self._server_url = get_logbook_base_url()
            self._offline_only = prefs.get("logbook_offline_only", False)
        except Exception:
            logger.debug("Could not load logbook preferences, using defaults")
```

- [ ] **Step 5: Run tests**

```bash
.venv/Scripts/python -m pytest tests/test_logbook_url.py -v
.venv/Scripts/python -m pytest tests/ -k logbook -q   # confirm no logbook regressions
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/logbook/url.py src/lightfall/logbook/client.py tests/test_logbook_url.py
git commit -m "refactor(logbook): extract get_logbook_base_url helper"
```

---

# Phase 3 — `UserSettingsClient`

## Task 8: Module skeleton + `get` / `get_all`

**Files:**
- Create: `src/lightfall/settings/__init__.py`
- Create: `src/lightfall/settings/user_settings_client.py`
- Test: `tests/test_user_settings_client.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_user_settings_client.py
"""Tests for UserSettingsClient — uses pytest-httpx's httpx_mock fixture."""
from __future__ import annotations

import re

import httpx
import pytest


@pytest.fixture(autouse=True)
def _reset_singleton():
    from lightfall.settings.user_settings_client import UserSettingsClient
    UserSettingsClient.reset()
    yield
    UserSettingsClient.reset()


def _client(base_url="https://lb.test"):
    from lightfall.settings.user_settings_client import UserSettingsClient
    UserSettingsClient.init(base_url=base_url)
    return UserSettingsClient.get_instance()


def test_get_returns_value(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/theme?beamline=",
        json={
            "user_id": "alice",
            "beamline": "",
            "key": "theme",
            "value": "dark",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    c = _client()
    assert c.get("theme") == "dark"


def test_get_with_default_swallows_404(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://lb\.test/logbook/settings/missing.*"),
        status_code=404,
    )
    c = _client()
    assert c.get("missing", default="fallback") == "fallback"


def test_get_with_default_swallows_connection_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    c = _client()
    assert c.get("anything", default=None) is None


def test_get_all_returns_dict(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings?beamline=",
        json={"theme": "dark", "favorite": ["a", "b"]},
    )
    c = _client()
    assert c.get_all() == {"theme": "dark", "favorite": ["a", "b"]}


def test_beamline_query_passed_through(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings?beamline=11.0.1",
        json={},
    )
    c = _client()
    assert c.get_all(beamline="11.0.1") == {}
```

- [ ] **Step 2: Run; fails on import**

```bash
.venv/Scripts/python -m pytest tests/test_user_settings_client.py -v
```

Expected: ImportError on `lightfall.settings.user_settings_client`.

- [ ] **Step 3: Implement the client skeleton**

```python
# src/lightfall/settings/__init__.py
"""User-scoped settings (server-backed) for Lightfall."""
```

```python
# src/lightfall/settings/user_settings_client.py
"""Sync HTTP client for the lightfall-logbook /logbook/settings endpoints.

Used for user-scoped settings that must follow a user across machines
(profile picture, future user-level prefs). Local-only preferences
continue to live in PreferencesManager.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from lightfall.auth.httpx_auth import SessionAuth
from lightfall.logbook.url import get_logbook_base_url
from lightfall.utils.logging import logger


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
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/test_user_settings_client.py -v
```

Expected: all 5 pass.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/settings/__init__.py src/lightfall/settings/user_settings_client.py tests/test_user_settings_client.py
git commit -m "feat(settings): add UserSettingsClient with get and get_all"
```

---

## Task 9: `set` / `delete`

**Files:**
- Modify: `src/lightfall/settings/user_settings_client.py`
- Modify: `tests/test_user_settings_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_user_settings_client.py`:

```python
def test_set_posts_value(httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/theme",
        match_json={"value": "dark", "beamline": ""},
        json={
            "user_id": "alice",
            "beamline": "",
            "key": "theme",
            "value": "dark",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    c = _client()
    c.set("theme", "dark")  # no return value checked


def test_set_with_beamline(httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/k",
        match_json={"value": [1, 2], "beamline": "11.0.1"},
        json={
            "user_id": "alice",
            "beamline": "11.0.1",
            "key": "k",
            "value": [1, 2],
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    c = _client()
    c.set("k", [1, 2], beamline="11.0.1")


def test_set_raises_on_5xx(httpx_mock):
    from lightfall.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/x",
        status_code=500,
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.set("x", "y")


def test_set_raises_on_network_error(httpx_mock):
    from lightfall.settings.user_settings_client import UserSettingsError

    httpx_mock.add_exception(httpx.ConnectError("boom"))
    c = _client()
    with pytest.raises(UserSettingsError):
        c.set("x", "y")


def test_delete_succeeds(httpx_mock):
    httpx_mock.add_response(
        method="DELETE",
        url="https://lb.test/logbook/settings/theme?beamline=",
        status_code=204,
    )
    c = _client()
    c.delete("theme")


def test_delete_raises_on_404(httpx_mock):
    from lightfall.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        method="DELETE",
        url=re.compile(r"https://lb\.test/logbook/settings/missing.*"),
        status_code=404,
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.delete("missing")
```

- [ ] **Step 2: Run; new tests fail (AttributeError)**

```bash
.venv/Scripts/python -m pytest tests/test_user_settings_client.py -v
```

- [ ] **Step 3: Implement `set` and `delete`**

Append to `UserSettingsClient` in `src/lightfall/settings/user_settings_client.py`:

```python
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
```

- [ ] **Step 4: Run; all green**

```bash
.venv/Scripts/python -m pytest tests/test_user_settings_client.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/settings/user_settings_client.py tests/test_user_settings_client.py
git commit -m "feat(settings): add UserSettingsClient.set and .delete"
```

---

## Task 10: `upload_image` + `image_url` helpers

**Files:**
- Modify: `src/lightfall/settings/user_settings_client.py`
- Modify: `tests/test_user_settings_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_user_settings_client.py`:

```python
def test_upload_image_returns_id(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://lb.test/logbook/images",
        json={"image_id": "abc-123", "mime_type": "image/png", "size_bytes": 42},
        status_code=201,
    )
    c = _client()
    image_id = c.upload_image(b"\x89PNG fake bytes", "image/png")
    assert image_id == "abc-123"


def test_upload_image_raises_on_4xx(httpx_mock):
    from lightfall.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        method="POST",
        url="https://lb.test/logbook/images",
        status_code=400,
        json={"detail": "too big"},
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.upload_image(b"x" * 10, "image/png")


def test_image_url_builds_absolute():
    c = _client()
    assert (
        c.image_url("abc-123")
        == "https://lb.test/logbook/images/abc-123"
    )


def test_download_image_returns_bytes_and_mime(httpx_mock):
    httpx_mock.add_response(
        url="https://lb.test/logbook/images/img-1",
        content=b"BYTES",
        headers={"content-type": "image/png"},
    )
    c = _client()
    data, mime = c.download_image("img-1")
    assert data == b"BYTES"
    assert mime == "image/png"


def test_download_image_raises_on_404(httpx_mock):
    from lightfall.settings.user_settings_client import UserSettingsError

    httpx_mock.add_response(
        url="https://lb.test/logbook/images/missing",
        status_code=404,
    )
    c = _client()
    with pytest.raises(UserSettingsError):
        c.download_image("missing")
```

- [ ] **Step 2: Run; new tests fail**

```bash
.venv/Scripts/python -m pytest tests/test_user_settings_client.py -v
```

- [ ] **Step 3: Implement**

Append to `UserSettingsClient`:

```python
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
        except (httpx.HTTPError, KeyError) as e:
            raise UserSettingsError(f"Image upload failed: {e}") from e

    def download_image(self, image_id: str) -> tuple[bytes, str]:
        """GET /logbook/images/{id}; return (bytes, content_type).

        Used by clients that want raw image bytes (e.g., a worker thread
        decoding into a QImage)."""
        try:
            with self._client() as c:
                r = c.get(f"/logbook/images/{image_id}")
            r.raise_for_status()
            return r.content, r.headers.get("content-type", "")
        except httpx.HTTPError as e:
            raise UserSettingsError(
                f"Image download failed for {image_id!r}: {e}"
            ) from e

    def image_url(self, image_id: str) -> str:
        """Build the absolute URL for an image (e.g., for QPixmap loaders
        that handle their own auth)."""
        return f"{self._base_url}/logbook/images/{image_id}"
```

- [ ] **Step 4: Run; all green**

```bash
.venv/Scripts/python -m pytest tests/test_user_settings_client.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/settings/user_settings_client.py tests/test_user_settings_client.py
git commit -m "feat(settings): add UserSettingsClient.upload_image and image_url"
```

---

# Phase 4 — `UserProfileSettingsPlugin`

## Task 11: Plugin skeleton with metadata + identity labels

**Files:**
- Create: `src/lightfall/ui/preferences/user_profile_settings.py`
- Test: `tests/ui/test_user_profile_plugin.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_user_profile_plugin.py
"""UI-side tests for UserProfileSettingsPlugin.

Uses pytest-qt's qtbot fixture and a stubbed Session so the widget can be
constructed without a real auth backend. UserSettingsClient calls are
intercepted via the singleton's reset/init pattern + httpx_mock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@dataclass
class _StubUser:
    username: str = "rpandolfi"
    display_name: str = "Ron Pandolfi"
    email: str = "rp@lbl.gov"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class _StubSession:
    user: _StubUser = field(default_factory=_StubUser)


@pytest.fixture(autouse=True)
def _reset_settings_client():
    from lightfall.settings.user_settings_client import UserSettingsClient
    UserSettingsClient.reset()
    UserSettingsClient.init(base_url="https://lb.test")
    yield
    UserSettingsClient.reset()


@pytest.fixture
def stub_session(monkeypatch):
    """Patch SessionManager.get_instance() to return a stub session."""
    from lightfall.auth import session as session_mod

    sm = MagicMock()
    sm.session = _StubSession()
    monkeypatch.setattr(
        session_mod.SessionManager, "get_instance", classmethod(lambda cls: sm)
    )
    return sm


def test_plugin_metadata():
    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    assert p.name == "user_profile"
    assert p.display_name == "User Profile"
    assert p.category == "general"
    assert p.priority == 1


def test_create_widget_shows_identity_labels(qtbot, stub_session):
    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    text = w.findChildren(type(w))  # silence unused-import
    # Walk all QLabels and assert username/email/display name appear
    from PySide6.QtWidgets import QLabel
    label_text = " ".join(
        lbl.text() for lbl in w.findChildren(QLabel)
    )
    assert "rpandolfi" in label_text
    assert "rp@lbl.gov" in label_text
    assert "Ron Pandolfi" in label_text


def test_orcid_row_hidden_when_absent(qtbot, stub_session):
    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )

    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    from PySide6.QtWidgets import QLabel
    label_text = " ".join(lbl.text() for lbl in w.findChildren(QLabel))
    assert "ORCID" not in label_text


def test_orcid_row_shown_when_present(qtbot, monkeypatch):
    from lightfall.auth import session as session_mod
    user = _StubUser(attributes={"orcid": "0000-0001-2345-6789"})
    sm = MagicMock()
    sm.session = _StubSession(user=user)
    monkeypatch.setattr(
        session_mod.SessionManager, "get_instance", classmethod(lambda cls: sm)
    )

    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)

    from PySide6.QtWidgets import QLabel
    label_text = " ".join(lbl.text() for lbl in w.findChildren(QLabel))
    assert "0000-0001-2345-6789" in label_text
```

Note: this assumes `lightfall.auth.session.SessionManager.get_instance()` exists. If the actual API differs, adjust the patch target — but the same shape of test is correct.

- [ ] **Step 2: Run; fails on import**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

- [ ] **Step 3: Implement the plugin**

```python
# src/lightfall/ui/preferences/user_profile_settings.py
"""Settings plugin for the per-user profile picture (and identity preview).

MVP scope: the user can view their identity (read-only labels), upload a
new profile image, or remove the current one. All actions commit
immediately on user input — there is no Apply/Cancel buffering — because
the work has non-trivial network side-effects whose rollback semantics
on Cancel would be ugly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


_AVATAR_PX = 128


class UserProfileSettingsPlugin(SettingsPlugin):
    """Profile picture + identity preview."""

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._avatar_label: QLabel | None = None

    @property
    def name(self) -> str:
        return "user_profile"

    @property
    def display_name(self) -> str:
        return "User Profile"

    @property
    def icon(self) -> "QIcon | None":
        return None

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 1  # under Appearance (0), above Login & Session (5)

    # ── Widget ───────────────────────────────────────────────────────────

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        outer.addLayout(row)

        # Avatar on the left
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(_AVATAR_PX, _AVATAR_PX)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_label.setStyleSheet(
            "QLabel { border: 1px solid palette(mid); border-radius: 8px; }"
        )
        self._set_placeholder_avatar()
        row.addWidget(self._avatar_label)

        # Identity labels on the right
        ident_box = QGroupBox("Identity")
        ident_form = QFormLayout(ident_box)

        username, display_name, email, orcid = self._read_identity()
        ident_form.addRow("Username:", QLabel(username))
        ident_form.addRow("Display name:", QLabel(display_name))
        ident_form.addRow("Email:", QLabel(email))
        if orcid:
            ident_form.addRow("ORCID:", QLabel(orcid))
        row.addWidget(ident_box, stretch=1)

        # Buttons row
        buttons = QHBoxLayout()
        self._choose_button = QPushButton("Choose Image…")
        self._remove_button = QPushButton("Remove Image")
        self._choose_button.clicked.connect(self._on_choose_clicked)
        self._remove_button.clicked.connect(self._on_remove_clicked)
        buttons.addWidget(self._choose_button)
        buttons.addWidget(self._remove_button)
        buttons.addStretch()
        outer.addLayout(buttons)

        outer.addWidget(QLabel(
            "Supported: PNG, JPEG, GIF · max 20 MB"
        ))
        outer.addStretch()

        self._widget = widget
        return widget

    # ── Lifecycle (noop bodies for now; later tasks fill in) ─────────────

    def load_settings(self) -> None:
        return None  # Task 12 implements

    def save_settings(self) -> None:
        # Commit-on-action design: nothing to do here.
        return None

    def validate(self) -> list[str]:
        return []

    # ── Stubs the later tasks replace ────────────────────────────────────

    def _on_choose_clicked(self) -> None:
        return None  # Task 13

    def _on_remove_clicked(self) -> None:
        return None  # Task 14

    # ── Helpers ──────────────────────────────────────────────────────────

    def _read_identity(self) -> tuple[str, str, str, str | None]:
        """Pull (username, display_name, email, orcid) from the current session."""
        try:
            from lightfall.auth.session import SessionManager
            sess = SessionManager.get_instance().session
            if sess is None or sess.user is None:
                return ("(not logged in)", "", "", None)
            user = sess.user
            orcid = (user.attributes or {}).get("orcid") if hasattr(
                user, "attributes"
            ) else None
            return (
                user.username,
                user.display_name,
                user.email,
                orcid,
            )
        except Exception as e:
            logger.debug("Could not read identity from session: {}", e)
            return ("(unknown)", "", "", None)

    def _set_placeholder_avatar(self) -> None:
        """Show a blank silhouette placeholder."""
        if self._avatar_label is None:
            return
        pm = QPixmap(_AVATAR_PX, _AVATAR_PX)
        pm.fill(Qt.GlobalColor.lightGray)
        self._avatar_label.setPixmap(pm)
```

Add `tests/ui/__init__.py` if it doesn't exist:

```bash
ls tests/ui/__init__.py 2>/dev/null || (mkdir -p tests/ui && touch tests/ui/__init__.py)
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/preferences/user_profile_settings.py tests/ui/test_user_profile_plugin.py tests/ui/__init__.py
git commit -m "feat(prefs): scaffold UserProfileSettingsPlugin with identity labels"
```

---

## Task 12: `load_settings` — fetch and display the current avatar

**Files:**
- Modify: `src/lightfall/ui/preferences/user_profile_settings.py`
- Modify: `tests/ui/test_user_profile_plugin.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/ui/test_user_profile_plugin.py`:

```python
def test_load_settings_no_image_keeps_placeholder(qtbot, stub_session, httpx_mock):
    """When no profile_image_id is set, load_settings leaves placeholder."""
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        status_code=404,
    )

    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()
    # Spin the event loop briefly to let the worker thread finish (or be
    # absent if no image_id).
    qtbot.wait(100)
    # Placeholder pixmap exists, label is not empty
    assert p._avatar_label is not None
    assert not p._avatar_label.pixmap().isNull()


def test_load_settings_with_image_fetches_bytes(
    qtbot, stub_session, httpx_mock, monkeypatch
):
    """When an image_id is set, the bytes are fetched and rendered."""
    image_bytes = _png_bytes_1x1_red()
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        json={
            "user_id": "rpandolfi",
            "beamline": "",
            "key": "profile_image_id",
            "value": "img-1",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )
    httpx_mock.add_response(
        url="https://lb.test/logbook/images/img-1",
        content=image_bytes,
        headers={"content-type": "image/png"},
    )

    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p.load_settings()

    # Wait for the worker thread to deliver the QImage to the GUI
    qtbot.waitUntil(
        lambda: p._loaded_image_id == "img-1",
        timeout=5000,
    )
    assert not p._avatar_label.pixmap().isNull()


def _png_bytes_1x1_red() -> bytes:
    import struct, zlib

    def chunk(t, d):
        return (
            struct.pack(">I", len(d))
            + t + d
            + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        )

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\x00\x00")
    idat = chunk(b"IDAT", raw)
    iend = chunk(b"IEND", b"")
    return header + ihdr + idat + iend
```

- [ ] **Step 2: Run; new tests fail**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

- [ ] **Step 3: Implement `load_settings` with worker-thread image fetch**

In `src/lightfall/ui/preferences/user_profile_settings.py`, replace the `load_settings` stub and add helpers:

```python
    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._avatar_label: QLabel | None = None
        self._loaded_image_id: str | None = None
        self._load_future = None  # QThreadFuture

    def load_settings(self) -> None:
        """Fetch the current profile_image_id and render the avatar."""
        from lightfall.settings.user_settings_client import UserSettingsClient
        from lightfall.utils.threads import QThreadFuture

        client = UserSettingsClient.get_instance()
        image_id = client.get("profile_image_id", default=None)
        if not image_id:
            self._set_placeholder_avatar()
            self._loaded_image_id = None
            return

        # Fetch the bytes on a worker thread, decode to QImage there,
        # then convert to QPixmap on the GUI thread (signal slot).
        self._load_future = QThreadFuture(
            _fetch_qimage,
            client,
            image_id,
            callback_slot=lambda qimg: self._on_image_ready(image_id, qimg),
            except_slot=lambda exc: self._on_image_error(exc),
        )
        self._load_future.start()

    def _on_image_ready(self, image_id: str, qimage) -> None:
        from PySide6.QtGui import QPixmap
        if qimage is None or qimage.isNull():
            self._set_placeholder_avatar()
            return
        pm = QPixmap.fromImage(qimage).scaled(
            _AVATAR_PX,
            _AVATAR_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self._avatar_label is not None:
            self._avatar_label.setPixmap(pm)
        self._loaded_image_id = image_id

    def _on_image_error(self, exc: BaseException) -> None:
        logger.warning("Failed to load profile image: {}", exc)
        self._set_placeholder_avatar()
        self._loaded_image_id = None
```

Add at module scope (below the class):

```python
def _fetch_qimage(client, image_id: str):
    """Worker-thread function: download bytes via the client, decode to QImage.

    QImage is safe to construct off the GUI thread; QPixmap is not.
    """
    from PySide6.QtGui import QImage

    data, _ = client.download_image(image_id)
    img = QImage()
    img.loadFromData(data)
    return img
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/preferences/user_profile_settings.py tests/ui/test_user_profile_plugin.py
git commit -m "feat(prefs): UserProfileSettingsPlugin.load_settings fetches avatar"
```

---

## Task 13: "Choose Image…" action

**Files:**
- Modify: `src/lightfall/ui/preferences/user_profile_settings.py`
- Modify: `tests/ui/test_user_profile_plugin.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/ui/test_user_profile_plugin.py`:

```python
ALLOWED_MIMES = {"image/png", "image/jpeg", "image/gif"}
MAX_BYTES = 20 * 1024 * 1024


def test_choose_image_happy_path(
    qtbot, stub_session, httpx_mock, tmp_path, monkeypatch
):
    """Selecting a small valid PNG uploads it and writes profile_image_id."""
    png_path = tmp_path / "me.png"
    png_path.write_bytes(_png_bytes_1x1_red())

    # Patch QFileDialog to return the prepared path
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(png_path), "Images (*.png *.jpg *.gif)")),
    )

    httpx_mock.add_response(
        method="POST",
        url="https://lb.test/logbook/images",
        json={"image_id": "new-id", "mime_type": "image/png", "size_bytes": len(_png_bytes_1x1_red())},
        status_code=201,
    )
    httpx_mock.add_response(
        method="PUT",
        url="https://lb.test/logbook/settings/profile_image_id",
        match_json={"value": "new-id", "beamline": ""},
        json={
            "user_id": "rpandolfi",
            "beamline": "",
            "key": "profile_image_id",
            "value": "new-id",
            "updated_at": "2026-04-30T00:00:00+00:00",
        },
    )

    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p._on_choose_clicked()
    qtbot.waitUntil(lambda: p._loaded_image_id == "new-id", timeout=5000)


def test_choose_image_rejects_too_large(
    qtbot, stub_session, tmp_path, monkeypatch
):
    """Files over 20 MB are rejected client-side with no upload attempted."""
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG" + b"\x00" * (MAX_BYTES + 1))

    from PySide6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(big), "Images (*.png)")),
    )
    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **kw: shown.append(a) or QMessageBox.StandardButton.Ok),
    )

    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p._on_choose_clicked()
    assert shown, "Expected QMessageBox.warning to be shown"


def test_choose_image_rejects_unknown_mime(
    qtbot, stub_session, tmp_path, monkeypatch
):
    """Files whose mime can't be determined or isn't allowed → warning, no upload."""
    bad = tmp_path / "thing.bmp"
    bad.write_bytes(b"BM" + b"\x00" * 100)

    from PySide6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **kw: (str(bad), "Images (*.bmp)")),
    )
    shown = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **kw: shown.append(a) or QMessageBox.StandardButton.Ok),
    )

    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    p._on_choose_clicked()
    assert shown
```

- [ ] **Step 2: Run; tests fail**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

- [ ] **Step 3: Implement choose-image flow**

In `src/lightfall/ui/preferences/user_profile_settings.py`, replace `_on_choose_clicked` and add helpers:

```python
_ALLOWED_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


    def _on_choose_clicked(self) -> None:
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        path_str, _ = QFileDialog.getOpenFileName(
            self._widget,
            "Choose profile image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif)",
        )
        if not path_str:
            return

        path = Path(path_str)
        ext = path.suffix.lower()
        mime = _ALLOWED_MIMES.get(ext)
        if mime is None:
            QMessageBox.warning(
                self._widget,
                "Unsupported file type",
                f"{ext or '(no extension)'} is not a supported image type. "
                "Please choose a PNG, JPEG, or GIF.",
            )
            return

        try:
            data = path.read_bytes()
        except OSError as e:
            QMessageBox.warning(
                self._widget, "Cannot read file", f"Could not read {path}: {e}"
            )
            return

        if len(data) > _MAX_IMAGE_BYTES:
            QMessageBox.warning(
                self._widget,
                "File too large",
                f"{path.name} is {len(data) // (1024*1024)} MB — limit is "
                f"{_MAX_IMAGE_BYTES // (1024*1024)} MB.",
            )
            return

        self._upload_and_set(data, mime)

    def _upload_and_set(self, data: bytes, mime: str) -> None:
        """Upload image, set profile_image_id, refresh avatar."""
        from PySide6.QtWidgets import QMessageBox

        from lightfall.settings.user_settings_client import (
            UserSettingsClient,
            UserSettingsError,
        )
        from lightfall.utils.threads import QThreadFuture

        client = UserSettingsClient.get_instance()

        def work():
            image_id = client.upload_image(data, mime)
            client.set("profile_image_id", image_id)
            return image_id

        def on_ok(image_id: str):
            # Re-trigger load to pull and display the new image.
            self.load_settings()

        def on_err(exc: BaseException):
            logger.warning("Profile image upload failed: {}", exc)
            QMessageBox.warning(
                self._widget,
                "Upload failed",
                f"Could not save profile image: {exc}",
            )

        self._upload_future = QThreadFuture(
            work,
            callback_slot=on_ok,
            except_slot=on_err,
        )
        self._upload_future.start()
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/preferences/user_profile_settings.py tests/ui/test_user_profile_plugin.py
git commit -m "feat(prefs): wire Choose Image action with size and mime pre-checks"
```

---

## Task 14: "Remove Image" action

**Files:**
- Modify: `src/lightfall/ui/preferences/user_profile_settings.py`
- Modify: `tests/ui/test_user_profile_plugin.py`

- [ ] **Step 1: Write failing test**

Append to `tests/ui/test_user_profile_plugin.py`:

```python
def test_remove_image_clears_setting(qtbot, stub_session, httpx_mock):
    httpx_mock.add_response(
        method="DELETE",
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        status_code=204,
    )
    # After delete, load_settings does a GET that 404s → placeholder
    httpx_mock.add_response(
        url="https://lb.test/logbook/settings/profile_image_id?beamline=",
        status_code=404,
    )

    from lightfall.ui.preferences.user_profile_settings import (
        UserProfileSettingsPlugin,
    )
    p = UserProfileSettingsPlugin()
    w = p.create_widget()
    qtbot.addWidget(w)
    # Pretend an image was loaded
    p._loaded_image_id = "old"
    p._on_remove_clicked()
    qtbot.waitUntil(lambda: p._loaded_image_id is None, timeout=5000)
```

- [ ] **Step 2: Run; fails**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py::test_remove_image_clears_setting -v
```

- [ ] **Step 3: Implement remove**

Replace `_on_remove_clicked` in `src/lightfall/ui/preferences/user_profile_settings.py`:

```python
    def _on_remove_clicked(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from lightfall.settings.user_settings_client import (
            UserSettingsClient,
            UserSettingsError,
        )
        from lightfall.utils.threads import QThreadFuture

        client = UserSettingsClient.get_instance()

        def work():
            try:
                client.delete("profile_image_id")
            except UserSettingsError as e:
                # If the setting wasn't there, treat as success.
                if "404" in str(e) or "Not Found" in str(e):
                    return
                raise

        def on_ok(_):
            self.load_settings()

        def on_err(exc):
            logger.warning("Profile image removal failed: {}", exc)
            QMessageBox.warning(
                self._widget,
                "Remove failed",
                f"Could not remove profile image: {exc}",
            )

        self._remove_future = QThreadFuture(
            work,
            callback_slot=on_ok,
            except_slot=on_err,
        )
        self._remove_future.start()
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/preferences/user_profile_settings.py tests/ui/test_user_profile_plugin.py
git commit -m "feat(prefs): wire Remove Image action"
```

---

## Task 15: Register the plugin

**Files:**
- Modify: `src/lightfall/plugins/builtin_manifest.py`
- Modify: `src/lightfall/ui/preferences/__init__.py`

The manifest is a Python module of `PluginEntry` objects, not toml/json. The existing settings entries live between roughly lines 42–114 of `builtin_manifest.py`.

- [ ] **Step 1: Add a `PluginEntry` for `UserProfileSettingsPlugin`**

In `src/lightfall/plugins/builtin_manifest.py`, insert a new entry alongside the other settings plugins (e.g., right after the "Login & Session settings" entry):

```python
        # User Profile settings
        PluginEntry(
            type_name="settings",
            name="user_profile",
            import_path="lightfall.ui.preferences.user_profile_settings:UserProfileSettingsPlugin",
        ),
```

- [ ] **Step 2: Re-export from the package `__init__.py`**

Edit `src/lightfall/ui/preferences/__init__.py`:

```python
"""User preferences management for NCS."""
from lightfall.ui.preferences.builtin import AppearanceSettingsPlugin
from lightfall.ui.preferences.device_settings import DeviceSettingsPlugin
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.ui.preferences.user_profile_settings import UserProfileSettingsPlugin

__all__ = [
    "AppearanceSettingsPlugin",
    "DeviceSettingsPlugin",
    "PreferencesDialog",
    "PreferencesManager",
    "UserProfileSettingsPlugin",
]
```

- [ ] **Step 3: Verify the plugin is discovered**

```bash
.venv/Scripts/python -c "from lightfall.plugins.builtin_manifest import builtin_manifest; print([p.name for p in builtin_manifest.plugins if p.type_name == 'settings'])"
```

Expected: a list containing `'user_profile'` alongside `'appearance'`, `'login'`, etc.

- [ ] **Step 4: Run the full test suite**

```bash
.venv/Scripts/python -m pytest -q
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/builtin_manifest.py src/lightfall/ui/preferences/__init__.py
git commit -m "feat(prefs): register UserProfileSettingsPlugin in builtin manifest"
```

---

## Task 16: End-to-end smoke + cleanup

**Files:** none (manual verification + branch push)

- [ ] **Step 1: Run the backend locally**

In one shell:

```bash
cd ~/PycharmProjects/ncs/lightfall-logbook
.venv/Scripts/python -m uvicorn lightfall_logbook.app:app --reload --port 8000
```

- [ ] **Step 2: Point Lightfall at it**

In Lightfall's Preferences (existing UI), set `Logbook URL` to `http://localhost:8000` if not already.

- [ ] **Step 3: Manual smoke**

1. Launch Lightfall, log in.
2. Open Preferences → User Profile.
3. Verify identity labels show your username, display name, email (and ORCID if your token has it).
4. Click "Choose Image…", pick a PNG. Verify the avatar updates inline.
5. Close and reopen Preferences. Verify the avatar still appears (proves the round-trip is real).
6. Click "Choose Image…" again with a different image. Confirm the avatar updates and the previous file in the server's `IMAGE_STORAGE_DIR` is gone (`ls` it).
7. Click "Remove Image". Confirm avatar reverts to the placeholder, and the image file on the server is removed.

- [ ] **Step 4: Push the Lightfall branch**

```bash
cd ~/PycharmProjects/ncs/ncs
git push -u origin feature/user-profile-settings
```

- [ ] **Step 5: Final commit (if anything was tweaked during smoke)**

If the smoke testing surfaced fix-ups, commit them with a `fix(prefs): ...` message and push.

---

## Done criteria (mirrors spec §9)

- New `user_settings` table exists in `lightfall-logbook` (Task 1).
- Four endpoints mounted, authenticated, tenant-scoped (Tasks 2–4).
- Profile-pic write hook deletes the previous image bytes on update (Task 4).
- `UserSettingsClient` exists with `get/set/delete/get_all/upload_image/image_url` (Tasks 8–10).
- `SessionAuth` and `get_logbook_base_url` are factored as in spec §4.1 and used by both clients (Tasks 6–7).
- `UserProfileSettingsPlugin` registered, appears in the Preferences dialog under General between Appearance and Login & Session, and supports load/choose/remove (Tasks 11–15).
- Backend test suites and Lightfall-side test suites pass (every task ends in `pytest -q` clean).

# User Settings & Profile-Picture SettingsPlugin — Design

**Date:** 2026-04-30
**Status:** Approved (brainstorming complete; pending implementation plan)
**Repos affected:** `lucid-logbook` (backend), `ncs/ncs` (LUCID client)

## 1. Context

LUCID currently stores user preferences locally via `PreferencesManager`
(QSettings + ConfigManager). All preferences are machine-local, with a
hard-coded `BEAMLINE_SPECIFIC_PREFS` set deciding which keys can be
overridden per beamline.

We want a second tier of preferences that follow the *user* across
machines: server-side, keyed by Keycloak `sub`, scoped either globally or
per-beamline. The lucid-logbook backend is the natural host — it already
has Keycloak JWT middleware, per-user data (`Logbook.user_id == sub`),
file storage (`ImageStore`), and a deployed instance the LUCID client
already talks to.

The MVP for this feature is a single concrete user setting — a profile
picture — exercised end-to-end through a new "User Profile" settings
plugin. The backend KV infrastructure built to support it is general
enough that subsequent user-scoped settings (e.g., display-name override,
favorite beamlines, per-user UI prefs that need to roam) need no new
schema, just new keys.

## 2. Decisions Summary

| Decision | Choice |
|---|---|
| Identity key | Keycloak `sub` only (matches existing `Logbook.user_id`). No separate `users` table. |
| Backend storage | Generic KV table `user_settings(user_id, beamline, key, value)`, `beamline=''` for global. |
| LUCID-side surface | New `UserSettingsClient` singleton; plugins call it directly. No `PreferencesManager` integration in this MVP. |
| Profile-pic upload | Two-step: existing `POST /logbook/images` then `PUT /logbook/settings/profile_image_id`. No new convenience endpoint. |
| MVP UI surface | The settings plugin only. Avatar widgets in chrome / login chip / entry author byline are explicitly out of scope. |

## 3. Backend Design (`lucid-logbook`)

### 3.1 Schema

New SQLAlchemy ORM model in `src/lucid_logbook/models.py`:

```python
class UserSettingRow(Base):
    __tablename__ = "user_settings"

    user_id:    Mapped[str]        = mapped_column(String(256), nullable=False)
    beamline:   Mapped[str]        = mapped_column(String(64),  nullable=False, default="")
    key:        Mapped[str]        = mapped_column(String(128), nullable=False)
    value:      Mapped[Any]        = mapped_column(JSON,        nullable=False)
    updated_at: Mapped[datetime]   = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (PrimaryKeyConstraint("user_id", "beamline", "key"),)
```

Notes:
- `beamline=''` is the canonical "global" sentinel. Compound PKs with
  nullable columns have inconsistent semantics across SQLite/Postgres;
  using an empty string keeps `(user_id, beamline, key)` a clean PK.
- `value` is JSON, so the same table holds strings, ints, dicts, lists.
- Table is created via `Base.metadata.create_all()` on app start, like the
  existing tables. (A migration toolchain is out of scope; if/when
  alembic shows up in this repo, this table joins the migration set.)

### 3.2 Pydantic schemas

```python
class UserSettingWrite(BaseModel):
    value: Any
    beamline: str = ""

class UserSettingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: str
    beamline: str
    key: str
    value: Any
    updated_at: datetime
```

### 3.3 API surface

New `SettingsController` in `src/lucid_logbook/api.py`, mounted at
`/logbook/settings`:

| Method | Path | Query | Body | Response |
|---|---|---|---|---|
| `GET` | `/logbook/settings` | `beamline` (default `""`) | — | `{key: value, …}` for the requesting user in that scope |
| `GET` | `/logbook/settings/{key}` | `beamline` (default `""`) | — | `UserSettingSchema` or 404 |
| `PUT` | `/logbook/settings/{key}` | — | `UserSettingWrite` | `UserSettingSchema` |
| `DELETE` | `/logbook/settings/{key}` | `beamline` (default `""`) | — | 204 |

All endpoints derive `user_id` from `request.state.user_id` (set by
`KeycloakAuthMiddleware`). No path or body parameter ever overrides this;
cross-user reads/writes are not possible by construction.

`PUT` uses upsert semantics: insert if `(user_id, beamline, key)` row
absent, otherwise update `value` and `updated_at`.

### 3.4 Profile-pic write hook

`PUT /logbook/settings/profile_image_id` triggers a small server-side
hook: if a previous `profile_image_id` row exists for this user, the old
image's bytes are deleted via `ImageStore.delete(old_id)` after the row
is updated. Failure to delete the old file is **logged but does not fail
the request** — orphan blobs are recoverable; a failed write is more
disruptive. The hook is implemented as a tiny key→callable registry on
the controller so the orthogonality of "settings" vs "images" is
preserved (the settings table doesn't know about images; the hook does).

This hook only fires for `profile_image_id` in the MVP. Other keys go
through the unhooked path.

### 3.5 Image visibility

The existing `GET /logbook/images/{id}` endpoint requires Bearer auth.
That is the right level for profile pictures: any authenticated LUCID
user can fetch any avatar by id, which matches the use case (avatars are
shown to other users). No change to the image controller is needed for
this MVP.

## 4. LUCID-Side Design (`ncs/ncs`)

### 4.1 Shared HTTP plumbing (small refactor)

`lucid/logbook/client.py` currently defines `_SessionAuth(httpx.Auth)`
and reads `prefs.get("logbook_url", …)`. The new client needs both. Two
small in-scope cleanups:

- Move `_SessionAuth` to `lucid/auth/httpx_auth.py` (rename to
  `SessionAuth`) and import it from both `LogbookClient` and
  `UserSettingsClient`.
- Extract a `lucid.logbook.url.get_logbook_base_url()` helper that
  encapsulates the `prefs.get("logbook_url", default)` lookup, used by
  both clients.

These are tiny, targeted, and improve the code we're already touching.

### 4.2 `UserSettingsClient`

New module: `lucid/settings/user_settings_client.py`. The package name is
`lucid.settings` rather than `lucid.logbook.settings` — the endpoint
happens to live on the logbook server today, but conceptually user
settings is not a logbook concern, and we don't want every import site
to change if it ever moves to its own service.

```python
class UserSettingsError(Exception): ...

class UserSettingsClient:
    @classmethod
    def get_instance(cls) -> "UserSettingsClient": ...
    @classmethod
    def init(cls, base_url: str | None = None) -> None:
        """Initialize the singleton. base_url=None falls back to
        get_logbook_base_url() (§4.1)."""

    # Key/value API
    def get(self, key: str, default: Any = None,
            *, beamline: str | None = None) -> Any: ...
    def set(self, key: str, value: Any,
            *, beamline: str | None = None) -> None: ...
    def delete(self, key: str, *, beamline: str | None = None) -> None: ...
    def get_all(self, *, beamline: str | None = None) -> dict[str, Any]: ...

    # Image-backed setting helpers (general; first consumer is profile pic)
    def upload_image(self, data: bytes, mime_type: str) -> str: ...
    def image_url(self, image_id: str) -> str: ...
```

Implementation notes:
- Sync `httpx.Client`, reusing `SessionAuth` from §4.1. Settings reads
  and writes happen at dialog-open and on user action; no async needed.
- `beamline=None` on the API maps to `beamline=""` on the wire.
- `get(key, default=…)` swallows 404 and `httpx` connection errors and
  returns the default. `set/delete` raise `UserSettingsError` on
  non-2xx or network failure (the caller needs to know).
- `upload_image` POSTs multipart to `/logbook/images` and returns the
  server's `image_id`.
- `image_url(image_id)` builds an absolute URL — useful for any pixmap
  loader that wants to fetch through `httpx` with the same auth.

### 4.3 No `PreferencesManager` integration

Plugins that need user-scoped settings call
`UserSettingsClient.get_instance()` explicitly. This keeps the boundary
honest (`PreferencesManager` = local; `UserSettingsClient` = server) and
avoids hiding network errors behind a sync `prefs.set()`. A unifying
facade can come later if it earns its keep.

## 5. `UserProfileSettingsPlugin`

New module: `lucid/ui/preferences/user_profile_settings.py`. Exported
from `lucid/ui/preferences/__init__.py` and registered in the plugin
manifest with the other settings plugins.

### 5.1 Plugin metadata

| Field | Value |
|---|---|
| `name` | `user_profile` |
| `display_name` | `User Profile` |
| `category` | `general` |
| `priority` | `1` (under Appearance, above Login & Session) |

### 5.2 Widget layout

Built in `create_widget`:

```
┌──────────────────────────────────────────┐
│  ┌─────────┐                             │
│  │         │  Username:    rpandolfi     │
│  │ 128×128 │  Display name: Ron Pandolfi │
│  │ avatar  │  Email:       rp@lbl.gov    │
│  └─────────┘  ORCID:       (if present)  │
│  [ Choose Image… ]  [ Remove Image ]     │
│                                          │
│  Supported: PNG, JPEG, GIF · max 20 MB   │
└──────────────────────────────────────────┘
```

The right-hand fields are **read-only labels** sourced from
`session.user.username`, `session.user.display_name`,
`session.user.email`, and `session.user.attributes.get("orcid")`. The
ORCID row is hidden when the claim is absent. No edit affordances in the
MVP.

### 5.3 Lifecycle

- **`load_settings()`** — read `profile_image_id` from
  `UserSettingsClient.get("profile_image_id", default=None)`. If present,
  on a worker thread (`lucid.utils.threads.QThreadFuture`) fetch the
  bytes from `client.image_url(image_id)` and decode into a `QImage`;
  marshal the result back to the GUI thread, where it is converted to a
  `QPixmap`, scaled to 128×128 with rounded corners, and set on the
  avatar label. (`QPixmap` must not be constructed off the GUI thread;
  `QImage` is safe.) If absent or any error, show a placeholder
  silhouette.
- **"Choose Image…"** — `QFileDialog` filtered to png/jpg/gif, then on a
  worker thread: pre-validate size (`< 20 MB`) and mime-type; call
  `client.upload_image(bytes, mime)` to get `image_id`; call
  `client.set("profile_image_id", image_id)`; update the preview. Old
  image cleanup is the server's job (§3.4).
- **"Remove Image"** — `client.delete("profile_image_id")`; preview
  reverts to placeholder.
- **`save_settings()`** — no-op. The plugin commits **immediately on
  user action** rather than on Apply, because the work has non-trivial
  network side-effects whose rollback semantics on Cancel would be ugly.
  A one-line code comment notes this.
- **`validate()`** — returns `[]`.
- **`apply_preview` / `revert_preview`** — not implemented.

### 5.4 Client-side pre-checks

Before upload, the plugin enforces the same constraints as the server
(`ALLOWED_MIME_TYPES`, `MAX_IMAGE_SIZE`) and shows a `QMessageBox` on
violation. This is a UX improvement, not a security boundary — the
server still validates.

## 6. Errors

### 6.1 Backend

- Missing/invalid `user_id` from middleware → `NotAuthorizedException`
  (the existing pattern in `_get_user_id`).
- `PUT` body that fails `UserSettingWrite` validation →
  `ValidationException`.
- `GET /settings/{key}` for nonexistent → `NotFoundException`.
- Failure to delete a replaced `profile_image_id` blob → logged at
  WARN, request still succeeds.

### 6.2 LUCID client

- `UserSettingsClient.get(key, default=…)` swallows 404 and connection
  errors and returns the default.
- `UserSettingsClient.set/delete` raise `UserSettingsError` on non-2xx
  or network failure.
- The plugin catches `UserSettingsError` around its commit-on-action
  paths and shows a `QMessageBox` with the error; the avatar preview
  reverts to its prior state.

## 7. Testing

### 7.1 Backend (`lucid-logbook/tests/`)

- `test_settings.py`: PUT/GET/DELETE round-trip, global vs beamline
  scope, upsert semantics on duplicate PUT, cross-user tenancy
  (user A cannot read/write user B's rows), unknown-key 404, malformed
  body 400.
- `test_user_profile_flow.py`: upload image → set
  `profile_image_id` → upload second image → set again → first image's
  bytes gone, second image's bytes remain. Old-image-delete failure
  path: simulate `image_store.delete` raising; verify the PUT still
  succeeds and the warning is logged.

### 7.2 LUCID client (`ncs/ncs/tests/`)

- `test_user_settings_client.py`: every method against an
  `httpx.MockTransport`. Verify the swallow-on-default behavior of
  `get` for 404 and connection errors; verify `set/delete` raise
  `UserSettingsError` appropriately.
- `test_user_profile_plugin.py`: `load_settings` with no image →
  placeholder; `load_settings` with image_id → fetch+scale path runs;
  Choose-Image happy path; oversized file → `QMessageBox`, no upload;
  wrong mime → `QMessageBox`; network failure during set → preview
  reverts. Use `UserSettingsClient`'s mock transport plus a stub
  `Session`.

## 8. Out of Scope (Deferred Follow-ups)

- Avatar rendering anywhere outside the plugin (login chip, sidebar,
  entry author byline). Each is a separate vertical slice.
- Display-name / email override settings.
- A `PreferencesManager` facade unifying local and user-scoped settings.
- Local cache of last-known user-setting values for offline reads.
- Cross-realm or ORCID-primary identity migration. (Today: `sub` only.
  If the realm changes, settings are orphaned with the old `sub`. We
  accept this risk — same risk Logbook itself carries today.)
- Alembic migrations for `lucid-logbook`. Today the table is created
  via `Base.metadata.create_all`, like every other table in the repo.

## 9. Acceptance Criteria

The feature is done when:

1. A new `user_settings` table exists in `lucid-logbook` with the schema
   in §3.1.
2. The four endpoints in §3.3 are mounted, authenticated, and tenant-
   scoped.
3. The profile-pic write hook in §3.4 deletes the previous image's
   bytes on update.
4. `UserSettingsClient` exists in `ncs/ncs` and exposes the API in §4.2.
5. `_SessionAuth` and the logbook base-URL lookup are factored as in §4.1
   and used by both `LogbookClient` and `UserSettingsClient`.
6. `UserProfileSettingsPlugin` is registered, appears in the
   Preferences dialog under "General" between Appearance and Login &
   Session, and supports the three actions (load, choose, remove).
7. The tests in §7.1 and §7.2 pass.

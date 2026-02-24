# User Settings Sync — Design Plan

**Status:** Draft / Not Started  
**Date:** 2026-02-24  
**Author:** Ayaka + Ron  

## Problem

LUCID uses `QSettings` (backed by registry on Windows, `.conf` on Linux) for all preferences. This is machine-local and user-agnostic — when multiple users share a workstation (common at beamlines), they all get the same settings. When a user moves between machines, their preferences don't follow.

## Goal

Per-user settings that sync across machines, while keeping machine-specific settings local.

## Settings Tiers

Not all settings should sync. Split into three tiers:

### Machine-Local (stays in QSettings)
- Window geometry & layout
- Network proxy configuration
- Theme (dark/light) — may vary by monitor
- Status bar visibility
- Local file paths (logbook db, etc.)

### User Preferences (sync per-user)
- Default beamline / endstation
- Favorited devices
- Notification preferences
- IPython console history
- Claude assistant: always-allowed tools
- Logbook: default tags, templates
- Panel layout preferences (which panels auto-open)

### Shared / Beamline Config (separate concern)
- Tiled server URL
- Logbook server URL
- Device catalog source
- Keycloak realm/server
- These are deployment config, not user settings — handled by environment variables, config files, or a future admin panel

## Recommended Approach: Extend lucid-logbook

### Why lucid-logbook?

1. **Offline-first sync already solved** — `LogbookClient` handles local SQLite + background sync to server. Same pattern applies directly.
2. **Auth already integrated** — Keycloak token is passed to sync requests. User ID is available from `SessionManager`.
3. **No new infrastructure** — just a new table and a few API endpoints on the existing Litestar backend.
4. **Guest fallback works** — guests get local-only settings (no sync), same as logbook entries today.

### Backend Changes (lucid-logbook)

New table:

```sql
CREATE TABLE user_settings (
    user_id     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,  -- JSON-encoded
    updated_at  TEXT NOT NULL,  -- ISO 8601
    PRIMARY KEY (user_id, key)
);
```

New endpoints:

```
GET    /api/users/{user_id}/settings          → all settings for user
GET    /api/users/{user_id}/settings/{key}    → single setting
PUT    /api/users/{user_id}/settings/{key}    → upsert setting
DELETE /api/users/{user_id}/settings/{key}    → delete setting
POST   /api/users/{user_id}/settings/sync     → bulk sync (same pattern as logbook)
```

Auth: require valid Keycloak token, enforce `user_id` matches token subject (users can only read/write their own settings).

### Client Changes (LUCID)

#### New: `SettingsClient`

Mirrors `LogbookClient` structure:

```python
class SettingsClient:
    """Offline-first user settings with optional remote sync."""
    
    # Local SQLite table: user_settings (same schema as server)
    # On login: pull remote settings, merge with local
    # On change: write local immediately, schedule sync
    # On logout: keep local cache (for next login)
    
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> None: ...
    def get_all(self) -> dict[str, Any]: ...
    def schedule_sync(self) -> None: ...  # debounced, like LogbookClient
```

- Local db path: `~/.lucid/user_settings.db`
- Values stored as JSON strings
- Sync uses same `QThreadFuture` + debounce timer pattern as logbook

#### Modified: `PreferencesManager`

Add a tier parameter to preference registration:

```python
class SettingsTier(Enum):
    MACHINE = "machine"  # QSettings (current behavior)
    USER = "user"        # SettingsClient (synced)

class PreferencesManager:
    def __init__(self):
        self._qsettings = QSettings(...)       # machine-local
        self._settings_client = SettingsClient()  # user-synced
    
    def get(self, key: str, *, tier: SettingsTier = SettingsTier.MACHINE) -> Any:
        if tier == SettingsTier.USER:
            return self._settings_client.get(key)
        return self._qsettings.value(key)
    
    def set(self, key: str, value: Any, *, tier: SettingsTier = SettingsTier.MACHINE) -> None:
        if tier == SettingsTier.USER:
            self._settings_client.set(key, value)
        else:
            self._qsettings.setValue(key, value)
```

Settings plugins would declare their tier:

```python
# In a settings plugin
SETTINGS_SCHEMA = {
    "proxy_host": {"tier": SettingsTier.MACHINE, "default": "localhost"},
    "favorite_devices": {"tier": SettingsTier.USER, "default": []},
}
```

### Sync Flow

```
Login:
  1. SessionManager emits user_changed(user)
  2. SettingsClient.set_user(user.id)
  3. Pull remote settings → merge into local SQLite
  4. Emit settings_loaded signal
  5. PreferencesManager refreshes user-tier values

Setting changed:
  1. PreferencesManager.set(key, value, tier=USER)
  2. SettingsClient writes to local SQLite immediately
  3. SettingsClient.schedule_sync() (2s debounce)
  4. Background thread pushes to server

Logout:
  1. SettingsClient flushes pending sync
  2. Local cache remains (user's settings persist for next login)

Offline:
  1. All reads/writes go to local SQLite
  2. Sync retries on reconnection (same as logbook)
```

### Merge Strategy

When pulling remote settings after login:

- **Remote wins for most settings** — the server is the source of truth
- **Local wins for machine-tier** — these never sync
- **Conflict resolution:** Last-write-wins based on `updated_at` timestamp
- **First login on new machine:** Remote settings populate local cache

### Migration Path

1. **Phase 1:** Add `user_settings` table + endpoints to lucid-logbook backend
2. **Phase 2:** Create `SettingsClient` in LUCID (offline-first, local-only initially)
3. **Phase 3:** Wire `SettingsClient` into `PreferencesManager` with tier system
4. **Phase 4:** Migrate individual settings to `USER` tier one at a time
5. **Phase 5:** Enable sync (connect `SettingsClient` to server)

Each phase is independently deployable. Phase 2-3 can ship without the backend — settings just stay local until the server is available.

## Open Questions

- **Settings UI:** Should user-tier settings show a sync indicator (cloud icon)?
- **Admin override:** Should beamline admins be able to set default user preferences? (e.g., "all users on BL 7.0.1 default to these notification settings")
- **Export/import:** Allow users to export/import their settings as JSON? Useful for debugging and onboarding.
- **Size limits:** Do we need to cap per-user storage? Probably not a concern with simple key-value settings.
- **Encryption:** Any settings that should be encrypted at rest? API keys probably shouldn't sync at all.

## Non-Goals

- This plan does NOT cover shared/beamline configuration (Tiled URL, device catalog, etc.). That's a deployment concern.
- This plan does NOT replace `QSettings` entirely — machine-local settings remain there.
- No real-time sync between multiple active sessions (eventual consistency via polling is fine).

# Profile avatar in menubar corner + PreferencesManager backend unification

**Date:** 2026-05-11
**Author:** Ron / Ayaka

## Motivation

Two related problems:

1. **Visible:** Lightfall lets a user upload a profile picture from the
   Preferences dialog, but there is no glanceable indicator that the
   user is signed in or which user. The profile picture should appear
   in the menubar corner, next to the RunEngine controls, and update
   whenever the user changes it.

2. **Underlying:** `UserSettingsClient` was added as a sibling surface
   to `PreferencesManager`, not as a backend of it. As a result, the
   two have separate read/write APIs and separate change-notification
   shapes (`UserSettingsClient` has no signal at all today). A widget
   that just wants "the current profile_image_id, and tell me when it
   changes" has to know which surface stores which key — and there is
   no observation mechanism on the server-portable surface.

The fix for (2) makes (1) tidy: `PreferencesManager` becomes the single
preference surface, with pluggable storage backends, and a per-key
subscription API. The avatar widget subscribes to one key and otherwise
knows nothing about how it's stored.

## Scope

- Introduce a `PreferenceBackend` ABC and split storage into two
  concrete backends: `LocalPreferenceBackend` (YAML via `ConfigManager`)
  and `UserPortableBackend` (lightfall-logbook via `UserSettingsClient`).
- Replace `PreferencesManager`'s coarse `preference_changed` signal with
  a topic-gated `subscribe(key, slot)` / `unsubscribe(key, slot)` API.
- Add a `ProfileAvatarWidget` in the menubar corner, right of the
  RunEngine controls.
- Migrate `UserProfileSettingsPlugin` to talk to `PreferencesManager`
  instead of `UserSettingsClient` directly.
- Migrate the single existing `preference_changed` consumer to
  `subscribe`.

Out of scope:

- Consolidating the two paths reaching `/logbook/images`
  (`UserSettingsClient.upload_image`/`download_image` vs. raw httpx in
  `lightfall.logbook.client._run_sync`). Noted as future work.
- Cross-machine preference invalidation (two Lightfall instances under one
  account observing each other's writes). Noted as future work.

## Architecture

```
[caller code]
     │
     ▼
PreferencesManager  ──── subscribe(key, slot) / unsubscribe(key, slot)
     │
     ├── _backend_for(key):                         (per-key Topic ──▶ slot)
     │     for b in self._backends:
     │         if b.owns(key): return b
     │     return self._local   # default
     │
     ▼
PreferenceBackend (ABC)
     │
     ├── LocalPreferenceBackend        ──► ConfigManager (YAML)
     │      changed(key, value) ─────────────────────────────────┐
     │                                                            │
     └── UserPortableBackend            ──► UserSettingsClient ──►│ HTTP
            cache: dict[str, Any]                                  │
            changed(key, value) ─────────────────────────────────┤
                                                                   ▼
                                            PreferencesManager._on_backend_changed
                                                                   │
                                                                   ▼
                                                       per-key _Topic.changed.emit
                                                                   │
                                                                   ▼
                                                              subscribed slots
```

Backends own their own threading. `LocalPreferenceBackend.set/.remove`
return synchronously after writing YAML, then emit `changed`.
`UserPortableBackend.set/.remove` return immediately; the HTTP call runs
on a `QThreadFuture`; the cache update + `changed` emission happen on
the GUI thread via the future's `callback_slot`.

## The `PreferenceBackend` ABC

```python
# src/lightfall/ui/preferences/backend.py
from abc import abstractmethod
from typing import Any
from PySide6.QtCore import QObject, Signal


class PreferenceBackend(QObject):
    """Storage backend for `PreferencesManager`.

    Backends own their own threading. `set()`/`remove()` return
    immediately and emit `changed` when the store reflects the new
    value. `get()` is a best-effort synchronous read — backends that
    need I/O may return a cached value or `default`. `refresh()` is an
    optional async pull hook (no-op by default).

    `owns()` is on the hot path (called on every get/set/remove). It
    must be O(1) — precompute any membership structure (set, prefix
    trie, etc.) at __init__ time.
    """

    changed = Signal(str, object)   # key, value (None on removal)

    @abstractmethod
    def owns(self, key: str) -> bool:
        """True if this backend is the canonical store for `key`."""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Return the cached/local value, or `default`."""

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        """Persist `value` for `key`; emit `changed(key, value)` on success."""

    @abstractmethod
    def remove(self, key: str) -> None:
        """Remove `key`; emit `changed(key, None)` on success."""

    def refresh(self) -> None:
        """Optional: re-pull known keys, emitting `changed` per moved key.
        No-op by default."""
        return None
```

### `LocalPreferenceBackend`

Lives in `backend.py` alongside the ABC.

- `__init__(config_manager, beamline=None)` — keeps a reference to the
  passed `ConfigManager`; computes its complement set of user-portable
  keys once for fast `owns()`.
- `owns(key)` returns `key not in USER_PORTABLE_KEYS` (imported from
  `user_portable_backend`).
- `get(key, default)` mirrors the current beamline-aware lookup in
  `manager.py` (beamline-specific keys consult
  `preferences.beamlines.{beamline}.{key}` first, then fall back to
  `preferences.{key}`).
- `set(key, value)` writes via `ConfigManager.set(..., persist=True)`
  then `self.changed.emit(key, value)`.
- `remove(key)` writes `None` via `ConfigManager.set(..., persist=True)`
  then `self.changed.emit(key, None)`.

This backend subsumes today's `PreferencesManager.set`/`.remove`
behavior plus its `preference_changed` emit calls.

### `UserPortableBackend`

```python
# src/lightfall/ui/preferences/user_portable_backend.py
USER_PORTABLE_KEYS: frozenset[str] = frozenset({"profile_image_id"})
```

- `__init__(client: UserSettingsClient)` — caches `USER_PORTABLE_KEYS`
  as `self._keys`; initializes an empty `dict[str, Any]` cache and an
  `_inflight_sets: set[str]` guard.
- `owns(key)` returns `key in self._keys`.
- `get(key, default)` returns `self._cache.get(key, default)` — no I/O.
- `set(key, value)`:
  1. `self._inflight_sets.add(key)`.
  2. Start a `QThreadFuture(lambda: self._client.set(key, value))`.
  3. `callback_slot` on success: `self._cache[key] = value`;
     `self._inflight_sets.discard(key)`; `self.changed.emit(key, value)`.
  4. `except_slot`: `self._inflight_sets.discard(key)`; log warning.
- `remove(key)`: analogous, with `self._client.delete(key)` and
  emission of `(key, None)` on success.
- `refresh()`:
  1. Start a `QThreadFuture(self._client.get_all)`.
  2. On success, walk the returned dict; for every key the backend
     owns, compare against the cache. For each key that moved (and
     that does *not* appear in `_inflight_sets`), update the cache and
     emit `changed`. Keys that are owned but missing from the response
     are emitted as `changed(key, None)`.
  3. On failure, log and leave the cache untouched.

The `_inflight_sets` guard means a slow `refresh` returning stale data
will not clobber a write that is in flight.

## PreferencesManager refactor

```python
# src/lightfall/ui/preferences/manager.py
class _Topic(QObject):
    """One signal per subscribed key. Created lazily."""
    changed = Signal(object)  # value (None on removal)


class PreferencesManager(QObject):
    _instance: "PreferencesManager | None" = None
    _lock = threading.RLock()

    def __init__(self, config_manager=None, beamline=None):
        super().__init__()
        self._local = LocalPreferenceBackend(config_manager, beamline)
        self._user_portable = UserPortableBackend(UserSettingsClient.get_instance())
        # User-portable checked first so its owns() wins for declared keys
        self._backends: tuple[PreferenceBackend, ...] = (
            self._user_portable, self._local,
        )
        self._topics: dict[str, _Topic] = {}
        for b in self._backends:
            b.changed.connect(self._on_backend_changed)
        self._settings = QSettings("ALS", "NCS")  # unchanged

    # ── Public API ──────────────────────────────────────────────────

    def get(self, key, default=None):
        return self._backend_for(key).get(key, default)

    def set(self, key, value, *, persist=True):
        # persist retained for back-compat; user-portable backend ignores it
        self._backend_for(key).set(key, value)

    def remove(self, key):
        self._backend_for(key).remove(key)

    def subscribe(self, key: str, slot: Callable[[Any], None]) -> None:
        """Subscribe `slot(value)` to changes for `key`.

        Slot is invoked once per change, only when this specific key
        changes — not for every preference write. Connect/disconnect
        from the GUI thread; slot is delivered on the GUI thread.
        """
        topic = self._topics.get(key)
        if topic is None:
            topic = _Topic(self)
            self._topics[key] = topic
        topic.changed.connect(slot)

    def unsubscribe(self, key: str, slot: Callable[[Any], None]) -> None:
        topic = self._topics.get(key)
        if topic is not None:
            topic.changed.disconnect(slot)

    def refresh_user_portable_keys(self) -> None:
        self._user_portable.refresh()

    # ── Internal ────────────────────────────────────────────────────

    def _backend_for(self, key: str) -> PreferenceBackend:
        for b in self._backends:
            if b.owns(key):
                return b
        return self._local

    @Slot(str, object)
    def _on_backend_changed(self, key: str, value: Any) -> None:
        topic = self._topics.get(key)
        if topic is not None:
            topic.changed.emit(value)
```

Notes:

- The coarse `preference_changed` signal is **removed**. The single
  existing consumer (`NCSMainWindow._on_preference_changed`) is
  migrated to `subscribe("theme", ...)`.
- `set_beamline`, `save_window_state`, recent-files management, and
  the `QSettings` wrapping all stay unchanged — they're outside the
  backend split.
- `_Topic` objects are parented to `PreferencesManager` so they share
  its thread affinity (GUI) and lifetime. Slots delivered on GUI
  thread regardless of where `changed.emit` was called from.

## ProfileAvatarWidget

```
src/lightfall/ui/widgets/profile_avatar.py
```

- `QWidget` subclass, fixed size (28x28 px), circular crop via
  `QPainter` clip path. Cursor: pointing hand. Tooltip: the user's
  display name (read via `SessionManager.get_instance().user`).
- Placeholder icon (themed silhouette) shown until the cache reports a
  `profile_image_id`.
- `clicked = Signal()` emitted on `mousePressEvent` (left button only).
- On construction:
  - `prefs.subscribe("profile_image_id", self._on_image_id_changed)`.
  - `image_id = prefs.get("profile_image_id")`.
  - If `image_id`, kick off `_fetch_and_swap(image_id)`; otherwise leave
    the placeholder. `refresh_user_portable_keys()` (called post-login)
    will populate later and the subscription will fire.
- `_fetch_and_swap(image_id)` uses the same `QThreadFuture +
  _fetch_qimage` helper currently in `user_profile_settings.py` (moved
  to a shared location: `lightfall.settings.image_helpers._fetch_qimage`).
- Tracks `self._loaded_image_id`; short-circuits if an incoming
  `image_id` equals the current one.

## NCSMainWindow integration

In `_setup_menus`, replace:

```python
self._re_control = RunEngineControlWidget()
menubar.setCornerWidget(self._re_control, Qt.Corner.TopRightCorner)
```

with:

```python
corner = QWidget()
corner_layout = QHBoxLayout(corner)
corner_layout.setContentsMargins(0, 0, 0, 0)
corner_layout.setSpacing(8)
self._re_control = RunEngineControlWidget()
self._profile_avatar = ProfileAvatarWidget()
self._profile_avatar.clicked.connect(self._on_preferences)
corner_layout.addWidget(self._re_control)
corner_layout.addWidget(self._profile_avatar)
menubar.setCornerWidget(corner, Qt.Corner.TopRightCorner)
```

Migrate the theme listener in `_connect_signals`:

```python
# before:
self._prefs_manager.preference_changed.connect(self._on_preference_changed)
# after:
self._prefs_manager.subscribe("theme", self._on_theme_changed)
```

with the slot signature dropping the `key` argument:

```python
@Slot(object)
def _on_theme_changed(self, value: Any) -> None:
    self._theme_manager.set_theme_by_name(str(value))
```

After login completes, call
`self._prefs_manager.refresh_user_portable_keys()` so the avatar (and
any open prefs dialog) populates from the server. The hook point is
wherever the existing post-login chain runs in `mainwindow.py`'s login
slot.

## UserProfileSettingsPlugin migration

`src/lightfall/ui/preferences/user_profile_settings.py`:

- `load_settings`: replace
  `UserSettingsClient.get_instance().get("profile_image_id", default=None)`
  with `PreferencesManager.get_instance().get("profile_image_id", default=None)`.
  Reads still return instantly — now from the cache.
- `_upload_and_set`:
  - Continue calling `UserSettingsClient.get_instance().upload_image(data, mime)`
    directly — that's a blob upload, not a setting.
  - Replace `client.set("profile_image_id", image_id)` with
    `PreferencesManager.get_instance().set("profile_image_id", image_id)`.
- `_on_remove_clicked`: replace the underlying `client.delete(...)` call
  with `PreferencesManager.get_instance().remove("profile_image_id")`.
- The plugin keeps its dialog-local avatar rendering, but the trigger
  to re-render becomes `prefs.subscribe("profile_image_id",
  self._on_image_id_changed)` rather than the plugin re-calling
  `load_settings` from its own `on_ok` callback. This removes a
  duplicated render path.

## Threading

- `UserPortableBackend.set/remove/refresh` use `QThreadFuture`. The
  blocking httpx work runs off-thread; the cache update + `changed.emit`
  run on the GUI thread via `callback_slot`.
- Because all emission ends up on the GUI thread, subscribers receive
  callbacks on the GUI thread, so widget slots may touch QWidget state
  freely.
- `subscribe`/`unsubscribe` must be called from the GUI thread (they
  attach Qt signal connections). Documented in the docstring.

## Testing

New tests:

- `tests/test_preference_backend.py`
  - `LocalPreferenceBackend.owns/get/set/remove`
  - `changed` emits with correct `(key, value)` on set and `(key, None)`
    on remove
  - Beamline override path: backend respects `set_beamline`-supplied
    namespace
- `tests/test_user_portable_backend.py` (uses `pytest-httpx`)
  - `set` runs work on a future and emits `changed` on success
  - `set` failure does not update cache and does not emit
  - `remove` emits `(key, None)` on success
  - `refresh` populates cache and emits per moved key, including
    deletions (keys that disappeared from the server)
  - `refresh` does not clobber a key with an in-flight `set`
- `tests/test_preferences_manager.py` (augment existing)
  - `subscribe`/`unsubscribe` route only the subscribed key
  - Multiple subscribers per key all fire
  - `_Topic` lazy creation; identity preserved across resubscribe
  - `get`/`set`/`remove` dispatch to the right backend
- `tests/ui/test_profile_avatar_widget.py` (pytest-qt)
  - Initial render is placeholder
  - On `subscribe` fire with non-None id, fetch helper is invoked
  - On `subscribe` fire with same id, fetch helper is *not* invoked
  - On `subscribe` fire with None, reverts to placeholder
  - Mouse press emits `clicked`

Touched tests:

- `tests/ui/test_user_profile_plugin.py` — update assertions: plugin
  now calls `PreferencesManager.set/remove/get`, not `UserSettingsClient`.
- `tests/test_user_settings_client.py` — no change; the client is
  unchanged.

## Migration / file inventory

| File | Action |
|---|---|
| `src/lightfall/ui/preferences/backend.py` | new — `PreferenceBackend` ABC + `LocalPreferenceBackend` |
| `src/lightfall/ui/preferences/user_portable_backend.py` | new — `UserPortableBackend` + `USER_PORTABLE_KEYS` |
| `src/lightfall/ui/preferences/manager.py` | edit — multiplex backends, `subscribe`/`unsubscribe`/`_Topic`, drop `preference_changed`, add `refresh_user_portable_keys` |
| `src/lightfall/settings/image_helpers.py` | new — extracts the `_fetch_qimage` helper currently in `user_profile_settings.py` so the avatar widget can reuse it |
| `src/lightfall/ui/widgets/profile_avatar.py` | new — `ProfileAvatarWidget` |
| `src/lightfall/ui/mainwindow.py` | edit — corner widget wraps RE controls + avatar; theme listener uses `subscribe`; post-login `refresh_user_portable_keys` |
| `src/lightfall/ui/preferences/user_profile_settings.py` | edit — `PreferencesManager` for set/remove/get; subscribe to `"profile_image_id"`; drop the manual re-load callback |
| `tests/test_preference_backend.py` | new |
| `tests/test_user_portable_backend.py` | new |
| `tests/test_preferences_manager.py` | edit |
| `tests/ui/test_profile_avatar_widget.py` | new |
| `tests/ui/test_user_profile_plugin.py` | edit |

## Risks and mitigations

1. **`refresh` vs. concurrent `set` race.** Mitigated by
   `_inflight_sets` guard on `UserPortableBackend` — refresh skips any
   key currently being written.
2. **Subscribe-from-non-GUI-thread.** Out of contract; documented in
   the docstring. The `_Topic` QObjects live on the GUI thread, so
   cross-thread `connect` would create the slot connection on the
   wrong thread.
3. **`USER_PORTABLE_KEYS` static.** New user-portable keys require a
   code change. Acceptable for the foreseeable future (we expect a
   small handful). Schema-driven membership is a follow-up if we ever
   have tens of these.
4. **Class name `UserSettingsClient` overpromises.** After this PR it
   is functionally a blob client (`upload_image`/`download_image`/
   `image_url`) plus internal-only key/value methods used by
   `UserPortableBackend`. Renaming is intentionally out of scope here;
   doing it cleanly requires unifying with the parallel httpx caller
   in `lightfall.logbook.client._run_sync` first.

## Future work

- Consolidate the two callers of `/logbook/images` (`UserSettingsClient`
  vs. raw httpx in `lightfall.logbook.client._run_sync`) under a shared
  `lightfall.logbook.blob_client`. Then rename `UserSettingsClient` to
  reflect its remaining responsibility.
- Cross-instance preference change notification (server-pushed or
  poll-based) so two Lightfall processes under one account see each other's
  preference writes.
- Schema-driven `USER_PORTABLE_KEYS` so new keys can be added without
  editing the backend module.

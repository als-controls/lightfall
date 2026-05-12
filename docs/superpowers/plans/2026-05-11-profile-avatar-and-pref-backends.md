# Profile Avatar + PreferencesManager Backend Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the user's profile picture in the menubar corner (right of RunEngine controls), reacting live to changes; refactor `PreferencesManager` into a backend-multiplexing surface with topic-gated subscriptions so the avatar (and future consumers) observes one key without polling or filtering.

**Architecture:** Introduce a `PreferenceBackend` ABC with two concretes — `LocalPreferenceBackend` (YAML via `ConfigManager`) and `UserPortableBackend` (lucid-logbook via `UserSettingsClient`). `PreferencesManager` becomes a thin multiplexer; per-key `_Topic` QObjects deliver `subscribe(key, slot)` callbacks. The coarse `preference_changed` signal is removed. The new `ProfileAvatarWidget` subscribes to `"profile_image_id"`.

**Tech Stack:** PySide6 (Qt6), httpx (sync), pytest + pytest-qt + pytest-httpx, `lucid.utils.threads.QThreadFuture` for off-thread HTTP.

**Spec:** `docs/superpowers/specs/2026-05-11-profile-avatar-and-pref-backends-design.md`

**Branch:** `feature/profile-avatar-pref-backends` (spec already committed).

**Test runner:** `.venv/Scripts/python -m pytest <path> -v` (never bare `pytest`).

---

## File inventory

| Action | File |
|---|---|
| new | `src/lucid/settings/image_helpers.py` |
| new | `src/lucid/ui/preferences/backend.py` |
| new | `src/lucid/ui/preferences/user_portable_backend.py` |
| edit | `src/lucid/ui/preferences/manager.py` |
| new | `src/lucid/ui/widgets/profile_avatar.py` |
| edit | `src/lucid/ui/mainwindow.py` |
| edit | `src/lucid/ui/preferences/user_profile_settings.py` |
| new | `tests/test_image_helpers.py` |
| new | `tests/test_preference_backend.py` |
| new | `tests/test_user_portable_backend.py` |
| edit | `tests/test_preferences_manager.py` |
| new | `tests/ui/widgets/test_profile_avatar.py` |
| edit | `tests/ui/test_user_profile_plugin.py` |

---

## Task 1: Extract `_fetch_qimage` helper

**Why first:** Both the existing settings plugin and the new avatar widget need this helper. Lifting it before touching either consumer keeps the diff for each later task small.

**Files:**
- Create: `src/lucid/settings/image_helpers.py`
- Test: `tests/test_image_helpers.py`

- [ ] **Step 1: Read the current `_fetch_qimage` to copy verbatim**

Run: read `src/lucid/ui/preferences/user_profile_settings.py` lines 260-340. The helper is defined near the bottom of the file. Copy the exact body for the next step.

- [ ] **Step 2: Write the failing test**

Create `tests/test_image_helpers.py`:

```python
"""Tests for image helpers used by both the profile dialog and avatar widget."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


def test_fetch_qimage_returns_qimage_for_png_bytes(qapp):
    """_fetch_qimage downloads via the client and decodes bytes to QImage."""
    from lucid.settings.image_helpers import _fetch_qimage

    # Smallest valid PNG: 1x1 transparent.
    png = bytes.fromhex(
        "89504E470D0A1A0A0000000D49484452"
        "00000001000000010806000000"
        "1F15C4890000000A49444154789C636000000000020001E221BC330000000049454E44AE426082"
    )
    client = MagicMock()
    client.download_image.return_value = (png, "image/png")

    image = _fetch_qimage(client, "img-abc")
    assert isinstance(image, QImage)
    assert not image.isNull()
    client.download_image.assert_called_once_with("img-abc")


def test_fetch_qimage_returns_null_image_on_garbage(qapp):
    from lucid.settings.image_helpers import _fetch_qimage

    client = MagicMock()
    client.download_image.return_value = (b"not an image", "image/png")
    image = _fetch_qimage(client, "img-bad")
    assert isinstance(image, QImage)
    assert image.isNull()
```

- [ ] **Step 3: Run the failing test**

```
.venv/Scripts/python -m pytest tests/test_image_helpers.py -v
```

Expected: `ModuleNotFoundError: No module named 'lucid.settings.image_helpers'`.

- [ ] **Step 4: Create the helper module**

Create `src/lucid/settings/image_helpers.py`:

```python
"""Pure helpers shared across UI code that displays lucid-logbook image
artifacts (profile picture, fragment images, etc.).

Keep this module dependency-free of QWidget — it must be safe to import
from worker threads.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from lucid.settings.user_settings_client import UserSettingsClient


def _fetch_qimage(client: "UserSettingsClient", image_id: str) -> QImage:
    """Download `image_id` via `client` and decode the bytes into a QImage.

    Designed to run on a worker thread; the returned QImage is
    thread-safe to pass back to the GUI thread (do the QPixmap
    conversion there).

    Returns a null QImage (image.isNull() == True) on any decode
    failure. HTTP failures propagate as UserSettingsError.
    """
    data, _content_type = client.download_image(image_id)
    image = QImage()
    image.loadFromData(data)
    return image
```

- [ ] **Step 5: Run the test — verify pass**

```
.venv/Scripts/python -m pytest tests/test_image_helpers.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Update `user_profile_settings.py` to import from the new module**

In `src/lucid/ui/preferences/user_profile_settings.py`, delete the in-file `_fetch_qimage` definition (the function near the bottom) and add at the top of the file (with other imports):

```python
from lucid.settings.image_helpers import _fetch_qimage
```

- [ ] **Step 7: Run the user-profile-plugin tests to confirm no regression**

```
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

Expected: every test still passes (the helper moved; behavior is unchanged).

- [ ] **Step 8: Commit**

```
cd C:/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/settings/image_helpers.py src/lucid/ui/preferences/user_profile_settings.py tests/test_image_helpers.py
git commit -m "refactor(settings): extract _fetch_qimage into shared image_helpers module"
```

---

## Task 2: `PreferenceBackend` ABC + `LocalPreferenceBackend`

**Why now:** The ABC defines the contract every later task depends on. `LocalPreferenceBackend` reproduces today's `PreferencesManager` behavior (the bit that talks to `ConfigManager`) inside the new shape, so we can swap the manager's internals without changing observable behavior for local keys.

**Files:**
- Create: `src/lucid/ui/preferences/backend.py`
- Test: `tests/test_preference_backend.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preference_backend.py`:

```python
"""Tests for PreferenceBackend ABC + LocalPreferenceBackend."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def config_manager():
    """A MagicMock-backed ConfigManager that records get/set calls."""
    cm = MagicMock()
    cm._store: dict = {}

    def _get(key, default=None):
        return cm._store.get(key, default)

    def _set(key, value, persist=True):
        cm._store[key] = value

    cm.get.side_effect = _get
    cm.set.side_effect = _set
    return cm


def test_abc_cannot_be_instantiated(qapp):
    from lucid.ui.preferences.backend import PreferenceBackend
    with pytest.raises(TypeError):
        PreferenceBackend()


def test_local_backend_owns_non_portable_key(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    assert b.owns("theme") is True
    assert b.owns("font_size") is True


def test_local_backend_does_not_own_user_portable_key(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    assert b.owns("profile_image_id") is False


def test_local_backend_get_returns_stored_value(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.theme"] = "dark"
    b = LocalPreferenceBackend(config_manager)
    assert b.get("theme") == "dark"


def test_local_backend_get_returns_default_when_missing(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    assert b.get("missing", default="fallback") == "fallback"


def test_local_backend_set_emits_changed(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    b = LocalPreferenceBackend(config_manager)
    captured: list[tuple] = []
    b.changed.connect(lambda k, v: captured.append((k, v)))

    b.set("theme", "evangelion")

    assert config_manager._store["preferences.theme"] == "evangelion"
    assert captured == [("theme", "evangelion")]


def test_local_backend_remove_emits_none(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.theme"] = "dark"
    b = LocalPreferenceBackend(config_manager)
    captured: list[tuple] = []
    b.changed.connect(lambda k, v: captured.append((k, v)))

    b.remove("theme")

    assert config_manager._store["preferences.theme"] is None
    assert captured == [("theme", None)]


def test_local_backend_beamline_override_consulted_first(qapp, config_manager):
    """Beamline-specific keys (e.g., default_data_dir) read beamline first, fall back to global."""
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.default_data_dir"] = "/data/global"
    config_manager._store["preferences.beamlines.7011.default_data_dir"] = "/data/7011"
    b = LocalPreferenceBackend(config_manager, beamline="7011")
    assert b.get("default_data_dir") == "/data/7011"


def test_local_backend_falls_back_when_no_beamline_override(qapp, config_manager):
    from lucid.ui.preferences.backend import LocalPreferenceBackend
    config_manager._store["preferences.default_data_dir"] = "/data/global"
    b = LocalPreferenceBackend(config_manager, beamline="7011")
    assert b.get("default_data_dir") == "/data/global"
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python -m pytest tests/test_preference_backend.py -v
```

Expected: every test fails with `ModuleNotFoundError`.

- [ ] **Step 3: Create the ABC + concrete backend**

Create `src/lucid/ui/preferences/backend.py`:

```python
"""Preference storage backends used by PreferencesManager.

The ABC declares a small, threading-aware contract; PreferencesManager
multiplexes over concrete backends (LocalPreferenceBackend here,
UserPortableBackend in user_portable_backend.py).
"""
from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.config.manager import ConfigManager


# Beamline-specific preference keys. Kept in sync with manager.py's
# BEAMLINE_SPECIFIC_PREFS — see Task 4 for the removal of the
# duplicated set when manager.py imports from here.
BEAMLINE_SPECIFIC_PREFS: frozenset[str] = frozenset({
    "default_data_dir",
    "panel_layout",
    "plot_defaults",
})


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
        """True if this backend is the canonical store for `key`.

        Must be O(1) — called on every get/set/remove."""

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


class LocalPreferenceBackend(PreferenceBackend):
    """YAML-backed preferences via ConfigManager.

    Owns every key that is NOT in USER_PORTABLE_KEYS. Beamline-specific
    keys are looked up under preferences.beamlines.{beamline}.{key}
    with a fallback to preferences.{key}.
    """

    def __init__(
        self,
        config_manager: "ConfigManager | None" = None,
        beamline: str | None = None,
    ) -> None:
        super().__init__()
        self._cm = config_manager
        self._beamline = beamline
        # Cache the user-portable set for O(1) `owns()` rejection.
        # Imported lazily to avoid a circular import with
        # user_portable_backend (which itself imports nothing from here).
        from lucid.ui.preferences.user_portable_backend import (
            USER_PORTABLE_KEYS,
        )
        self._user_portable_keys = USER_PORTABLE_KEYS

    def set_beamline(self, beamline: str | None) -> None:
        self._beamline = beamline

    def owns(self, key: str) -> bool:
        return key not in self._user_portable_keys

    def get(self, key: str, default: Any = None) -> Any:
        if self._cm is None:
            logger.warning("ConfigManager not set, returning default for {}", key)
            return default
        if key in BEAMLINE_SPECIFIC_PREFS and self._beamline:
            override = self._cm.get(
                f"preferences.beamlines.{self._beamline}.{key}"
            )
            if override is not None:
                return override
        return self._cm.get(f"preferences.{key}", default)

    def set(self, key: str, value: Any) -> None:
        if self._cm is None:
            logger.warning("ConfigManager not set, cannot set {}", key)
            return
        if key in BEAMLINE_SPECIFIC_PREFS and self._beamline:
            config_key = f"preferences.beamlines.{self._beamline}.{key}"
        else:
            config_key = f"preferences.{key}"
        self._cm.set(config_key, value, persist=True)
        self.changed.emit(key, value)

    def remove(self, key: str) -> None:
        if self._cm is None:
            return
        self._cm.set(f"preferences.{key}", None, persist=True)
        self.changed.emit(key, None)
```

- [ ] **Step 4: Stub `USER_PORTABLE_KEYS` so the local backend imports it**

Create `src/lucid/ui/preferences/user_portable_backend.py` with just the constant for now (the rest lands in Task 3):

```python
"""User-portable preference backend (lucid-logbook). Populated in Task 3."""
from __future__ import annotations

USER_PORTABLE_KEYS: frozenset[str] = frozenset({"profile_image_id"})
```

- [ ] **Step 5: Run the tests — verify pass**

```
.venv/Scripts/python -m pytest tests/test_preference_backend.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```
git add src/lucid/ui/preferences/backend.py src/lucid/ui/preferences/user_portable_backend.py tests/test_preference_backend.py
git commit -m "feat(prefs): add PreferenceBackend ABC and LocalPreferenceBackend"
```

---

## Task 3: `UserPortableBackend`

**Files:**
- Modify: `src/lucid/ui/preferences/user_portable_backend.py` (fill in beyond the stub)
- Test: `tests/test_user_portable_backend.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_user_portable_backend.py`:

```python
"""Tests for UserPortableBackend.

Uses MagicMock for UserSettingsClient so we can drive set/get/delete
synchronously while the backend's own QThreadFuture handles concurrency.
We block on the `changed` signal via QSignalSpy.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


def _wait_for_spy(spy: QSignalSpy, expected_count: int, timeout_ms: int = 2000) -> None:
    """Pump the event loop until spy has at least `expected_count` items
    or `timeout_ms` elapses. Raises AssertionError on timeout."""
    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.monotonic() + timeout_ms / 1000.0
    while len(spy) < expected_count and time.monotonic() < deadline:
        QCoreApplication.processEvents()
    assert len(spy) >= expected_count, (
        f"expected {expected_count} signal(s), got {len(spy)}"
    )


def test_owns_only_user_portable_keys(qapp):
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    b = UserPortableBackend(client)
    assert b.owns("profile_image_id") is True
    assert b.owns("theme") is False


def test_get_returns_default_when_cache_empty(qapp):
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    b = UserPortableBackend(client)
    assert b.get("profile_image_id", default=None) is None
    assert b.get("profile_image_id", default="x") == "x"


def test_set_runs_async_and_emits_changed(qapp):
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    client.set.return_value = None  # successful PUT

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.set("profile_image_id", "img-1")
    _wait_for_spy(spy, 1)

    assert spy[0][0] == "profile_image_id"
    assert spy[0][1] == "img-1"
    assert b.get("profile_image_id") == "img-1"
    client.set.assert_called_once_with("profile_image_id", "img-1")


def test_set_failure_leaves_cache_untouched_and_no_emit(qapp):
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    from lucid.settings.user_settings_client import UserSettingsError

    client = MagicMock()
    client.set.side_effect = UserSettingsError("boom")

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)
    b.set("profile_image_id", "img-1")

    # Drain the loop a little; should NOT see a signal.
    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert len(spy) == 0
    assert b.get("profile_image_id") is None


def test_remove_emits_none_and_clears_cache(qapp):
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    client.set.return_value = None
    client.delete.return_value = None

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.set("profile_image_id", "img-1")
    _wait_for_spy(spy, 1)
    b.remove("profile_image_id")
    _wait_for_spy(spy, 2)

    assert spy[1][0] == "profile_image_id"
    assert spy[1][1] is None
    assert b.get("profile_image_id") is None


def test_refresh_populates_cache_and_emits_per_changed_key(qapp):
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()
    client.get_all.return_value = {"profile_image_id": "img-7"}

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.refresh()
    _wait_for_spy(spy, 1)

    assert spy[0][0] == "profile_image_id"
    assert spy[0][1] == "img-7"
    assert b.get("profile_image_id") == "img-7"


def test_refresh_emits_none_for_removed_keys(qapp):
    """Server has no profile_image_id but cache had one — emit (key, None)."""
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    client = MagicMock()

    # Seed: server responds with the key, then drops it on second refresh.
    client.get_all.side_effect = [
        {"profile_image_id": "img-1"},
        {},  # disappeared
    ]
    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.refresh()
    _wait_for_spy(spy, 1)
    b.refresh()
    _wait_for_spy(spy, 2)

    assert spy[1][0] == "profile_image_id"
    assert spy[1][1] is None
    assert b.get("profile_image_id") is None


def test_refresh_skips_in_flight_set(qapp, monkeypatch):
    """If a key has an in-flight set, refresh must not clobber it."""
    from lucid.ui.preferences.user_portable_backend import UserPortableBackend
    import time

    client = MagicMock()
    # Make set() slow so we can fire refresh() while it's still running.
    def slow_set(key, value):
        time.sleep(0.3)
    client.set.side_effect = slow_set
    client.get_all.return_value = {"profile_image_id": "stale-server-value"}

    b = UserPortableBackend(client)
    spy = QSignalSpy(b.changed)

    b.set("profile_image_id", "new-value")
    # While slow_set is still running on the worker thread, fire refresh.
    b.refresh()

    # Wait for both futures to complete (set + refresh).
    _wait_for_spy(spy, 1, timeout_ms=3000)

    # After everything settles, the cache must equal the value we wrote,
    # not the stale value the server returned during refresh.
    assert b.get("profile_image_id") == "new-value"
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python -m pytest tests/test_user_portable_backend.py -v
```

Expected: every test fails with `ImportError` or `AttributeError: 'NoneType' object has no attribute 'UserPortableBackend'` (the class doesn't exist yet — only the constant does).

- [ ] **Step 3: Implement the backend**

Replace the contents of `src/lucid/ui/preferences/user_portable_backend.py`:

```python
"""User-portable preference backend (lucid-logbook via UserSettingsClient).

Owns the set of keys whose canonical store is the user's logbook
account (so they follow the user across machines). All I/O is async:
set()/remove()/refresh() return immediately, do their work on a
QThreadFuture, and emit `changed` from the GUI thread on success.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lucid.ui.preferences.backend import PreferenceBackend
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture

if TYPE_CHECKING:
    from lucid.settings.user_settings_client import UserSettingsClient


USER_PORTABLE_KEYS: frozenset[str] = frozenset({"profile_image_id"})


class UserPortableBackend(PreferenceBackend):
    """Caches user-portable keys locally; round-trips writes via HTTP."""

    def __init__(self, client: "UserSettingsClient") -> None:
        super().__init__()
        self._client = client
        self._keys = USER_PORTABLE_KEYS
        self._cache: dict[str, Any] = {}
        # Keys for which a set/remove is in flight. refresh() skips
        # these so a slow server response can't clobber a recent write.
        self._inflight: set[str] = set()
        # Keep refs to futures so GC doesn't kill them mid-flight.
        self._futures: list[QThreadFuture] = []

    # ── Hot-path ────────────────────────────────────────────────────

    def owns(self, key: str) -> bool:
        return key in self._keys

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    # ── Mutations ───────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        self._inflight.add(key)

        def work():
            self._client.set(key, value)
            return value

        f = QThreadFuture(
            work,
            callback_slot=lambda v: self._on_set_ok(key, v),
            except_slot=lambda e: self._on_set_err(key, e),
        )
        self._futures.append(f)
        f.start()

    def remove(self, key: str) -> None:
        self._inflight.add(key)

        def work():
            self._client.delete(key)
            return None

        f = QThreadFuture(
            work,
            callback_slot=lambda _v: self._on_remove_ok(key),
            except_slot=lambda e: self._on_remove_err(key, e),
        )
        self._futures.append(f)
        f.start()

    def refresh(self) -> None:
        def work():
            return self._client.get_all()

        f = QThreadFuture(
            work,
            callback_slot=self._on_refresh_ok,
            except_slot=self._on_refresh_err,
        )
        self._futures.append(f)
        f.start()

    # ── Callbacks (GUI thread) ──────────────────────────────────────

    def _on_set_ok(self, key: str, value: Any) -> None:
        self._inflight.discard(key)
        self._cache[key] = value
        self.changed.emit(key, value)

    def _on_set_err(self, key: str, exc: BaseException) -> None:
        self._inflight.discard(key)
        logger.warning("UserPortableBackend.set({!r}) failed: {}", key, exc)

    def _on_remove_ok(self, key: str) -> None:
        self._inflight.discard(key)
        self._cache.pop(key, None)
        self.changed.emit(key, None)

    def _on_remove_err(self, key: str, exc: BaseException) -> None:
        self._inflight.discard(key)
        logger.warning("UserPortableBackend.remove({!r}) failed: {}", key, exc)

    def _on_refresh_ok(self, all_settings: dict[str, Any]) -> None:
        # Update only keys this backend owns; emit diff against the cache.
        seen: set[str] = set()
        for key, value in all_settings.items():
            if not self.owns(key):
                continue
            seen.add(key)
            if key in self._inflight:
                continue
            if self._cache.get(key) != value:
                self._cache[key] = value
                self.changed.emit(key, value)
        # Keys we knew about but the server no longer reports → removed.
        for key in list(self._cache):
            if key in seen or key in self._inflight:
                continue
            self._cache.pop(key, None)
            self.changed.emit(key, None)

    def _on_refresh_err(self, exc: BaseException) -> None:
        logger.warning("UserPortableBackend.refresh failed: {}", exc)
```

- [ ] **Step 4: Run the tests — verify pass**

```
.venv/Scripts/python -m pytest tests/test_user_portable_backend.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```
git add src/lucid/ui/preferences/user_portable_backend.py tests/test_user_portable_backend.py
git commit -m "feat(prefs): add UserPortableBackend for logbook-backed settings"
```

---

## Task 4: PreferencesManager refactor + `subscribe()` API

**Note:** This task does the full swap in one cohesive change — multiplex backends, introduce `_Topic` and `subscribe`/`unsubscribe`, drop the coarse `preference_changed` signal, migrate the single consumer in `mainwindow.py`. Keeping it atomic prevents an intermediate state where the old signal is gone but the new subscribe API isn't wired.

**Files:**
- Modify: `src/lucid/ui/preferences/manager.py`
- Modify: `src/lucid/ui/mainwindow.py` (migrate the theme listener)
- Modify: `tests/test_preferences_manager.py`

- [ ] **Step 1: Read the existing manager for shape**

Run: open `src/lucid/ui/preferences/manager.py` and skim `__init__`, `get`, `set`, `remove`, `_connect_signals` (if any), and the `BEAMLINE_SPECIFIC_PREFS` set declaration. You'll need to leave non-backend functionality (QSettings, recent files) untouched.

- [ ] **Step 2: Read the existing manager test file**

Run: open `tests/test_preferences_manager.py` and identify tests that connect to `preference_changed` (they'll need migration to `subscribe`). Note the existing fixture pattern.

- [ ] **Step 3: Write the new subscribe tests (failing)**

Append to `tests/test_preferences_manager.py` (or create if missing — adjust path based on the read in Step 2):

```python
def test_subscribe_routes_only_subscribed_key(qapp, prefs_manager):
    """Slot fires only when its key changes — not for other keys."""
    received: list = []
    prefs_manager.subscribe("theme", received.append)

    prefs_manager.set("theme", "dark")
    prefs_manager.set("font_size", 14)

    # Allow queued events to flush.
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()

    assert received == ["dark"]


def test_subscribe_multiple_slots_per_key(qapp, prefs_manager):
    received_a: list = []
    received_b: list = []
    prefs_manager.subscribe("theme", received_a.append)
    prefs_manager.subscribe("theme", received_b.append)

    prefs_manager.set("theme", "evangelion")
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()

    assert received_a == ["evangelion"]
    assert received_b == ["evangelion"]


def test_unsubscribe_stops_delivery(qapp, prefs_manager):
    received: list = []
    prefs_manager.subscribe("theme", received.append)
    prefs_manager.unsubscribe("theme", received.append)

    prefs_manager.set("theme", "dark")
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()

    assert received == []


def test_unsubscribe_for_unknown_key_is_noop(qapp, prefs_manager):
    prefs_manager.unsubscribe("never_subscribed", lambda v: None)
    # No exception → pass.


def test_set_dispatches_to_local_backend_for_local_key(qapp, prefs_manager):
    prefs_manager.set("theme", "dark")
    assert prefs_manager.get("theme") == "dark"


def test_get_returns_cached_user_portable_value(qapp, prefs_manager, monkeypatch):
    """User-portable get is cache-only; populated via refresh or set."""
    from lucid.ui.preferences.manager import PreferencesManager

    # No fetch has happened yet — get returns default.
    assert prefs_manager.get("profile_image_id", default=None) is None


def test_user_portable_set_emits_through_topic(qapp, prefs_manager, monkeypatch):
    """When user-portable backend.set() succeeds, the PrefMgr-level
    subscriber receives the value via the per-key topic."""
    from lucid.ui.preferences import user_portable_backend as upb_mod

    # Replace the backend's client so set() succeeds without HTTP.
    from unittest.mock import MagicMock
    client = MagicMock()
    client.set.return_value = None
    prefs_manager._user_portable._client = client

    received: list = []
    prefs_manager.subscribe("profile_image_id", received.append)

    prefs_manager.set("profile_image_id", "img-42")

    # Wait for the future + queued signal delivery.
    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.monotonic() + 2.0
    while not received and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert received == ["img-42"]
```

For the `prefs_manager` fixture, ensure it constructs `PreferencesManager` with a stub `ConfigManager` and resets between tests. If the existing file already defines such a fixture, reuse it; otherwise add:

```python
@pytest.fixture
def prefs_manager(qapp):
    from unittest.mock import MagicMock
    from lucid.ui.preferences.manager import PreferencesManager

    cm = MagicMock()
    cm._store: dict = {}
    cm.get.side_effect = lambda k, default=None: cm._store.get(k, default)
    cm.set.side_effect = lambda k, v, persist=True: cm._store.__setitem__(k, v)

    PreferencesManager.reset()
    mgr = PreferencesManager(config_manager=cm)
    yield mgr
    PreferencesManager.reset()
```

- [ ] **Step 4: Run the failing tests**

```
.venv/Scripts/python -m pytest tests/test_preferences_manager.py -v
```

Expected: the new subscribe tests fail with `AttributeError: ... has no attribute 'subscribe'`. Existing tests that still reference `preference_changed` will start failing once we delete it — that's expected, we'll migrate them in Step 6.

- [ ] **Step 5: Rewrite `manager.py`**

Replace `src/lucid/ui/preferences/manager.py` with the new shape. Preserve everything *outside* the backend split (QSettings, save/restore window state, recent files, login slot wiring). The relevant edits are concentrated in `__init__`, `set`, `get`, `remove`, and the signal declaration.

Inside `__init__` (after `super().__init__()` and existing field assignments):

```python
from lucid.settings.user_settings_client import UserSettingsClient
from lucid.ui.preferences.backend import LocalPreferenceBackend
from lucid.ui.preferences.user_portable_backend import UserPortableBackend

self._local = LocalPreferenceBackend(config_manager, beamline)
self._user_portable = UserPortableBackend(UserSettingsClient.get_instance())
self._backends: tuple = (self._user_portable, self._local)
self._topics: dict[str, _Topic] = {}
for b in self._backends:
    b.changed.connect(self._on_backend_changed)
```

Delete the class-level `preference_changed = Signal(str, object)` line.

Replace `set`/`remove`/`get` bodies:

```python
def get(self, key: str, default: Any = None) -> Any:
    return self._backend_for(key).get(key, default)

def set(self, key: str, value: Any, *, persist: bool = True) -> None:
    # `persist` retained for back-compat; user-portable backend ignores it.
    self._backend_for(key).set(key, value)

def remove(self, key: str) -> None:
    self._backend_for(key).remove(key)
```

Update `set_beamline` to also propagate to the local backend:

```python
def set_beamline(self, beamline: str | None) -> None:
    self._beamline = beamline
    self._local.set_beamline(beamline)
    logger.debug("Beamline set to: {}", beamline)
```

Add the new methods (place after `remove`):

```python
def subscribe(self, key: str, slot: Callable[[Any], None]) -> None:
    """Subscribe `slot(value)` to changes for `key`.

    Slot is invoked once per change for this specific key only.
    Call from the GUI thread. Slot is delivered on the GUI thread.
    """
    topic = self._topics.get(key)
    if topic is None:
        topic = _Topic(self)
        self._topics[key] = topic
    topic.changed.connect(slot)

def unsubscribe(self, key: str, slot: Callable[[Any], None]) -> None:
    """Disconnect a previously-subscribed slot. No-op if not subscribed."""
    topic = self._topics.get(key)
    if topic is None:
        return
    try:
        topic.changed.disconnect(slot)
    except (TypeError, RuntimeError):
        pass  # slot wasn't connected — treat as no-op

def refresh_user_portable_keys(self) -> None:
    """Trigger an async pull of all user-portable keys from the backend.
    `subscribe` slots fire for each key that moved."""
    self._user_portable.refresh()

def _backend_for(self, key: str) -> "PreferenceBackend":
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

Add `_Topic` at module scope (above `PreferencesManager`):

```python
from collections.abc import Callable


class _Topic(QObject):
    """One signal per subscribed preference key. Created lazily."""

    changed = Signal(object)  # value (None on removal)
```

Remove the now-unused `BEAMLINE_SPECIFIC_PREFS` if it lived in this file — backend.py owns it now. Update any `manager.py`-internal references to import it from `backend.py`.

- [ ] **Step 6: Migrate `mainwindow.py` theme listener**

In `src/lucid/ui/mainwindow.py`, find the line (around 252):

```python
self._prefs_manager.preference_changed.connect(self._on_preference_changed)
```

Replace with:

```python
self._prefs_manager.subscribe("theme", self._on_theme_changed)
```

Find the `_on_preference_changed` slot (around line 732) and replace it with a focused theme slot:

```python
@Slot(object)
def _on_theme_changed(self, value: Any) -> None:
    """Handle theme preference changes."""
    self._theme_manager.set_theme_by_name(str(value))
```

Delete the old `_on_preference_changed` definition.

- [ ] **Step 7: Run the full prefs + mainwindow test suite**

```
.venv/Scripts/python -m pytest tests/test_preferences_manager.py tests/test_preference_backend.py tests/test_user_portable_backend.py -v
```

Expected: all pass. If existing tests in `test_preferences_manager.py` connected to `preference_changed`, migrate them to `subscribe` (slot signature drops the key argument).

- [ ] **Step 8: Verify no stray references to `preference_changed`**

```
.venv/Scripts/python -m pytest -k "preference_changed" -v
```

Expected: zero tests matched, or all matched tests pass. Then:

```
grep -rn "preference_changed" src/ tests/
```

Expected: no matches.

- [ ] **Step 9: Commit**

```
git add src/lucid/ui/preferences/manager.py src/lucid/ui/mainwindow.py tests/test_preferences_manager.py
git commit -m "refactor(prefs): multiplex backends + topic-gated subscribe API"
```

---

## Task 5: `ProfileAvatarWidget`

**Files:**
- Create: `src/lucid/ui/widgets/profile_avatar.py`
- Test: `tests/ui/widgets/test_profile_avatar.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/widgets/__init__.py` if it doesn't exist (empty file).

Create `tests/ui/widgets/test_profile_avatar.py`:

```python
"""Tests for ProfileAvatarWidget."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    """ProfileAvatarWidget is a QWidget — need a QApplication, not QCoreApplication."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_prefs(qapp):
    from lucid.ui.preferences.manager import PreferencesManager
    from unittest.mock import MagicMock

    cm = MagicMock()
    cm._store: dict = {}
    cm.get.side_effect = lambda k, default=None: cm._store.get(k, default)
    cm.set.side_effect = lambda k, v, persist=True: cm._store.__setitem__(k, v)
    PreferencesManager.reset()
    # Construct so get_instance() returns this one.
    PreferencesManager._instance = PreferencesManager(config_manager=cm)
    yield
    PreferencesManager.reset()


def test_initial_render_is_placeholder(qapp):
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget

    w = ProfileAvatarWidget()
    assert w._loaded_image_id is None
    # The widget is at least asking for a sensible size.
    assert w.minimumSize().width() > 0


def test_subscribe_with_new_id_triggers_fetch(qapp):
    from lucid.ui.widgets import profile_avatar as pa_mod
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget
    from lucid.ui.preferences.manager import PreferencesManager

    fake_qimage = QImage(16, 16, QImage.Format.Format_ARGB32)
    fake_qimage.fill(Qt.GlobalColor.red)

    fetched_ids: list[str] = []

    def fake_fetch(client, image_id):
        fetched_ids.append(image_id)
        return fake_qimage

    with patch.object(pa_mod, "_fetch_qimage", side_effect=fake_fetch):
        w = ProfileAvatarWidget()
        prefs = PreferencesManager.get_instance()
        # Simulate a backend-driven update by routing through the topic.
        prefs._user_portable._cache["profile_image_id"] = "img-1"
        prefs._on_backend_changed("profile_image_id", "img-1")

        # Wait for the future to complete.
        import time
        deadline = time.monotonic() + 2.0
        while not fetched_ids and time.monotonic() < deadline:
            QCoreApplication.processEvents()

        assert fetched_ids == ["img-1"]


def test_subscribe_same_id_does_not_refetch(qapp):
    from lucid.ui.widgets import profile_avatar as pa_mod
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget
    from lucid.ui.preferences.manager import PreferencesManager

    fake_qimage = QImage(16, 16, QImage.Format.Format_ARGB32)
    fake_qimage.fill(Qt.GlobalColor.red)

    fetched_ids: list[str] = []

    def fake_fetch(client, image_id):
        fetched_ids.append(image_id)
        return fake_qimage

    with patch.object(pa_mod, "_fetch_qimage", side_effect=fake_fetch):
        w = ProfileAvatarWidget()
        prefs = PreferencesManager.get_instance()

        prefs._on_backend_changed("profile_image_id", "img-1")
        import time
        deadline = time.monotonic() + 2.0
        while not fetched_ids and time.monotonic() < deadline:
            QCoreApplication.processEvents()

        # Same id again — must not re-fetch.
        prefs._on_backend_changed("profile_image_id", "img-1")
        for _ in range(20):
            QCoreApplication.processEvents()

        assert fetched_ids == ["img-1"]


def test_subscribe_none_reverts_to_placeholder(qapp):
    from lucid.ui.widgets import profile_avatar as pa_mod
    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget
    from lucid.ui.preferences.manager import PreferencesManager

    fake_qimage = QImage(16, 16, QImage.Format.Format_ARGB32)
    fake_qimage.fill(Qt.GlobalColor.red)

    with patch.object(pa_mod, "_fetch_qimage", return_value=fake_qimage):
        w = ProfileAvatarWidget()
        prefs = PreferencesManager.get_instance()

        prefs._on_backend_changed("profile_image_id", "img-1")
        import time
        deadline = time.monotonic() + 2.0
        while w._loaded_image_id is None and time.monotonic() < deadline:
            QCoreApplication.processEvents()
        assert w._loaded_image_id == "img-1"

        prefs._on_backend_changed("profile_image_id", None)
        QCoreApplication.processEvents()
        assert w._loaded_image_id is None


def test_mouse_press_emits_clicked(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QMouseEvent

    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget

    w = ProfileAvatarWidget()
    received: list = []
    w.clicked.connect(lambda: received.append(True))

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.mousePressEvent(event)
    assert received == [True]


def test_right_click_does_not_emit_clicked(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QMouseEvent

    from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget

    w = ProfileAvatarWidget()
    received: list = []
    w.clicked.connect(lambda: received.append(True))

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(5, 5),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.mousePressEvent(event)
    assert received == []
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python -m pytest tests/ui/widgets/test_profile_avatar.py -v
```

Expected: every test fails with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the widget**

Create `src/lucid/ui/widgets/profile_avatar.py`:

```python
"""Small clickable avatar widget for the menubar corner.

Subscribes to `profile_image_id` on PreferencesManager; renders a
circular crop of the current profile picture, falling back to a
generic placeholder when unset. Click emits `clicked` so a host (the
mainwindow) can open the preferences dialog.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from lucid.settings.image_helpers import _fetch_qimage
from lucid.settings.user_settings_client import UserSettingsClient
from lucid.ui.preferences.manager import PreferencesManager
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture


_AVATAR_PX = 28
_PLACEHOLDER_COLOR = QColor(140, 140, 140)


class ProfileAvatarWidget(QWidget):
    """Menubar-corner avatar. Reactive on `profile_image_id` changes."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(_AVATAR_PX, _AVATAR_PX))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("User profile")

        self._pixmap: QPixmap | None = None
        self._loaded_image_id: str | None = None
        self._fetch_future: QThreadFuture | None = None

        prefs = PreferencesManager.get_instance()
        prefs.subscribe("profile_image_id", self._on_image_id_changed)

        # Seed from cache (may be None if refresh hasn't run yet).
        initial = prefs.get("profile_image_id")
        if initial:
            self._kick_off_fetch(initial)

    # ── Qt overrides ────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)

        if self._pixmap is not None:
            painter.drawPixmap(rect, self._pixmap)
        else:
            painter.fillRect(rect, QBrush(_PLACEHOLDER_COLOR))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    # ── Subscription handler ────────────────────────────────────────

    def _on_image_id_changed(self, new_id: Any) -> None:
        if new_id == self._loaded_image_id:
            return
        if not new_id:
            self._pixmap = None
            self._loaded_image_id = None
            self.update()
            return
        self._kick_off_fetch(new_id)

    # ── Worker thread plumbing ──────────────────────────────────────

    def _kick_off_fetch(self, image_id: str) -> None:
        client = UserSettingsClient.get_instance()

        def work():
            return _fetch_qimage(client, image_id)

        self._fetch_future = QThreadFuture(
            work,
            callback_slot=lambda qimg: self._on_image_ready(image_id, qimg),
            except_slot=lambda exc: self._on_image_error(image_id, exc),
        )
        self._fetch_future.start()

    def _on_image_ready(self, image_id: str, qimage) -> None:
        if qimage is None or qimage.isNull():
            self._pixmap = None
            self._loaded_image_id = None
            self.update()
            return
        pm = QPixmap.fromImage(qimage).scaled(
            _AVATAR_PX,
            _AVATAR_PX,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._pixmap = pm
        self._loaded_image_id = image_id
        self.update()

    def _on_image_error(self, image_id: str, exc: BaseException) -> None:
        logger.warning("Failed to load profile image {!r}: {}", image_id, exc)
        self._pixmap = None
        self._loaded_image_id = None
        self.update()
```

- [ ] **Step 4: Run the tests — verify pass**

```
.venv/Scripts/python -m pytest tests/ui/widgets/test_profile_avatar.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```
git add src/lucid/ui/widgets/profile_avatar.py tests/ui/widgets/__init__.py tests/ui/widgets/test_profile_avatar.py
git commit -m "feat(ui): add ProfileAvatarWidget for menubar corner"
```

---

## Task 6: NCSMainWindow corner-widget integration

**Files:**
- Modify: `src/lucid/ui/mainwindow.py`

- [ ] **Step 1: Locate the RE control corner-widget setup**

In `src/lucid/ui/mainwindow.py`, find lines 192-194:

```python
# Add RunEngine control widget to menubar corner
self._re_control = RunEngineControlWidget()
menubar.setCornerWidget(self._re_control, Qt.Corner.TopRightCorner)
```

- [ ] **Step 2: Replace with a container holding RE controls + avatar**

Replace those three lines with:

```python
# Compose the menubar-corner: [RunEngine controls | profile avatar]
from PySide6.QtWidgets import QHBoxLayout
from lucid.ui.widgets.profile_avatar import ProfileAvatarWidget

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

Ensure `QWidget` is already imported at the top of the file (it is — confirmed in the existing imports).

- [ ] **Step 3: Add the post-login refresh hook**

Find the slot `_on_login_completed` (or whichever method runs after `SessionManager` reports a successful login). Add at the end of that slot:

```python
self._prefs_manager.refresh_user_portable_keys()
```

If no such slot exists yet, find where login state changes are observed in `mainwindow.py`. As a fallback, call `self._prefs_manager.refresh_user_portable_keys()` from the existing `_connect_signals` method **after** `UserSettingsClient.init(...)` has been called — i.e., put it at the end of `_connect_signals` so the cache is at least seeded on startup. If login flow is async, also call it on every successful login transition.

- [ ] **Step 4: Smoke-launch the app**

```
cd C:/Users/rp/PycharmProjects/ncs/ncs
.venv/Scripts/python -m lucid
```

Expected: app launches; menubar shows a small circular placeholder to the right of the RE controls. Click → preferences dialog opens. (If you already have a `profile_image_id` set on the server, the avatar should populate after login.)

- [ ] **Step 5: Commit**

```
git add src/lucid/ui/mainwindow.py
git commit -m "feat(ui): mount ProfileAvatarWidget in menubar corner"
```

---

## Task 7: UserProfileSettingsPlugin migration to `PreferencesManager`

**Files:**
- Modify: `src/lucid/ui/preferences/user_profile_settings.py`
- Modify: `tests/ui/test_user_profile_plugin.py`

- [ ] **Step 1: Update test assertions (failing)**

Open `tests/ui/test_user_profile_plugin.py`. For any test that asserts `UserSettingsClient.set(...)` or `UserSettingsClient.get(...)` was called for `profile_image_id`, change the assertion to check `PreferencesManager.get_instance().set/get/remove` instead. Concretely:

- Tests for the upload flow: assert `PreferencesManager.get_instance().set("profile_image_id", <id>)` was called, **not** `client.set`. (Use `monkeypatch.setattr(PreferencesManager, "set", ...)` or check side effects.)
- Tests for the remove flow: assert `PreferencesManager.get_instance().remove("profile_image_id")` was called.
- Tests for load: assert `PreferencesManager.get_instance().get("profile_image_id", default=None)` was called.

Where the existing tests use the `_StubSession` and httpx_mock pattern, that pattern is still appropriate for the *upload* (blob) call — `client.upload_image` still hits HTTP. Only the key/value side moves.

- [ ] **Step 2: Run tests — verify failure**

```
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

Expected: the modified tests fail because the plugin still calls `UserSettingsClient` directly.

- [ ] **Step 3: Edit the plugin**

In `src/lucid/ui/preferences/user_profile_settings.py`:

(a) Replace the `load_settings` body so it reads from PreferencesManager and subscribes for live updates:

```python
def load_settings(self) -> None:
    """Render the current avatar from PreferencesManager's cache; subscribe
    for future updates so a change elsewhere re-renders this dialog."""
    from lucid.ui.preferences.manager import PreferencesManager

    prefs = PreferencesManager.get_instance()
    prefs.subscribe("profile_image_id", self._on_image_id_changed)
    image_id = prefs.get("profile_image_id", default=None)
    self._on_image_id_changed(image_id)


def _on_image_id_changed(self, image_id: str | None) -> None:
    from lucid.settings.user_settings_client import UserSettingsClient
    from lucid.utils.threads import QThreadFuture

    if not image_id:
        self._set_placeholder_avatar()
        self._loaded_image_id = None
        return
    if image_id == self._loaded_image_id:
        return

    client = UserSettingsClient.get_instance()
    self._load_future = QThreadFuture(
        _fetch_qimage,
        client,
        image_id,
        callback_slot=lambda qimg: self._on_image_ready(image_id, qimg),
        except_slot=lambda exc: self._on_image_error(exc),
    )
    self._load_future.start()
```

Delete any duplicated logic from the old `load_settings`.

(b) In `_upload_and_set`, replace `client.set("profile_image_id", image_id)` with the PreferencesManager call:

```python
def _upload_and_set(self, data: bytes, mime: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    from lucid.settings.user_settings_client import (
        UserSettingsClient,
        UserSettingsError,
    )
    from lucid.ui.preferences.manager import PreferencesManager
    from lucid.utils.threads import QThreadFuture

    client = UserSettingsClient.get_instance()

    def work():
        # Blob upload stays direct on the client.
        return client.upload_image(data, mime)

    def on_ok(image_id: str):
        # Route the key/value through PreferencesManager so observers
        # (including this dialog and the toolbar avatar) get notified.
        PreferencesManager.get_instance().set("profile_image_id", image_id)

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

(c) In `_on_remove_clicked` (or wherever `client.delete("profile_image_id")` is called), replace with:

```python
PreferencesManager.get_instance().remove("profile_image_id")
```

and drop the now-unused `client.delete` call site. Re-render happens via the subscription, so no manual `load_settings()` call is needed.

(d) Unsubscribe on widget destruction. Add to the plugin class:

```python
def teardown(self) -> None:
    """Called by the dialog when the page is closed."""
    from lucid.ui.preferences.manager import PreferencesManager
    PreferencesManager.get_instance().unsubscribe(
        "profile_image_id", self._on_image_id_changed
    )
```

If `SettingsPlugin` doesn't have a `teardown` hook, this is a no-op until one is added — the `_Topic` parented to PreferencesManager will keep the connection live until PrefMgr resets. Acceptable for now; document in a `# TODO:` comment.

- [ ] **Step 4: Run plugin tests — verify pass**

```
.venv/Scripts/python -m pytest tests/ui/test_user_profile_plugin.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full suite for safety**

```
.venv/Scripts/python -m pytest tests/test_image_helpers.py tests/test_preference_backend.py tests/test_user_portable_backend.py tests/test_preferences_manager.py tests/ui/widgets/test_profile_avatar.py tests/ui/test_user_profile_plugin.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add src/lucid/ui/preferences/user_profile_settings.py tests/ui/test_user_profile_plugin.py
git commit -m "refactor(prefs): route profile_image_id through PreferencesManager"
```

---

## Task 8: Manual smoke test + final commit hygiene

- [ ] **Step 1: Launch the app fresh**

```
cd C:/Users/rp/PycharmProjects/ncs/ncs
.venv/Scripts/python -m lucid
```

- [ ] **Step 2: Walk the scenarios**

1. App start, not logged in → menubar corner shows RE controls + a grey placeholder avatar.
2. Log in → after the login completes, the avatar populates with the user's saved image (if any).
3. Open Preferences → User Profile → upload a new image → the dialog updates AND the menubar avatar updates simultaneously (same `preference_changed` topic fires both).
4. Remove the image → both the dialog avatar and the menubar avatar revert to placeholder.
5. Click the menubar avatar → Preferences dialog opens.

- [ ] **Step 3: Run the full lucid test suite (not just the touched files)**

```
.venv/Scripts/python -m pytest -q
```

Expected: full pass. Investigate any unexpected breakage — most likely a stray `preference_changed` reference somewhere in code or tests that the migration missed.

- [ ] **Step 4: Push the branch and open the MR**

```
cd C:/Users/rp/PycharmProjects/ncs/ncs
git push -u upstream feature/profile-avatar-pref-backends
```

Open an MR via the URL the push response prints. Suggested title: `feat(prefs): unified backends + topic-gated subscribe; profile avatar in menubar`.

---

## Notes for the executor

- **Threading:** `QThreadFuture` lives in `lucid.utils.threads`. Callbacks (`callback_slot`, `except_slot`) are delivered on the GUI thread. You should never need to call `QMetaObject.invokeMethod` manually.
- **PreferencesManager singleton:** Tests reset it via `PreferencesManager.reset()` (existing classmethod). Construct fresh manager state in fixtures; never mutate the global across tests.
- **`USER_PORTABLE_KEYS` is a `frozenset`** — extending it later (display name, ORCID-linked metadata) is a one-line edit. Don't make it a mutable singleton.
- **Test pattern:** prefer `QSignalSpy` + a hand-rolled `_wait_for_spy` (shown in Task 3) over `qtbot.waitSignal` — keeps tests usable under `QCoreApplication` for backend-only suites that don't need a full `QApplication`.
- **Never use `git add -A`** on Ron's working tree. Always stage explicit paths (see each task's commit step).
- **Test command:** `.venv/Scripts/python -m pytest <path>`. Bare `pytest` resolves to system Python 3.10 and cannot import `lucid` (which is 3.12+).

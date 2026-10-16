"""Preference backend that resolves keys through the scope chain (spec section 5).

Owns every non-local key while a user is logged in. Reads come from an in-memory
ScopedStore; writes are optimistic and pushed to the logbook server on a worker.
The local YAML stays the user-scope cache, so logged-out behaviour is unchanged.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from lightfall_settings import SpecRegistry, spec_to_dict
from PySide6.QtCore import QTimer

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.journal import WriteJournal
from lightfall.scopes.keys import client_registry, flatten_preferences, is_local_only
from lightfall.scopes.ops import migrate_user_prefs, push_write, replay_journal
from lightfall.scopes.state import FetchedState, fetch_state
from lightfall.scopes.store import MISSING, ScopedStore
from lightfall.ui.preferences.backend import PreferenceBackend
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

Runner = Callable[[Callable[[], Any], Callable[[Any], None], Callable[[Exception], None]], None]


def _thread_runner(fn: Callable[[], Any], ok: Callable[[Any], None],
                   err: Callable[[Exception], None]) -> None:
    QThreadFuture(fn, callback_slot=ok, except_slot=err, name="scoped-settings",
                  log_exceptions=False).start()


def _toast(title: str, text: str) -> None:
    try:
        from lightfall.ui.toast import ToastManager

        ToastManager.get_instance().warning(title, text)
    except Exception:  # no main window (tests, early startup)
        logger.warning("{}: {}", title, text)


class ScopedBackend(PreferenceBackend):
    def __init__(self, local: Any, *, client: Any, cache: SnapshotCache, journal: WriteJournal,
                 bind: str, identity: str, registry: SpecRegistry | None = None,
                 user_preferences: Callable[[], Mapping[str, Any]] = dict,
                 run: Runner | None = None, notify: Callable[[str, str], None] | None = None,
                 poll_ms: int = 300_000) -> None:
        super().__init__()
        self._local = local
        self._client = client
        self._cache = cache
        self._journal = journal
        self._bind = bind
        self._identity = identity
        self._registry = registry or client_registry()
        self._user_preferences = user_preferences
        self._run = run or _thread_runner
        self._notify = notify or _toast
        self._store: ScopedStore | None = None
        self._active = False
        self._refreshing = False
        self._migration_tried = False
        # Bumped on deactivate so callbacks from an earlier session are dropped.
        self._session = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._poll_ms = poll_ms

    # ── state ───────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def store(self) -> ScopedStore | None:
        return self._store

    def activate(self) -> None:
        self._active = True
        if self._poll_ms:
            self._timer.start(self._poll_ms)
        self.refresh()

    def deactivate(self) -> None:
        keys = self._store.keys() if self._store is not None else set()
        before = {k: self.get(k) for k in keys}
        self._active = False
        self._session += 1
        self._store = None
        self._refreshing = False
        self._timer.stop()
        for key in sorted(keys):
            after = self._local.get(key, None)
            if after != before[key]:
                self.changed.emit(key, after)

    def _guard(self, fn: Callable[..., None]) -> Callable[..., None]:
        session = self._session

        def wrapped(*args: Any) -> None:
            if self._active and session == self._session:
                fn(*args)
        return wrapped

    # ── PreferenceBackend contract ──────────────────────────────────────

    def owns(self, key: str) -> bool:
        return self._active and not is_local_only(key)

    def get(self, key: str, default: Any = None) -> Any:
        if self._store is not None:
            value = self._store.value(key)
            if value is not MISSING:
                return value
        return self._local.get(key, default)

    def set(self, key: str, value: Any, scope: str | None = None) -> None:
        if is_local_only(key):
            raise ValueError(f"{key!r} is local-only and never goes through scoped settings")
        if value is None:
            self.remove(key, scope=scope)
            return
        user_scope = self._store.user_scope if self._store is not None else None
        if scope is None or scope == user_scope:
            self._write_user(key, "set", value)
        else:
            self._write_scope(scope, key, value)

    def remove(self, key: str, scope: str | None = None) -> None:
        if is_local_only(key):
            raise ValueError(f"{key!r} is local-only and never goes through scoped settings")
        user_scope = self._store.user_scope if self._store is not None else None
        if scope is None or scope == user_scope:
            self._write_user(key, "delete", None)
        else:
            self._write_scope(scope, key, None)

    # ── writes ──────────────────────────────────────────────────────────

    def _cache_yaml(self, key: str, value: Any) -> None:
        cm = getattr(self._local, "_cm", None)
        if cm is None:
            return
        try:
            cm.set(f"preferences.{key}", value, persist=True)
        except Exception as e:
            logger.warning("Could not cache {} in user config: {}", key, e)

    def _emit_if_moved(self, key: str, before: Any) -> None:
        after = self.get(key)
        if after != before:
            self.changed.emit(key, after)

    def _write_user(self, key: str, op: str, value: Any) -> None:
        before = self.get(key)
        self._cache_yaml(key, value)
        store = self._store
        scope = store.user_scope if store is not None else None
        if store is None or scope is None:
            self._journal.record(key, op, value)
            self._emit_if_moved(key, before)
            return
        prev, prev_version = store.user_value(key), store.version(scope, key)
        if op == "delete":
            store.drop_local(scope, key)
        else:
            store.put_local(scope, key, value)
        self._emit_if_moved(key, before)
        client = self._client
        self._run(
            lambda: push_write(client, scope, key, op, value, prev_version),
            self._guard(lambda version: self._on_written(scope, key, version)),
            self._guard(lambda e: self._on_write_failed(scope, key, op, value, prev,
                                                        prev_version, e)),
        )

    def _on_written(self, scope: str, key: str, version: int) -> None:
        if self._store is not None:
            self._store.set_version(scope, key, version)
        self._journal.discard(key)

    def _on_write_failed(self, scope: str, key: str, op: str, value: Any, prev: Any,
                         prev_version: int | None, exc: Exception) -> None:
        if getattr(exc, "status", 0) is None:  # transport failure: keep it, replay later
            self._journal.record(key, op, value)
            return
        store = self._store
        if store is None:
            return
        current = store.user_value(key)
        still_ours = (current is MISSING) if op == "delete" else (current == value)
        if not still_ours:
            return  # a refresh already replaced this value; don't clobber it
        before = self.get(key)
        store.restore(scope, key, prev, prev_version)
        self._cache_yaml(key, None if prev is MISSING else prev)
        self._emit_if_moved(key, before)
        self._notify("Setting not saved", f"{key}: {exc}")

    def _write_scope(self, scope: str, key: str, value: Any) -> None:
        store = self._store
        version = store.version(scope, key) if store is not None else None
        spec = spec_to_dict(self._registry.get(key)) if self._registry.is_declared(key) else None
        client = self._client

        def work() -> int:
            if value is None:
                client.delete_setting(scope, key)
                return 0
            return client.put_setting(scope, key, value, version=version, proposed_spec=spec)

        def failed(exc: Exception) -> None:
            if getattr(exc, "code", None) == "key_pending":
                self._notify("Setting not saved", f"{key} at {scope}: awaiting approval "
                                                  "by a facility admin")
            else:
                self._notify("Setting not saved", f"{key} at {scope}: {exc}")

        self._run(work, self._guard(lambda _v: self.refresh()), self._guard(failed))

    # ── refresh ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        if not self._active or self._refreshing:
            return
        self._refreshing = True
        client, cache, bind, identity = self._client, self._cache, self._bind, self._identity
        self._run(lambda: fetch_state(client, cache, bind, identity),
                  self._guard(self._on_state), self._guard(self._on_state_failed))

    def _on_state_failed(self, exc: Exception) -> None:
        self._refreshing = False
        logger.info("Scoped settings unavailable: {}", exc)

    def _on_state(self, state: FetchedState) -> None:
        self._refreshing = False
        old_keys = self._store.keys() if self._store is not None else set()
        old_user = self._store.user_settings() if self._store is not None else {}
        new_keys = {k for snap in state.snapshots.values() for k in snap.settings}
        journal = self._journal.entries()
        watched = old_keys | new_keys | set(journal)
        before = {k: self.get(k) for k in watched}

        if self._store is None:
            self._store = ScopedStore(self._registry, state)
        else:
            self._store.replace(state)
        store = self._store
        scope = store.user_scope
        if scope is not None:
            for key, entry in journal.items():
                if entry["op"] == "delete":
                    store.drop_local(scope, key)
                else:
                    store.put_local(scope, key, entry["value"])
            self._sync_yaml(old_user, store.user_settings(), journal)

        for key in sorted(watched):
            after = self.get(key)
            if after != before[key]:
                self.changed.emit(key, after)
        if not state.offline and scope is not None:
            self._after_online(scope, state)

    def _sync_yaml(self, old_user: Mapping[str, Any], new_user: Mapping[str, Any],
                   journal: Mapping[str, Any]) -> None:
        for key, value in new_user.items():
            if not is_local_only(key) and self._local.get(key, None) != value:
                self._cache_yaml(key, value)
        for key in set(old_user) - set(new_user) - set(journal):
            if not is_local_only(key):
                self._cache_yaml(key, None)

    def _after_online(self, scope: str, state: FetchedState) -> None:
        client, registry = self._client, self._registry
        server_user = state.snapshots.get(scope)
        if (server_user is not None and not server_user.settings
                and not self._migration_tried):
            self._migration_tried = True
            prefs = flatten_preferences(self._user_preferences())
            self._run(lambda: migrate_user_prefs(client, scope, prefs, registry),
                      self._guard(self._on_migrated), self._guard(self._on_op_failed))
            return
        entries = self._journal.entries()
        if entries:
            store = self._store
            versions = {k: store.version(scope, k) if store else None for k in entries}
            self._run(lambda: replay_journal(client, scope, entries, versions),
                      self._guard(self._on_replayed), self._guard(self._on_op_failed))

    def _on_migrated(self, uploaded: list[str]) -> None:
        logger.info("Migrated {} preferences to user scope", len(uploaded))
        self.refresh()

    def _on_replayed(self, result: tuple[list[str], list[tuple[str, str]]]) -> None:
        done, dropped = result
        for key in done:
            self._journal.discard(key)
        for key, reason in dropped:
            self._journal.discard(key)
            self._notify("Offline change not saved", f"{key}: {reason}")
        if done or dropped:
            self.refresh()

    def _on_op_failed(self, exc: Exception) -> None:
        logger.info("Scoped settings sync deferred: {}", exc)
        if getattr(exc, "status", 0) is None:
            self._migration_tried = False  # retry the migration on the next refresh


def build_scoped_backend(local: Any, *, identity: str, bind: str,
                         user_preferences: Callable[[], Mapping[str, Any]]) -> ScopedBackend:
    from lightfall.scopes.client import ScopesClient
    from lightfall.scopes.journal import journal_path
    from lightfall.scopes.site import default_cache_dir

    directory = default_cache_dir()
    return ScopedBackend(local, client=ScopesClient(), cache=SnapshotCache(directory),
                         journal=WriteJournal(journal_path(directory, identity)),
                         bind=bind, identity=identity, user_preferences=user_preferences)

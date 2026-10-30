"""Preference backend that resolves keys through the scope chain (spec section 5).

Owns every non-local key while a user is logged in. Reads come from an in-memory
ScopedStore; writes are optimistic and pushed to the logbook server on a worker.

The local YAML is this console's own configuration, never a copy of the user's scope
(offline reads come from the snapshot cache and the write journal). It is the fallback
below every scope, and for LOCAL_LAYER_KEYS a layer between branch and user scope
(docs/developer-guide/settings-layering.md).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from lightfall_settings import ScopeId, SpecRegistry, role_at_least, spec_to_dict
from PySide6.QtCore import QTimer, Signal

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.client import is_transient
from lightfall.scopes.journal import WriteJournal
from lightfall.scopes.keys import (
    LOCAL_LAYER_KEYS,
    client_registry,
    flatten_preferences,
    is_local_only,
)
from lightfall.scopes.ops import migrate_user_prefs, push_write, replay_journal
from lightfall.scopes.state import FetchedState, fetch_state
from lightfall.scopes.store import MISSING, ScopedStore
from lightfall.ui.preferences.backend import PreferenceBackend
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

# Local-only YAML markers (leading underscore => never uploaded). Clients before the
# settings-layering fix cached the logged-in user's values in the YAML and recorded whose
# they were here; _drop_old_user_cache() removes that copy once, then this marker.
_OWNER_KEY = "_scoped_cache_owner"
_MIGRATED_KEY = "_scoped_migrated"
LOCAL = "local"  # write target for a console's own layer (the YAML)


def _as_union(value: Any) -> dict[str, Any]:
    """A row key's value as {item id: item}; legacy YAML stores lists."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        out: dict[str, Any] = {}
        for item in value:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = item
            elif isinstance(item, str):
                out[item] = True
        return out
    return {}


def _is_json(value: Any) -> bool:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


SETTINGS_SUBJECT = "lightfall.settings.changed"  # lightfall_logbook.scoped_api.SETTINGS_SUBJECT


def _default_ipc() -> Any:
    from lightfall.ipc.service import get_ipc_service

    return get_ipc_service()


Runner = Callable[[Callable[[], Any], Callable[[Any], None], Callable[[Exception], None]], None]


def _thread_runner(fn: Callable[[], Any], ok: Callable[[Any], None],
                   err: Callable[[Exception], None]) -> None:
    QThreadFuture(fn, callback_slot=ok, except_slot=err, name="scoped-settings",
                  log_exceptions=False).start()


def _pending_reason(key_status: str | None) -> str:
    """Why the server refused a write of a key it hasn't approved."""
    if key_status == "pending":
        return "awaiting approval by a facility admin"
    if key_status == "rejected":
        return "a facility admin rejected this setting"
    return ("not an approved setting, and this Lightfall doesn't know its rules, so no "
            "proposal was filed; ask a facility admin to approve it")


def _toast(title: str, text: str) -> None:
    try:
        from lightfall.ui.toast import ToastManager

        ToastManager.get_instance().warning(title, text)
    except Exception:  # no main window (tests, early startup)
        logger.warning("{}: {}", title, text)


class ScopedBackend(PreferenceBackend):
    chain_changed = Signal(object)  # ChainInfo, when the resolved chain changes

    def __init__(self, local: Any, *, client: Any, cache: SnapshotCache, journal: WriteJournal,
                 bind: str, identity: str, registry: SpecRegistry | None = None,
                 user_preferences: Callable[[], Mapping[str, Any]] = dict,
                 run: Runner | None = None, notify: Callable[[str, str], None] | None = None,
                 poll_ms: int = 60_000, ipc: Callable[[], Any] | None = None,
                 debounce_ms: int = 500) -> None:
        super().__init__()
        self._local = local
        self._client = client
        self._cache = cache
        self._journal = journal
        self._bind = bind
        self._identity = identity
        self._registry = registry or client_registry()
        # Rebuilt from each chain's approved specs unless a registry was passed in (tests).
        self._own_registry = registry is None
        self._server_specs: tuple[dict[str, Any], ...] = ()
        self._user_preferences = user_preferences
        self._run = run or _thread_runner
        self._notify = notify or _toast
        self._store: ScopedStore | None = None
        self._roles: dict[str, str | None] = {}
        self._active = False
        self._refreshing = False
        self._migrating = False
        # Also kept in memory: if the YAML marker can't be written, don't migrate again.
        self._migrated_here = False
        # Confirmed writes: key -> (op, value, version). A snapshot taken before the
        # write committed must not roll it back.
        self._written: dict[str, tuple[str, Any, int | None]] = {}
        # Bumped on deactivate so callbacks from an earlier session are dropped.
        self._session = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._poll_ms = poll_ms
        self._fallback: Callable[[str, Any], Any] = local.get
        self._refresh_again = False
        # Live updates: server change events (notify-and-pull), debounced.
        self._ipc_get = ipc or _default_ipc
        self._ipc: Any = None
        self._debounce_ms = debounce_ms
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)

    # ── state ───────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    def _subscribe_events(self) -> None:
        try:
            ipc = self._ipc_get()
        except Exception as e:
            logger.debug("No IPC service for settings events: {}", e)
            ipc = None
        if ipc is None:
            return
        try:
            ipc.subscribe(SETTINGS_SUBJECT, self._on_settings_event, main_thread=True)
            ipc.sigConnectionChanged.connect(self._on_ipc_connection)
        except Exception as e:
            logger.warning("Could not subscribe to settings events: {}", e)
            return
        self._ipc = ipc

    def _unsubscribe_events(self) -> None:
        ipc, self._ipc = self._ipc, None
        if ipc is None:
            return
        try:
            ipc.unsubscribe(SETTINGS_SUBJECT)
            ipc.sigConnectionChanged.disconnect(self._on_ipc_connection)
        except Exception:
            pass

    def _on_settings_event(self, subject: str, data: Any, reply: str | None) -> None:
        if not self._active or not isinstance(data, dict):
            return
        scope = data.get("scope")
        if self._store is not None and scope not in self._store.chain:
            return  # another user's scope, or outside this chain
        if self._debounce_ms <= 0:
            self.refresh()
        else:
            self._debounce.start(self._debounce_ms)

    def _on_ipc_connection(self, connected: bool) -> None:
        if connected and self._active:
            self.refresh()

    @property
    def store(self) -> ScopedStore | None:
        return self._store

    def serves(self, identity: str, bind: str) -> bool:
        return self._active and identity == self._identity and bind == self._bind

    def activate(self) -> None:
        self._active = True
        if self._poll_ms:
            self._timer.start(self._poll_ms)
        self._subscribe_events()
        self.refresh()

    def deactivate(self) -> None:
        keys = self._store.keys() if self._store is not None else set()
        before = {k: self.get(k) for k in keys}
        self._active = False
        self._session += 1
        self._store = None
        self._roles = {}
        self._refreshing = False
        self._migrating = False
        self._written.clear()
        self._timer.stop()
        self._debounce.stop()
        self._refresh_again = False
        self._unsubscribe_events()
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
            if key in LOCAL_LAYER_KEYS:
                value, _origins = self._layered(key)
            else:
                value = self._store.value(key)
            if value is not MISSING:
                return value
        if self._yaml_foreign():
            return default  # an old client's cache of another user's values
        return self._fallback(key, default)

    def _layered(self, key: str) -> tuple[Any, dict[str, str]]:
        """beamline -> branch -> local (YAML) -> user. Returns (value, item origins)."""
        store = self._store
        assert store is not None
        spec = self._registry.get(key)
        chain = store.resolve(key, include_user=False)
        local = MISSING if self._yaml_foreign() else self._local.get(key, MISSING)
        user = store.user_value(key) if spec.allows("user") else MISSING
        if chain.locked_at is not None:
            local = user = MISSING  # locked above: lower layers don't apply
        if spec.policy != "union":
            for value, origin in ((user, store.user_scope), (local, LOCAL)):
                if value is not MISSING and value is not None:
                    return value, {"": str(origin)}
            if chain.origin is None:
                return MISSING, {}
            return chain.value, {"": str(chain.origin)}
        merged: dict[str, Any] = dict(chain.value) if chain.origin is not None else {}
        origins = {name: str(scope) for name, scope in chain.item_origins.items()}
        found = chain.origin is not None
        for value, origin in ((local, LOCAL), (user, store.user_scope)):
            if value is MISSING or value is None:
                continue
            found = True
            for name, item in _as_union(value).items():
                if item is not None:
                    merged[name], origins[name] = item, str(origin)
        return (merged if found else MISSING), origins

    def item_origins(self, key: str) -> dict[str, str]:
        """For a row key: item id -> the scope it comes from (``"local"`` = this console)."""
        if self._store is None or key not in LOCAL_LAYER_KEYS:
            return {}
        return self._layered(key)[1]

    def layer_value(self, key: str, scope: str) -> Any:
        """The value stored at one layer (a chain scope or ``"local"``), or MISSING."""
        if scope == LOCAL:
            return self._local.get(key, MISSING)
        if self._store is None:
            return MISSING
        return self._store.scope_value(scope, key)

    def targets(self, key: str) -> list[str]:
        """Where this caller may write ``key``, most specific first."""
        spec = self._registry.get(key)
        store = self._store
        out: list[str] = []
        if store is not None and store.user_scope and spec.allows("user"):
            out.append(store.user_scope)
        if key in LOCAL_LAYER_KEYS:
            out.append(LOCAL)
        for scope in reversed(store.chain if store is not None else ()):
            sid = ScopeId.parse(scope)
            if sid.kind in ("user", "facility") or not spec.allows(sid.kind):
                continue
            if role_at_least(self._roles.get(scope), spec.required_role(sid.kind)):
                out.append(scope)
        return out

    def set_fallback(self, reader: Callable[[str, Any], Any]) -> None:
        """Read path for keys no scope sets (default: the YAML). The manager passes one
        that consults local scope files first, so their values and locks survive an
        offline login."""
        self._fallback = reader

    def _yaml_foreign(self) -> bool:
        owner = self._local.get(_OWNER_KEY, None)
        return owner is not None and owner != self._identity

    def set(self, key: str, value: Any, scope: str | None = None) -> None:
        if is_local_only(key):
            raise ValueError(f"{key!r} is local-only and never goes through scoped settings")
        if value is None:
            self.remove(key, scope=scope)
            return
        if scope == LOCAL:
            self._write_local(key, value)
            return
        if scope is None:
            if not self._registry.get(key).allows("user") and self.get(key) == value:
                return  # e.g. Preferences "Apply" re-saving the beamline's value
            target = self._default_target(key)
            if target is False:
                return
            scope = target
        user_scope = self._store.user_scope if self._store is not None else None
        if not _is_json(value):
            if scope not in (None, user_scope):
                raise TypeError(f"{key!r}: value is not JSON and cannot be shared at {scope}")
            logger.warning("{} is not JSON-serializable; keeping it on this machine only", key)
            self._write_local(key, value)
            return
        if scope is None or scope == user_scope:
            self._write_user(key, "set", value)
        else:
            self._write_scope(scope, key, value)

    def remove(self, key: str, scope: str | None = None) -> None:
        if is_local_only(key):
            raise ValueError(f"{key!r} is local-only and never goes through scoped settings")
        if scope == LOCAL:
            self._write_local(key, None)
            return
        if scope is None and not self._registry.get(key).allows("user"):
            # Nothing of the user's to remove; resetting a beamline value needs its scope.
            return
        user_scope = self._store.user_scope if self._store is not None else None
        if scope is None or scope == user_scope:
            self._write_user(key, "delete", None)
        else:
            self._write_scope(scope, key, None)

    # ── writes ──────────────────────────────────────────────────────────

    def _default_target(self, key: str) -> str | None | bool:
        """Scope for an unscoped write: the user's, unless the key's spec forbids user
        scope; then the most specific scope this caller may write. False = refused."""
        spec = self._registry.get(key)
        if spec.allows("user") or self._store is None:
            return None
        writable = [t for t in self.targets(key) if t != LOCAL]
        if writable:
            return writable[0]
        self._notify("Setting not saved",
                     f"{key} is set per beamline; ask beamline staff to change it")
        return False

    def _write_local(self, key: str, value: Any) -> None:
        before = self.get(key)
        self._cache_yaml(key, value)
        self._emit_if_moved(key, before)

    def _cache_yaml(self, key: str, value: Any) -> None:
        try:
            self._local.store_quiet(key, value)
        except Exception as e:
            logger.warning("Could not write {} to the local config: {}", key, e)

    def _emit_if_moved(self, key: str, before: Any) -> None:
        after = self.get(key)
        if after != before:
            self.changed.emit(key, after)

    def _write_user(self, key: str, op: str, value: Any) -> None:
        before = self.get(key)
        # Write-ahead: the entry survives logout or quit mid-push and is overlaid on
        # refreshes until the server confirms it.
        self._journal.record(key, op, value)
        store = self._store
        scope = store.user_scope if store is not None else None
        if store is None or scope is None:
            self._emit_if_moved(key, before)
            return
        prev, prev_version = store.user_value(key), store.version(scope, key)
        if (op == "set" and prev is not MISSING and prev == value) or (
                op == "delete" and prev is MISSING):
            # Already the server's value (e.g. Preferences "Apply" re-saving every page).
            self._journal.discard_if(key, op, value)
            self._emit_if_moved(key, before)
            return
        if op == "delete":
            store.drop_local(scope, key)
        else:
            store.put_local(scope, key, value)
        self._emit_if_moved(key, before)
        client = self._client
        self._run(
            lambda: push_write(client, scope, key, op, value, prev_version),
            self._guard(lambda version: self._on_written(scope, key, op, value, version,
                                                         prev_version)),
            self._guard(lambda e: self._on_write_failed(scope, key, op, value, prev,
                                                        prev_version, e)),
        )

    def _on_written(self, scope: str, key: str, op: str, value: Any, version: int,
                    prev_version: int | None) -> None:
        if self._store is not None:
            self._store.set_version(scope, key, version)
        self._written[key] = (op, value, version if op == "set" else prev_version)
        self._journal.discard_if(key, op, value)

    def _on_write_failed(self, scope: str, key: str, op: str, value: Any, prev: Any,
                         prev_version: int | None, exc: Exception) -> None:
        if is_transient(exc):  # stays journaled; replayed on the next refresh
            return
        self._journal.discard_if(key, op, value)
        store = self._store
        if store is None:
            return
        current = store.user_value(key)
        still_ours = (current is MISSING) if op == "delete" else (current == value)
        if not still_ours:
            return  # a refresh already replaced this value; don't clobber it
        before = self.get(key)
        store.restore(scope, key, prev, prev_version)
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
                self._notify("Setting not saved", f"{key} at {scope}: " + _pending_reason(
                    getattr(exc, "key_status", None)))
            else:
                self._notify("Setting not saved", f"{key} at {scope}: {exc}")

        self._run(work, self._guard(lambda _v: self.refresh()), self._guard(failed))

    # ── refresh ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        if not self._active:
            return
        if self._refreshing:
            self._refresh_again = True  # e.g. an event during a poll: fetch once more after
            return
        self._refreshing = True
        client, cache, bind, identity = self._client, self._cache, self._bind, self._identity
        self._run(lambda: fetch_state(client, cache, bind, identity),
                  self._guard(self._on_state), self._guard(self._on_state_failed))

    def _on_state_failed(self, exc: Exception) -> None:
        self._refreshing = False
        logger.info("Scoped settings unavailable: {}", exc)
        self._maybe_refresh_again()

    def _maybe_refresh_again(self) -> None:
        if self._refresh_again:
            self._refresh_again = False
            self.refresh()

    def _on_state(self, state: FetchedState) -> None:
        self._refreshing = False
        old_chain = self._store.chain if self._store is not None else None
        old_keys = self._store.keys() if self._store is not None else set()
        new_keys = {k for snap in state.snapshots.values() for k in snap.settings}
        journal = self._journal.entries()
        watched = old_keys | new_keys | set(journal) | set(self._written)
        before = {k: self.get(k) for k in watched}

        if self._own_registry and state.chain.specs != self._server_specs:
            # Approvals take effect here: the server enforces these rules already.
            self._server_specs = state.chain.specs
            self._registry = client_registry(self._server_specs)
            if self._store is not None:
                self._store.set_registry(self._registry)
        if self._store is None:
            self._store = ScopedStore(self._registry, state)
        else:
            self._store.replace(state)
        store = self._store
        scope = store.user_scope
        user_ok = scope is not None and scope not in state.failed
        if scope is not None:
            server_versions = state.snapshots[scope].versions if scope in state.snapshots else {}
            self._overlay_written(store, scope, server_versions)
            for key, entry in journal.items():
                if entry["op"] == "delete":
                    store.drop_local(scope, key)
                else:
                    store.put_local(scope, key, entry["value"])
        self._roles = dict(state.chain.roles)
        if user_ok and not state.offline:
            watched |= self._drop_old_user_cache(store.user_settings())

        for key in sorted(watched):
            after = self.get(key)
            if after != before.get(key):
                self.changed.emit(key, after)
        if tuple(state.chain.chain) != old_chain:
            self.chain_changed.emit(state.chain)
        if not state.offline and user_ok:
            self._after_online(scope, state)
        self._maybe_refresh_again()

    def _overlay_written(self, store: ScopedStore, scope: str,
                         server_versions: Mapping[str, int]) -> None:
        """Re-apply confirmed writes newer than this snapshot (it predates them)."""
        for key, (op, value, version) in list(self._written.items()):
            seen = server_versions.get(key)
            if op == "set":
                if seen is None or (version is not None and seen < version):
                    store.put_local(scope, key, value)
                    if version:
                        store.set_version(scope, key, version)
                else:
                    del self._written[key]  # the server has caught up (or moved on)
            elif seen is not None and version is not None and seen <= version:
                store.drop_local(scope, key)  # snapshot still shows the deleted row
            else:
                del self._written[key]

    def _drop_old_user_cache(self, user_settings: Mapping[str, Any]) -> set[str]:
        """Remove values an old client cached in the YAML for the logged-in user.

        Runs once per console (the owner marker is dropped afterwards). Values equal to
        the owner's server values are a cached copy; with another user's cache we cannot
        tell, so every scoped key goes. Returns the keys removed.
        """
        owner = self._local.get(_OWNER_KEY, None)
        if owner is None:
            return set()
        purged: set[str] = set()
        for key, value in flatten_preferences(self._user_preferences()).items():
            if is_local_only(key):
                continue
            if owner == self._identity and user_settings.get(key, MISSING) != value:
                continue  # differs from the user's value: console config, keep it
            self._cache_yaml(key, None)
            purged.add(key)
        self._cache_yaml(_OWNER_KEY, None)
        if owner != self._identity:
            self._mark_migrated()  # never upload the YAML another user left behind
        logger.info("Removed {} cached user value(s) from the local config", len(purged))
        return purged

    def _migrated(self) -> bool:
        if self._migrated_here:
            return True
        done = self._local.get(_MIGRATED_KEY, None)
        return isinstance(done, list) and self._identity in done

    def _mark_migrated(self) -> None:
        self._migrated_here = True
        done = self._local.get(_MIGRATED_KEY, None)
        done = list(done) if isinstance(done, list) else []
        if self._identity not in done:
            self._cache_yaml(_MIGRATED_KEY, [*done, self._identity])

    def _after_online(self, scope: str, state: FetchedState) -> None:
        client, registry = self._client, self._registry
        # A bound console's YAML belongs to the console, not to whoever logs in first.
        if self._bind == "facility" and not self._migrated() and not self._yaml_foreign():
            if self._migrating:
                return
            # Per-key PUTs without a version never overwrite: existing keys 412 and
            # are skipped, so this is safe to resume after a partial run.
            self._migrating = True
            prefs = flatten_preferences(self._user_preferences())
            self._run(lambda: migrate_user_prefs(client, scope, prefs, registry),
                      self._guard(self._on_migrated), self._guard(self._on_op_failed))
            return
        entries = self._journal.entries()
        if entries:
            store = self._store
            versions = {k: store.version(scope, k) if store else None for k in entries}
            self._run(lambda: replay_journal(client, scope, entries, versions),
                      self._guard(lambda result: self._on_replayed(entries, result)),
                      self._guard(self._on_op_failed))

    def _on_migrated(self, uploaded: list[str]) -> None:
        self._migrating = False
        self._mark_migrated()
        logger.info("Migrated {} preferences to user scope", len(uploaded))
        self.refresh()

    def _on_replayed(self, entries: Mapping[str, Mapping[str, Any]],
                     result: tuple[list[str], list[tuple[str, str]]]) -> None:
        done, dropped = result
        for key in done:
            entry = entries[key]
            self._journal.discard_if(key, str(entry["op"]), entry.get("value"))
        for key, reason in dropped:
            entry = entries[key]
            self._journal.discard_if(key, str(entry["op"]), entry.get("value"))
            self._notify("Offline change not saved", f"{key}: {reason}")
        if done or dropped:
            self.refresh()

    def _on_op_failed(self, exc: Exception) -> None:
        self._migrating = False  # a transient failure: retried on the next refresh
        logger.info("Scoped settings sync deferred: {}", exc)


def build_scoped_backend(local: Any, *, identity: str, bind: str,
                         user_preferences: Callable[[], Mapping[str, Any]]) -> ScopedBackend:
    from lightfall.scopes.client import ScopesClient
    from lightfall.scopes.journal import journal_path
    from lightfall.scopes.site import default_cache_dir

    directory = default_cache_dir()
    return ScopedBackend(local, client=ScopesClient(), cache=SnapshotCache(directory),
                         journal=WriteJournal(journal_path(directory, identity)),
                         bind=bind, identity=identity, user_preferences=user_preferences)

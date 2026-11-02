"""Publishing actions and the rows the "Shared content" UI and the agent tools list.

No widgets here: everything may run on a worker thread (the network calls must). The
client is a ``ScopesClient``; its ``ScopesError`` passes through for the caller to show
with ``describe_error``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lightfall.content.publish import Bundle, LocalItem, make_manifest
from lightfall.content.store import (
    CONTENT_TYPES,
    ContentStore,
    _is_shared_scope,
    default_content_dir,
)
from lightfall.scopes.client import ContentPolicy
from lightfall.utils.logging import logger

PUBLISH_ROLE = "maintainer"
ADMIN_ROLE = "admin"  # admins and owners rank the same; policy limits don't apply to them


def _role_at_least(role: str | None, needed: str) -> bool:
    try:
        from lightfall_settings import role_at_least

        return role_at_least(role, needed)  # type: ignore[arg-type]
    except (ImportError, KeyError):  # no scopes support, or a role this client doesn't know
        return False


def publishable_scopes(chain: Sequence[str], roles: Mapping[str, str | None]) -> list[str]:
    """Shared scopes of ``chain`` where the user is maintainer or above, most specific first."""
    return [s for s in reversed(tuple(chain))
            if _is_shared_scope(s) and _role_at_least(roles.get(s), PUBLISH_ROLE)]


def is_admin(scope: str, roles: Mapping[str, str | None]) -> bool:
    """True for an admin or owner of ``scope`` (the server's policy limits skip them)."""
    return _role_at_least(roles.get(scope), ADMIN_ROLE)


def current_scope_state() -> tuple[tuple[str, ...], dict[str, str | None], ContentPolicy]:
    """``(chain, roles, policy)`` while scopes come from the server, else ``((), {}, default)``.

    Reads the manager's frozen ``ScopeInfo``, so any thread may call it; never raises.
    """
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        info = PreferencesManager.get_instance().scope_info
        if info.mode == "server":
            return tuple(info.chain), dict(info.roles), info.content_policy
    except Exception as e:
        logger.debug("No scope state: {}", e)
    return (), {}, ContentPolicy()


def refresh_sync() -> None:
    """Ask the running ``ContentSync`` (if any) to sync now; never raises.

    From a worker thread the request is queued to the sync's own thread.
    """
    try:
        from lightfall.content.service import ContentSync
        from lightfall.core.services import ServiceRegistry

        sync = ServiceRegistry.get_instance().get(ContentSync, None)
        if sync is None:
            return
        from PySide6.QtCore import QMetaObject, QObject, Qt, QThread

        if isinstance(sync, QObject) and sync.thread() is not QThread.currentThread():
            QMetaObject.invokeMethod(sync, "refresh", Qt.ConnectionType.QueuedConnection)
        else:
            sync.refresh()
    except Exception as e:
        logger.warning("Could not refresh scoped content: {}", e)


def publish(client: Any, item: LocalItem, bundle: Bundle, scope: str, *, message: str,
            author: str | None, agent: bool = False) -> dict[str, Any]:
    """Upload ``bundle`` (already built, hashed and shown) as ``item`` at ``scope``.

    The manifest's ``local_commit`` is the bundle's (read when it was built), so an edit
    committed since doesn't relabel the old bytes. Network: worker thread only.
    """
    manifest = make_manifest(item, bundle, message=message, author=author, agent=agent)
    record = client.publish_content(scope, item.type, item.name, manifest, bundle.path,
                                    agent=agent)
    refresh_sync()
    return record


def retract(client: Any, scope: str, type_: str, name: str, *,
            agent: bool = False) -> dict[str, Any]:
    record = client.retract_content(scope, type_, name, agent=agent)
    refresh_sync()
    return record


def restore(client: Any, scope: str, type_: str, name: str, revision: int, *,
            agent: bool = False) -> dict[str, Any]:
    record = client.restore_content(scope, type_, name, revision, agent=agent)
    refresh_sync()
    return record


def history(client: Any, scope: str, type_: str, name: str) -> list[dict[str, Any]]:
    return client.content_history(scope, type_, name)


# -- content rows -----------------------------------------------------------------------

@dataclass(frozen=True)
class ContentRow:
    type: str
    name: str
    source: str  # "core", "package", "plugin", "local", or a shared scope
    revision: int | None  # synced rows only
    path: Path | None  # entry point; None for core/package plans
    shadows: tuple[str, ...]  # sources this row's item wins over, lowest first
    error: str | None


# Loader labels -> one vocabulary: skills say "builtin"/"user" and call an endstation
# package's skill dirs "beamline"; agents say "user" for ~/lightfall.
_SOURCE = {"builtin": "core", "user": "local", "beamline": "package"}


def _norm(source: str) -> str:
    return _SOURCE.get(source, source)


def _unique(sources: Sequence[str], winner: str) -> tuple[str, ...]:
    return tuple(s for s in dict.fromkeys(_norm(x) for x in sources) if s != winner)


class _Revisions:
    """Revision of a synced row: from the revision folder its path is in, else the index."""

    def __init__(self, store: ContentStore) -> None:
        self._store = store
        self._listings: dict[str, Any] = {}

    def __call__(self, type_: str, name: str, source: str, path: Path | None) -> int | None:
        if not _is_shared_scope(source):
            return None
        if path is not None:
            base = self._store.scope_dir(source) / type_
            try:
                folder = path.relative_to(base).parts[0]
            except (ValueError, IndexError):
                folder = ""
            stem, sep, rev = folder.rpartition("@")
            if sep and stem == name and rev.isdigit():
                return int(rev)
        if source not in self._listings:
            self._listings[source] = self._store.listing(source)
        listing = self._listings[source]
        for item in listing.items if listing is not None else ():
            if item.type == type_ and item.name == name:
                return item.revision
        return None


def _clash_shadows() -> dict[tuple[str, str, str], list[str]]:
    from lightfall.content.clashes import ClashLog

    out: dict[tuple[str, str, str], list[str]] = {}
    for c in ClashLog.get_instance().all():
        out.setdefault((c.type, c.name, _norm(c.winner)), []).append(c.loser)
    return out


def _plan_rows(rev: _Revisions, clashes: dict) -> list[ContentRow]:
    from lightfall.acquire.plans.registry import PlanRegistry
    from lightfall.acquire.plans.user_plans import UserPlanService

    service = UserPlanService._instance  # don't create one (and ~/lightfall/plans) to list
    rows = []
    for info in PlanRegistry.get_instance().list_plans():
        source = _norm(info.source)
        path = None
        if source not in ("core", "package") and service is not None:
            path = service.loaded_plan_path(info.name)
        rows.append(ContentRow("plan", info.name, source, rev("plan", info.name, source, path),
                               path, _unique(clashes.get(("plan", info.name, source), []),
                                             source), None))
    return rows


def _plugin_rows(rev: _Revisions, clashes: dict) -> list[ContentRow]:
    from lightfall.plugins.user_plugins import UserPluginService

    service = UserPluginService._instance
    if service is None:
        return []
    rows = []
    for info in service.get_loaded_plugins():
        if info.is_temp:
            continue
        name, source = info.plugin_name, _norm(info.source)
        rows.append(ContentRow("plugin", name, source, rev("plugin", name, source, info.file_path),
                               info.file_path,
                               _unique(clashes.get(("plugin", name, source), []), source),
                               info.load_error))
    return rows


def _agent_rows(rev: _Revisions) -> list[ContentRow]:
    from lightfall.agents.registry import AgentSpecRegistry

    registry = AgentSpecRegistry.get_instance()
    files = registry.all_scope_files()
    rows = []
    for spec in registry.specs():
        source = _norm(spec.scope)
        defined = [s for s, _ in files.get(spec.name, [])]
        rows.append(ContentRow("agent", spec.name, source,
                               rev("agent", spec.name, source, spec.source_path),
                               spec.source_path, _unique(defined, source), None))
    for path, message in registry.errors():
        for name, entries in files.items():
            for scope, entry in entries:
                if entry == path:
                    source = _norm(scope)
                    rows.append(ContentRow("agent", name, source,
                                           rev("agent", name, source, path), path, (),
                                           message))
    return rows


def _skill_rows(rev: _Revisions) -> list[ContentRow]:
    from lightfall.agents import skills_store

    resolved = skills_store.resolve_skills()
    sources = skills_store.skill_sources()
    rows = []
    for info in skills_store.skill_catalog().values():
        # Package skills are listed under namespaced ids too; keep only the winner.
        if info.source == "package" and resolved.get(info.name) != info.path:
            continue
        source = _norm(info.source)
        rows.append(ContentRow("skill", info.name, source,
                               rev("skill", info.name, source, info.path), info.path,
                               _unique(sources.get(info.name, [])[:-1], source), None))
    return rows


def content_rows(*, store: ContentStore | None = None) -> list[ContentRow]:
    """Every plan, plugin, agent and skill the running loaders hold, with its source.

    Reads only services that already exist (nothing is created to be listed). A type
    whose loader fails to answer is logged and left out. Worker-thread safe as far as
    the loaders' own listings are (they're read, not changed).
    """
    rev = _Revisions(store if store is not None else ContentStore(default_content_dir()))
    try:
        clashes = _clash_shadows()
    except Exception as e:
        logger.warning("Could not read content clashes: {}", e)
        clashes = {}
    rows: list[ContentRow] = []
    for type_, build in (("plan", lambda: _plan_rows(rev, clashes)),
                         ("plugin", lambda: _plugin_rows(rev, clashes)),
                         ("agent", lambda: _agent_rows(rev)),
                         ("skill", lambda: _skill_rows(rev))):
        try:
            rows.extend(build())
        except Exception as e:
            logger.warning("Could not list {} content: {}", type_, e)
    order = {t: i for i, t in enumerate(CONTENT_TYPES)}
    return sorted(rows, key=lambda r: (order[r.type], r.name.lower(), r.source))

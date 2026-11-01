"""Which plugins load: the scoped, item-merged ``plugins.enabled`` setting.

``plugins.enabled`` holds ``{unique_id: bool}`` per scope (facility, beamline, branch,
this console, user); the resolver merges it item by item, so a user's ``{"x": True}``
wins over a beamline's ``{"x": False}`` for that user only. An id no scope mentions
uses its code default: on, except ``DEFAULT_DISABLED_PLUGIN_IDS``. The Plugins
settings page (``settings:plugins``) is never disabled.

Timing: enablement changes apply at the next start. Plugins that load before login
(preload plugins, the built-in auth providers) read what this console sees then: its
own YAML and any local scope files. The post-login wave re-reads enablement when it
starts (``PluginLoader._apply_enablement``), so it sees server scopes if they have
resolved by then; a plugin already loaded stays loaded until the next start.

Migration from the old per-user ``disabled_plugins`` list (``migrate_disabled_plugins``)
writes at ``prefs.default_scope()``, where the value is actually read:

* before login (and with scopes off or file-based) that is ``"local"``, this console's
  YAML, which the pre-login readers use;
* once server scopes are online it is the user's scope. There the console YAML is only
  a fallback below every scope, so the YAML copy alone would be ignored as soon as any
  scope sets ``plugins.enabled``. ScopedBackend's own YAML upload
  (``scopes.ops.migrate_user_prefs``) skips beamline-capable keys such as
  ``plugins.enabled``, so it never carries this value; no double write.

``start_plugin_migration`` therefore runs it at startup (for ``"local"``) and again each
time server scopes resolve (for the user's scope). The marker ``_plugins_enabled_migrated``
lists the targets already migrated, so each target is migrated once and later runs are
no-ops. It is a local-only key (leading underscore): it lives in this console's YAML in
every mode, next to the YAML the migration reads. Offline (cached) scopes are skipped and
retried at the next resolution, so a stale cached user scope is never overwritten. Values
already set at the target win over migrated ones; ``disabled_plugins`` stays in the YAML,
unread by runtime code.
"""
from __future__ import annotations

import weakref
from collections.abc import Iterable
from typing import Any

from lightfall.scopes.enablement import enabled_overrides
from lightfall.utils.logging import logger

PLUGINS_KEY = "plugins.enabled"

# Plugins that ship disabled; a scope's {id: True} turns one on.
DEFAULT_DISABLED_PLUGIN_IDS: tuple[str, ...] = (
    "auth_provider:local",
    "statusbar:memory_status",  # developer memory indicator
)
# Never disabled: without it nobody could turn plugins back on.
PROTECTED_PLUGIN_IDS: frozenset[str] = frozenset({"settings:plugins"})

MIGRATED_PREF = "_plugins_enabled_migrated"
_LEGACY_KEY = "disabled_plugins"
# Which default-disabled ids the old seeding had added to disabled_plugins (a list).
# Older installs stored True, which meant only the original default, auth_provider:local.
_SEEDED_PREF = "default_disabled_plugins_seeded"
_LEGACY_SEEDED_IDS = ("auth_provider:local",)


def default_enabled(unique_id: str) -> bool:
    """The code default: on, except the plugins that ship disabled."""
    return unique_id not in DEFAULT_DISABLED_PLUGIN_IDS


def _enabled(unique_id: str, overrides: dict[str, bool]) -> bool:
    if unique_id in PROTECTED_PLUGIN_IDS:
        return True
    return overrides.get(unique_id, default_enabled(unique_id))


def plugin_enabled(unique_id: str) -> bool:
    """Whether the plugin ``type:name`` should load, per the resolved setting."""
    return _enabled(unique_id, enabled_overrides(PLUGINS_KEY))


def disabled_plugin_ids(known_ids: Iterable[str]) -> set[str]:
    """The ids among ``known_ids`` that should not load (one settings read)."""
    overrides = enabled_overrides(PLUGINS_KEY)
    return {uid for uid in known_ids if not _enabled(uid, overrides)}


# ── One-time migration from disabled_plugins ────────────────────────────


def _migrated_targets(prefs: Any) -> set[str]:
    done = prefs.get(MIGRATED_PREF, None)
    return {str(x) for x in done} if isinstance(done, list) else set()


def _seeded_ids(prefs: Any) -> set[str]:
    marker = prefs.get(_SEEDED_PREF, None)
    if marker is True:
        return set(_LEGACY_SEEDED_IDS)
    return {str(x) for x in marker} if isinstance(marker, list) else set()


def _legacy_items(prefs: Any) -> dict[str, bool]:
    """What the old ``disabled_plugins`` list and seeding marker meant, as overrides."""
    disabled = prefs.get(_LEGACY_KEY, None)
    disabled = {str(x) for x in disabled} if isinstance(disabled, list) else set()
    items = {uid: False for uid in sorted(disabled)
             if uid not in DEFAULT_DISABLED_PLUGIN_IDS and uid not in PROTECTED_PLUGIN_IDS}
    # Seeded off, then switched back on in the old Plugins page.
    for uid in DEFAULT_DISABLED_PLUGIN_IDS:
        if uid in _seeded_ids(prefs) and uid not in disabled:
            items[uid] = True
    return items


def _online(prefs: Any) -> bool:
    """False while server scopes resolve from the offline cache (may be stale)."""
    if prefs.scope_info.mode != "server":
        return True
    store = getattr(prefs.scoped_backend, "store", None)
    return store is not None and not store.offline


def migrate_disabled_plugins(prefs: Any) -> bool:
    """Carry ``disabled_plugins`` into ``plugins.enabled`` at ``prefs.default_scope()``.

    Once per target (see the module docstring). Returns True if this run migrated.
    """
    target = prefs.default_scope()
    done = _migrated_targets(prefs)
    if target in done or not _online(prefs) or not prefs.writable_at(PLUGINS_KEY, target):
        return False
    existing = prefs.layer_value(PLUGINS_KEY, target)
    existing = dict(existing) if isinstance(existing, dict) else {}
    merged = {**_legacy_items(prefs), **existing}  # values already set here win
    if merged != existing:
        prefs.set(PLUGINS_KEY, merged, scope=target)
        logger.info("Migrated disabled_plugins to {} at {}: {}", PLUGINS_KEY, target,
                    {k: v for k, v in merged.items() if k not in existing})
    prefs.set(MIGRATED_PREF, sorted(done | {target}))
    return True


_HOOKED: weakref.WeakSet[Any] = weakref.WeakSet()


def start_plugin_migration(prefs: Any = None) -> None:
    """Migrate now (this console's YAML before login) and whenever scopes resolve.

    Never raises: a failed migration leaves the old values unread and is retried at
    the next start.
    """
    try:
        if prefs is None:
            from lightfall.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
    except Exception as e:  # noqa: BLE001 - early startup, tests
        logger.debug("Cannot migrate disabled plugins: {}", e)
        return

    def run(_info: Any = None) -> None:
        try:
            migrate_disabled_plugins(prefs)
        except Exception:  # noqa: BLE001 - must not break startup or the scope signal
            logger.exception("Migrating disabled_plugins failed")

    run()
    if prefs not in _HOOKED:
        _HOOKED.add(prefs)
        prefs.scopes_changed.connect(run)

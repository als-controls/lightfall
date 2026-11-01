"""Agent plugin registry — slimmed singleton replacing SkillRegistry + MCPToolRegistry.

Holds registered ToolPlugin instances. The settings UI reads from it for
the enable/disable table. The agent-construction path (claude/agent.py +
claude_panel.py) reads `enabled_plugins()` to materialize SKILL.md files
and assemble per-plugin MCP servers.

Enablement model (opt-out semantics so newly-added plugins are enabled by
default unless a scope says otherwise): the scoped, item-merged ``tools.enabled``
setting holds ``{plugin name: bool}`` per layer (user, branch, beamline, facility). A
plugin no layer mentions uses its ``enabled_by_default``. ``enabled_plugins()`` reads
the resolved map on every call, so a change applies to the next assistant session
without a restart.

The old ``disabled_tool_plugins`` / ``forced_enabled_tool_plugins`` lists migrate once
into ``tools.enabled`` (``migrate_tool_lists``, run at startup and on each online scope
fetch by ``start_tool_migration``) and are not read afterwards. Before that, the
even older ``enabled_tool_plugins`` allow-list is converted into those two lists on first
read (``_migrate_legacy_pref_if_needed``), as it always was.
"""

from __future__ import annotations

import threading
import weakref
from typing import TYPE_CHECKING, Any

from lightfall.scopes.enablement import item_enabled
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.tool_plugin import ToolPlugin


DISABLED_PLUGINS_PREF: str = "disabled_tool_plugins"
FORCED_ENABLED_PLUGINS_PREF: str = "forced_enabled_tool_plugins"
LEGACY_ENABLED_PLUGINS_PREF: str = "enabled_tool_plugins"
LEGACY_ENABLED_SKILLS_PREF: str = "enabled_skills"  # SkillRegistry-era
TOOLS_KEY: str = "tools.enabled"
# Targets (scope ids) whose copy of the two lists has already moved into tools.enabled.
# Local-only key (leading underscore): it lives in this console's YAML in every mode.
MIGRATED_PREF: str = "_tools_enabled_migrated"


class ToolRegistry:
    """Singleton registry of ToolPlugin instances.

    Use ToolRegistry.get_instance() to access. reset_instance() is for tests.
    """

    _instance: ToolRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._plugins: dict[str, ToolPlugin] = {}
        self._legacy_migrated: bool = False

    @classmethod
    def get_instance(cls) -> ToolRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, plugin: ToolPlugin) -> None:
        """Register an ToolPlugin. Replaces any existing plugin with the same name."""
        if plugin.name in self._plugins:
            logger.warning("agent plugin '{}' already registered, replacing", plugin.name)
        self._plugins[plugin.name] = plugin
        logger.debug(
            "Registered agent plugin: {} (category={}, priority={})",
            plugin.name, plugin.category, plugin.priority,
        )

    def unregister(self, name: str) -> bool:
        """Unregister by name. Returns True if found + removed."""
        if name in self._plugins:
            del self._plugins[name]
            logger.debug("Unregistered agent plugin: {}", name)
            return True
        return False

    def get_plugins(self) -> list[ToolPlugin]:
        """All registered plugins (any order)."""
        return list(self._plugins.values())

    def get_plugin(self, name: str) -> ToolPlugin | None:
        return self._plugins.get(name)

    def _migrate_legacy_pref_if_needed(self) -> bool:
        """Convert legacy `enabled_tool_plugins` allow-list into the new
        opt-out + opt-in pair, then delete the legacy key.

        Returns True if it converted the allow-list on this call.

        Why: the legacy pref froze the enabled set, so any plugin registered
        *after* the user saved settings was silently excluded. Splitting into
        explicit opt-out / opt-in makes new plugins fall through to their
        `enabled_by_default` value.
        """
        if self._legacy_migrated:
            return False
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not access PreferencesManager for migration: {}", e)
            return False

        if prefs.get(DISABLED_PLUGINS_PREF) is not None:
            self._legacy_migrated = True
            return False

        legacy = prefs.get(LEGACY_ENABLED_PLUGINS_PREF)
        if not isinstance(legacy, list):
            legacy = prefs.get(LEGACY_ENABLED_SKILLS_PREF)
            if not isinstance(legacy, list):
                return False  # don't mark migrated yet: registry may still be filling

        legacy_set = set(legacy)
        disabled = sorted(
            p.name for p in self._plugins.values()
            if p.enabled_by_default and p.name not in legacy_set
        )
        forced_enabled = sorted(
            p.name for p in self._plugins.values()
            if not p.enabled_by_default and p.name in legacy_set
        )
        prefs.set(DISABLED_PLUGINS_PREF, disabled)
        prefs.set(FORCED_ENABLED_PLUGINS_PREF, forced_enabled)
        prefs.remove(LEGACY_ENABLED_PLUGINS_PREF)
        prefs.remove(LEGACY_ENABLED_SKILLS_PREF)
        self._legacy_migrated = True
        logger.info(
            "Migrated tool-plugin prefs: {} disabled, {} forced-enabled",
            len(disabled), len(forced_enabled),
        )
        return True

    def enabled_plugins(self) -> list[ToolPlugin]:
        """Plugins enabled by the resolved ``tools.enabled`` map, sorted by priority."""
        if self._migrate_legacy_pref_if_needed():
            _migrate_tools_quietly()  # the allow-list just became the two lists: move them on
        result = [
            p for p in self._plugins.values()
            if item_enabled(TOOLS_KEY, p.name, p.enabled_by_default)
        ]
        result.sort(key=lambda p: p.priority)
        return result

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "plugin_count": len(self._plugins),
            "plugins": [p.get_introspection_data() for p in self._plugins.values()],
        }


# -- One-time migration from the two lists ---------------------------------


def _migrated_targets(prefs: Any) -> set[str]:
    done = prefs.get(MIGRATED_PREF, None)
    return {str(x) for x in done} if isinstance(done, list) else set()


def _may_migrate(prefs: Any) -> bool:
    """With server scopes: only once online (an offline-cache user scope may be stale)."""
    if prefs.scope_info.mode != "server":
        return True
    store = getattr(prefs.scoped_backend, "store", None)
    return store is not None and not store.offline


def _legacy_pending(prefs: Any) -> bool:
    """An old allow-list is still waiting for the registry to fill so it can be converted."""
    if prefs.get(DISABLED_PLUGINS_PREF, None) is not None:
        return False
    return any(isinstance(prefs.get(k, None), list)
               for k in (LEGACY_ENABLED_PLUGINS_PREF, LEGACY_ENABLED_SKILLS_PREF))


def _as_names(value: Any) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


def migrate_tool_lists(prefs: Any) -> bool:
    """Carry the two old lists into ``tools.enabled`` at ``prefs.default_scope()``.

    Once per target (marker ``_tools_enabled_migrated``); a name in both lists is off,
    as before. Values already set at the target win. Returns True if this run migrated.
    """
    target = prefs.default_scope()
    done = _migrated_targets(prefs)
    if (target in done or not _may_migrate(prefs) or _legacy_pending(prefs)
            or not prefs.writable_at(TOOLS_KEY, target)):
        return False
    disabled = _as_names(prefs.get(DISABLED_PLUGINS_PREF, None))
    forced = _as_names(prefs.get(FORCED_ENABLED_PLUGINS_PREF, None))
    legacy = {**dict.fromkeys(sorted(forced), True), **dict.fromkeys(sorted(disabled), False)}
    existing = prefs.layer_value(TOOLS_KEY, target)
    existing = dict(existing) if isinstance(existing, dict) else {}
    merged = {**legacy, **existing}
    if merged != existing:
        prefs.set(TOOLS_KEY, merged, scope=target)
        logger.info("Migrated tool-plugin lists to {} at {}: {}", TOOLS_KEY, target,
                    {k: v for k, v in merged.items() if k not in existing})
    prefs.set(MIGRATED_PREF, sorted(done | {target}))
    return True


def _migrate_tools_quietly(prefs: Any = None) -> None:
    """Migrate; never raise (a failure leaves the old lists unread until the next try)."""
    try:
        if prefs is None:
            from lightfall.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
        migrate_tool_lists(prefs)
    except Exception:  # noqa: BLE001 - must not break startup, a session or the scope signal
        logger.debug("Migrating tool-plugin lists failed", exc_info=True)


_HOOKED: weakref.WeakSet[Any] = weakref.WeakSet()


def start_tool_migration(prefs: Any = None) -> None:
    """Migrate now (this console's YAML before login) and on each online scope fetch."""
    try:
        if prefs is None:
            from lightfall.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
    except Exception as e:  # noqa: BLE001 - early startup, tests
        logger.debug("Cannot migrate tool-plugin lists: {}", e)
        return
    _migrate_tools_quietly(prefs)
    if prefs not in _HOOKED:
        _HOOKED.add(prefs)
        prefs.scopes_online.connect(lambda _info=None: _migrate_tools_quietly(prefs))

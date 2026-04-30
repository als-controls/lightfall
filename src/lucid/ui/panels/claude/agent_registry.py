"""Agent plugin registry — slimmed singleton replacing SkillRegistry + MCPToolRegistry.

Holds registered AgentPlugin instances. The settings UI reads from it for
the enable/disable table. The agent-construction path (claude/agent.py +
claude_panel.py) reads `enabled_plugins()` to materialize SKILL.md files
and assemble per-plugin MCP servers.

Preference model (opt-out semantics so newly-added plugins are enabled
by default unless the user explicitly disables them):

- `disabled_tool_plugins`: explicit user opt-outs (default-enabled plugins
  the user has unchecked).
- `forced_enabled_tool_plugins`: explicit user opt-ins (default-disabled
  plugins the user has checked).

The legacy `enabled_tool_plugins` allow-list pref (full enabled set) is
migrated once on first read into the two new keys.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.agent_plugin import AgentPlugin


DISABLED_PLUGINS_PREF: str = "disabled_tool_plugins"
FORCED_ENABLED_PLUGINS_PREF: str = "forced_enabled_tool_plugins"
LEGACY_ENABLED_PLUGINS_PREF: str = "enabled_tool_plugins"
LEGACY_ENABLED_SKILLS_PREF: str = "enabled_skills"  # SkillRegistry-era


class AgentRegistry:
    """Singleton registry of AgentPlugin instances.

    Use AgentRegistry.get_instance() to access. reset_instance() is for tests.
    """

    _instance: "AgentRegistry | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._plugins: dict[str, AgentPlugin] = {}
        self._legacy_migrated: bool = False

    @classmethod
    def get_instance(cls) -> "AgentRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, plugin: "AgentPlugin") -> None:
        """Register an AgentPlugin. Replaces any existing plugin with the same name."""
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

    def get_plugins(self) -> list["AgentPlugin"]:
        """All registered plugins (any order)."""
        return list(self._plugins.values())

    def get_plugin(self, name: str) -> "AgentPlugin | None":
        return self._plugins.get(name)

    def _read_list_pref(self, key: str) -> list[str] | None:
        """Read a list-valued preference. Returns None if unset/unreadable."""
        try:
            from lucid.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            value = prefs.get(key)
            if value is None or isinstance(value, list):
                return value
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load {}: {}", key, e)
        return None

    def _migrate_legacy_pref_if_needed(self) -> None:
        """Convert legacy `enabled_tool_plugins` allow-list into the new
        opt-out + opt-in pair, then delete the legacy key.

        Why: the legacy pref froze the enabled set, so any plugin registered
        *after* the user saved settings was silently excluded. Splitting into
        explicit opt-out / opt-in makes new plugins fall through to their
        `enabled_by_default` value.
        """
        if self._legacy_migrated:
            return
        try:
            from lucid.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not access PreferencesManager for migration: {}", e)
            return

        if prefs.get(DISABLED_PLUGINS_PREF) is not None:
            self._legacy_migrated = True
            return

        legacy = prefs.get(LEGACY_ENABLED_PLUGINS_PREF)
        if not isinstance(legacy, list):
            legacy = prefs.get(LEGACY_ENABLED_SKILLS_PREF)
            if not isinstance(legacy, list):
                return  # don't mark migrated yet — registry may still be filling

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

    def enabled_plugins(self) -> list["AgentPlugin"]:
        """Plugins enabled by current preferences, sorted by priority (ascending)."""
        self._migrate_legacy_pref_if_needed()
        disabled = set(self._read_list_pref(DISABLED_PLUGINS_PREF) or [])
        forced_enabled = set(self._read_list_pref(FORCED_ENABLED_PLUGINS_PREF) or [])
        result = [
            p for p in self._plugins.values()
            if p.name not in disabled
            and (p.enabled_by_default or p.name in forced_enabled)
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

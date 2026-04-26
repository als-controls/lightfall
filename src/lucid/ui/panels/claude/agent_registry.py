"""Agent plugin registry — slimmed singleton replacing SkillRegistry + MCPToolRegistry.

Holds registered AgentPlugin instances. The settings UI reads from it for
the enable/disable table. The agent-construction path (claude/agent.py +
claude_panel.py) reads `enabled_plugins()` to materialize SKILL.md files
and assemble per-plugin MCP servers.

The preference key `enabled_tool_plugins` is retained from the previous
SkillRegistry/MCPToolRegistry world for backward-compat with existing user
settings — set semantics, plugin names unchanged.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.agent_plugin import AgentPlugin


ENABLED_PLUGINS_PREF: str = "enabled_tool_plugins"


class AgentRegistry:
    """Singleton registry of AgentPlugin instances.

    Use AgentRegistry.get_instance() to access. reset_instance() is for tests.
    """

    _instance: "AgentRegistry | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._plugins: dict[str, AgentPlugin] = {}

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

    def _get_enabled_pref(self) -> list[str] | None:
        """Read the enabled_tool_plugins preference. Returns None if not set."""
        try:
            from lucid.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            value = prefs.get(ENABLED_PLUGINS_PREF)
            if value is None or isinstance(value, list):
                return value
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load {}: {}", ENABLED_PLUGINS_PREF, e)
        return None

    def enabled_plugins(self) -> list["AgentPlugin"]:
        """Plugins enabled by current preferences, sorted by priority (ascending)."""
        pref = self._get_enabled_pref()
        if pref is None:
            enabled_names = {p.name for p in self._plugins.values() if p.enabled_by_default}
        else:
            enabled_names = set(pref) & set(self._plugins.keys())
        result = [p for name, p in self._plugins.items() if name in enabled_names]
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

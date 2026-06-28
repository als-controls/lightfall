"""Singleton registry of MonitorPlugins. Mirrors
src/lightfall/ui/panels/claude/agent_registry.py (opt-out preference model)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.monitor.feed import MonitorFeed
    from lightfall.monitor.monitor_plugin import MonitorPlugin

DISABLED_MONITORS_PREF = "disabled_monitor_plugins"
FORCED_ENABLED_MONITORS_PREF = "forced_enabled_monitor_plugins"


class MonitorRegistry:
    _instance: MonitorRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._plugins: dict[str, MonitorPlugin] = {}
        self._feed_cache: dict[str, list[MonitorFeed]] = {}

    @classmethod
    def get_instance(cls) -> MonitorRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, plugin: MonitorPlugin) -> None:
        if plugin.name in self._plugins:
            logger.warning("monitor plugin '{}' already registered, replacing", plugin.name)
        self._plugins[plugin.name] = plugin
        self._feed_cache.pop(plugin.name, None)
        logger.debug("Registered monitor plugin: {} (priority={})", plugin.name, plugin.priority)

    def unregister(self, name: str) -> bool:
        self._feed_cache.pop(name, None)
        return self._plugins.pop(name, None) is not None

    def get_plugins(self) -> list[MonitorPlugin]:
        return list(self._plugins.values())

    def _read_list_pref(self, key: str) -> list[str]:
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            value = PreferencesManager.get_instance().get(key)
            if isinstance(value, list):
                return value
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load {}: {}", key, e)
        return []

    def enabled_plugins(self) -> list[MonitorPlugin]:
        disabled = set(self._read_list_pref(DISABLED_MONITORS_PREF))
        forced = set(self._read_list_pref(FORCED_ENABLED_MONITORS_PREF))
        result = [
            p for p in self._plugins.values()
            if p.name not in disabled and (p.enabled_by_default or p.name in forced)
        ]
        result.sort(key=lambda p: p.priority)
        return result

    def enabled_feeds(self) -> list[MonitorFeed]:
        feeds: list[MonitorFeed] = []
        for plugin in self.enabled_plugins():
            cached = self._feed_cache.get(plugin.name)
            if cached is None:
                cached = plugin.create_feeds()
                self._feed_cache[plugin.name] = cached
            feeds.extend(cached)
        return feeds

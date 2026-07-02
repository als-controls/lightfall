"""Plugin type for contributing MonitorFeeds. Mirrors AgentPlugin so
behaviour and settings are predictable (see src/lightfall/plugins/agent_plugin.py)."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from lightfall.monitor.feed import MonitorFeed
from lightfall.plugins.types import PluginType


class MonitorPlugin(PluginType):
    """Contributes one or more MonitorFeeds when enabled."""

    type_name: ClassVar[str] = "monitor"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier (≤64 chars, lowercase + _/-)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description shown in the settings UI."""
        ...

    @property
    def display_name(self) -> str:
        return self.name.replace("_", " ").title()

    @property
    def category(self) -> str:
        return "general"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        return 100

    @abstractmethod
    def create_feeds(self) -> list[MonitorFeed]:
        """Return the MonitorFeed instances this plugin contributes."""
        ...

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "enabled_by_default": self.enabled_by_default,
            "priority": self.priority,
            "feed_count": len(self.create_feeds()),
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

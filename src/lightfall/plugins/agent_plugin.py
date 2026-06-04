"""Unified plugin type for plugins that extend the embedded Claude agent.

Replaces both SkillPlugin and MCPToolPlugin. One AgentPlugin contributes
an optional SKILL.md (via get_system_prompt) and/or an in-process MCP
server (via create_tools). One settings toggle controls both.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from lightfall.plugins.types import PluginType


class AgentPlugin(PluginType):
    """Extends the embedded Claude agent with an optional skill prompt and/or
    a bag of MCP tools.

    When enabled, contributes:

    - a SKILL.md (if get_system_prompt() returns non-empty text), materialized
      into the per-session SDK plugin dir at agent construction time;
    - an in-process MCP server (if create_tools() returns tools), registered
      as mcp_servers[plugin.name] with namespace mcp__<plugin.name>__*.

    See docs/superpowers/specs/2026-04-25-lightfall-sdk-native-plugins-design.md.
    """

    type_name: ClassVar[str] = "agent"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier.

        ≤64 chars. Lowercase + hyphens/underscores. Used as:
        - the manifest entry name,
        - the SKILL.md frontmatter `name` field (with underscores → hyphens
          conversion at materialization, per spec Open question),
        - the MCP server name (mcp__<name>__*),
        - the settings UI preference identifier.
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description shown in settings UI and in SKILL.md frontmatter.

        Truncated to 1024 chars at SKILL.md materialization time (SDK limit).
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for the settings UI."""
        return self.name.replace("_", " ").title()

    @property
    def category(self) -> str:
        """Settings-UI grouping. Common values: general, devices, acquisition,
        operations, development."""
        return "general"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        """Sort order in settings UI (lower = first)."""
        return 100

    def get_system_prompt(self) -> str:
        """Return the SKILL.md body. Empty string = no skill contribution."""
        return ""

    def create_tools(self) -> list[Any]:
        """Return @tool-decorated callables. Empty = no MCP server contribution."""
        return []

    def get_references_dir(self) -> Path | None:
        """Optional package directory containing supplementary docs.

        If returned, files are copied to <session_plugin_dir>/skills/<name>/references/
        at session start, where the SDK Skill tool loads them lazily on demand.
        """
        return None

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "enabled_by_default": self.enabled_by_default,
            "priority": self.priority,
            "has_prompt": bool(self.get_system_prompt().strip()),
            "has_tools": len(self.create_tools()) > 0,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

"""MCP Tool plugin type.

MCPToolPlugin is the plugin type for MCP (Model Context Protocol) tools
that extend the Claude assistant's capabilities. Plugins implementing this
interface provide tool functions that the Claude agent can call.

This is also the base class for SkillPlugin, which adds system prompt capabilities.
All tool plugins (mcp_tool and skill) can be enabled/disabled via the settings UI.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from lucid.plugins.types import PluginType


class MCPToolPlugin(PluginType):
    """Abstract base for MCP tool plugins.

    MCP tool plugins provide contextual tools for the Claude assistant.
    Each plugin creates tool functions that are registered with the agent
    and can be called during conversations.

    Class Attributes:
        type_name: "mcp_tool" - identifies this as an MCP tool plugin.
        is_singleton: True - MCP tool plugins are singletons.

    Properties for Enable/Disable:
        display_name: Human-readable name for settings UI.
        description: Description shown in settings UI (abstract).
        category: Grouping for settings UI (default: "general").
        enabled_by_default: Whether plugin is on by default (default: True).
        priority: Sort order (lower = higher priority, default: 100).

    Lifecycle:
        1. Plugin is instantiated on load
        2. create_tools() is called to get the tool functions
        3. Tools are registered with the Claude agent (if enabled)
        4. Tools can be called by Claude during conversations

    Tool Creation Pattern:
        Tool methods should use the @tool decorator from claude_agent_sdk
        and can reference self for accessing application context through
        support methods.

    Example implementation::

        from lucid.plugins.mcp_tool import MCPToolPlugin
        from claude_agent_sdk import tool

        class BlueskyToolPlugin(MCPToolPlugin):
            @property
            def name(self) -> str:
                return "bluesky"

            @property
            def description(self) -> str:
                return "Tools for running Bluesky scans"

            @property
            def category(self) -> str:
                return "acquisition"

            def __init__(self):
                super().__init__()
                self._engine = None

            # Support method for getting context
            def _get_engine(self):
                if self._engine is None:
                    from lucid.acquire.engine import get_engine
                    self._engine = get_engine()
                return self._engine

            # Called once on plugin load
            def create_tools(self) -> list:
                @tool(
                    name="get_engine_state",
                    description="Get current RunEngine state",
                    input_schema={"type": "object", "properties": {}}
                )
                async def get_engine_state(args: dict) -> dict:
                    engine = self._get_engine()
                    return {"state": engine.state.name}

                return [get_engine_state]

    Registration in manifest::

        from lucid.plugins import PluginManifest, PluginEntry

        manifest = PluginManifest(
            name="my-beamline",
            plugins=[
                PluginEntry(
                    type_name="mcp_tool",
                    name="bluesky_tools",
                    import_path="my_beamline.plugins:BlueskyToolPlugin",
                ),
            ]
        )
    """

    type_name: ClassVar[str] = "mcp_tool"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this MCP tool plugin.

        This should be unique within the mcp_tool type and is used to
        identify the plugin in the registry.

        Returns:
            Plugin name string.
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this plugin provides.

        This is shown in the settings UI to help users understand
        what enabling this plugin will do.

        Returns:
            Description string.
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown in settings UI.

        Override this to provide a custom display name. By default,
        converts the name to title case.

        Returns:
            Display name for the settings UI.
        """
        return self.name.replace("_", " ").title()

    @property
    def category(self) -> str:
        """Category for grouping plugins in the settings UI.

        Override this to group related plugins together.
        Common categories: "general", "devices", "acquisition", "analysis".

        Returns:
            Category name. Defaults to "general".
        """
        return "general"

    @property
    def enabled_by_default(self) -> bool:
        """Whether this plugin is enabled by default.

        MCP tool plugins are enabled by default (True). Override this
        to False for plugins that should be opt-in.

        Returns:
            True if enabled by default. Defaults to True.
        """
        return True

    @property
    def priority(self) -> int:
        """Sort order for display and prompt aggregation (lower = higher priority).

        Plugins with lower priority values appear first in the settings UI
        and (for skills) have their prompts appear earlier in the aggregated
        system prompt.

        Returns:
            Priority value. Defaults to 100.
        """
        return 100

    @property
    def tool_description(self) -> str:
        """Human-readable description of the tools provided.

        DEPRECATED: Use 'description' property instead.
        Kept for backward compatibility.

        Returns:
            Description string.
        """
        return self.description

    @abstractmethod
    def create_tools(self) -> list[Any]:
        """Create and return MCP tool functions.

        Called once when the plugin is loaded. Returns a list of
        tool functions that will be registered with the Claude agent.

        Tool functions should be created using the @tool decorator
        from claude_agent_sdk and can reference self for context.

        Returns:
            List of tool functions decorated with @tool.
        """
        ...

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for this plugin.

        Returns:
            Dictionary with plugin metadata.
        """
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "enabled_by_default": self.enabled_by_default,
            "priority": self.priority,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

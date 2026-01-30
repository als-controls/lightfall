"""MCP Tool plugin type.

MCPToolPlugin is the plugin type for MCP (Model Context Protocol) tools
that extend the Claude assistant's capabilities. Plugins implementing this
interface provide tool functions that the Claude agent can call.
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

    Lifecycle:
        1. Plugin is instantiated on load
        2. create_tools() is called to get the tool functions
        3. Tools are registered with the Claude agent
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
    description: ClassVar[str] = "MCP tool plugin for Claude assistant"
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
    def tool_description(self) -> str:
        """Human-readable description of the tools provided.

        Override this to provide a custom description. By default,
        converts the name to a readable format.

        Returns:
            Description string.
        """
        return f"Tools from {self.name}"

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
            "description": self.tool_description,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

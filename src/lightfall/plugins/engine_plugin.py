"""Engine plugin type for execution engines.

EnginePlugin is the plugin type for execution engines. Plugins implementing
this interface provide engine factories that can be discovered and selected
through user preferences.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lightfall.plugins.types import PluginType

if TYPE_CHECKING:
    from lightfall.acquire.engine.base import BaseEngine


class EnginePlugin(PluginType):
    """Abstract base for engine plugins.

    Engine plugins provide execution engines that can be discovered
    and selected through user preferences. Each plugin creates an
    engine instance when activated.

    Class Attributes:
        type_name: "engine" - identifies this as an engine plugin.
        is_singleton: True - only one instance per plugin type.

    Example implementation::

        class MyEnginePlugin(EnginePlugin):
            @property
            def name(self) -> str:
                return "my_engine"

            @property
            def display_name(self) -> str:
                return "My Custom Engine"

            def create_engine(self, **kwargs) -> BaseEngine:
                return MyEngine(**kwargs)

    The plugin can then be registered via a manifest::

        manifest = PluginManifest(
            name="my-beamline-engines",
            plugins=[
                PluginEntry("engine", "my_engine", "my_beamline.engines:MyEnginePlugin"),
            ]
        )
    """

    type_name: ClassVar[str] = "engine"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this engine plugin."""
        return "Execution engine plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Engine identifier used for registration and preferences.

        This should be unique within the engine type and is used to
        identify the engine in the registry and preferences.
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for UI display.

        Override this to provide a custom display name. By default,
        converts the name from snake_case to Title Case.

        Returns:
            Display name string.
        """
        return self.name.replace("_", " ").title()

    @property
    def engine_description(self) -> str:
        """Description of this engine for UI display.

        Override this to provide a description of the engine's
        capabilities and use cases.

        Returns:
            Description text.
        """
        return ""

    @abstractmethod
    def create_engine(self, **kwargs: Any) -> BaseEngine:
        """Create and return an engine instance.

        Called when this engine is selected and activated. The engine
        should be fully initialized and ready to accept procedures.

        Args:
            **kwargs: Engine-specific configuration options.

        Returns:
            The engine instance.
        """
        ...

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with engine information.
        """
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.engine_description,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

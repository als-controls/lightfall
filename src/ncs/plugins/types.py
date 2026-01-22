"""Abstract base class for plugin types.

Plugin types define the interface contract for categories of plugins.
Each plugin type (e.g., PlanPlugin, PanelPlugin) inherits from PluginType
and defines the required interface for that category.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class PluginType(ABC):
    """Abstract base class for all NCS plugins.

    All plugin implementations must inherit from a PluginType subclass.
    The type defines the interface contract for that category of plugins.

    Class Attributes:
        type_name: Unique identifier for this plugin type (e.g., "plan").
        description: Human-readable description of this plugin type.
        is_singleton: Whether only one instance should exist per plugin.

    Example subclass::

        class PlanPlugin(PluginType):
            type_name = "plan"
            description = "Bluesky plan plugin"
            is_singleton = True

            @property
            def name(self) -> str:
                return "my_plan"

            @abstractmethod
            def get_plan_function(self) -> Callable:
                ...
    """

    type_name: ClassVar[str] = "base"
    description: ClassVar[str] = "Base plugin type"
    is_singleton: ClassVar[bool] = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin instance name.

        This should return a unique name within this plugin type.
        """
        ...

    @classmethod
    def validate_class(cls, plugin_class: type) -> bool:
        """Validate that a class is a valid plugin of this type.

        Override in subclasses for type-specific validation.

        Args:
            plugin_class: The class to validate.

        Returns:
            True if the class is valid for this plugin type.
        """
        return issubclass(plugin_class, cls)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Override in subclasses to provide type-specific data.

        Returns:
            Dictionary with plugin information.
        """
        return {
            "type": self.type_name,
            "name": self.name,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

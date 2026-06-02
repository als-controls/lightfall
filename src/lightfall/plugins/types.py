"""Abstract base class for plugin types.

Plugin types define the interface contract for categories of plugins.
Each plugin type (e.g., PlanPlugin, PanelPlugin) inherits from PluginType
and defines the required interface for that category.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar


def _user_plugin_roots() -> list[Path]:
    """Canonical user plugin root directories.

    Used by PluginType.__init_subclass__ to decide whether a newly-defined
    subclass came from a user plugin file (and thus should auto-enqueue).
    """
    home = Path.home()
    roots: list[Path] = []
    for candidate in (home / "lucid" / "plugins", home / ".lucid" / "plugins"):
        try:
            roots.append(candidate.resolve())
        except (OSError, RuntimeError):
            pass
    return roots


def _is_under_user_plugin_dir(p: Path) -> bool:
    try:
        resolved = p.resolve()
    except (OSError, RuntimeError):
        return False
    for root in _user_plugin_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


class PluginType(ABC):
    """Abstract base class for all NCS plugins.

    All plugin implementations must inherit from a PluginType subclass.
    The type defines the interface contract for that category of plugins.

    Class Attributes:
        type_name: Unique identifier for this plugin type (e.g., "plan").
        is_singleton: Whether only one instance should exist per plugin.

    Properties:
        description: Human-readable description of this plugin type.

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
    is_singleton: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__module__ == "__main__" or inspect.isabstract(cls):
            return
        try:
            module_file = Path(inspect.getfile(cls))
        except (TypeError, OSError):
            return
        if not _is_under_user_plugin_dir(module_file):
            return
        try:
            from lucid.plugins.user_plugins import UserPluginService
            UserPluginService.get_instance().enqueue(cls, module_file)
        except Exception:  # noqa: BLE001 — don't crash class definition on plumbing failure
            import logging
            logging.getLogger(__name__).exception("auto-enqueue failed for %s", cls)

    @property
    def description(self) -> str:
        """Human-readable description of this plugin type."""
        return "Base plugin type"

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

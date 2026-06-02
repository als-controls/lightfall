"""Panel plugin type for application panels.

PanelPlugin is the plugin type for application panels. Plugins
implementing this interface provide BasePanel subclasses that are
automatically registered with PanelRegistry when loaded.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lucid.plugins.types import PluginType

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel


class PanelPlugin(PluginType):
    """Abstract base for panel plugins.

    Panel plugins provide BasePanel subclasses that are automatically
    registered with PanelRegistry when the plugin is loaded. Each plugin
    wraps a panel class and provides it to the plugin system.

    Class Attributes:
        type_name: "panel" - identifies this as a panel plugin.
        is_singleton: True - panel plugins are singletons (the plugin
            instance is singleton; the panel class it provides can still
            create multiple panel instances based on its own singleton flag).

    Lifecycle:
        1. Plugin is instantiated on load (preload=True recommended)
        2. get_panel_class() is called to get the BasePanel subclass
        3. Panel class is registered with PanelRegistry
        4. Panel can then be instantiated via PanelRegistry.create()

    Example implementation::

        class MyPanelPlugin(PanelPlugin):
            @property
            def name(self) -> str:
                return "my_panel"

            def get_panel_class(self) -> type[BasePanel]:
                from my_package.panels import MyPanel
                return MyPanel

    In builtin_manifest.py::

        PluginEntry(
            type_name="panel",
            name="my_panel",
            import_path="my_package.plugins:MyPanelPlugin",
            preload=True,  # Recommended for panels
        ),
    """

    type_name: ClassVar[str] = "panel"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        """Human-readable description of this panel plugin."""
        return "Application panel plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this panel plugin.

        This should be unique within the panel type and is used to
        identify the plugin in the registry.
        """
        ...

    @abstractmethod
    def get_panel_class(self) -> type[BasePanel]:
        """Return the BasePanel subclass this plugin provides.

        The returned class will be registered with PanelRegistry
        when the plugin is loaded.

        Returns:
            A BasePanel subclass.
        """
        ...

    @property
    def panel_id(self) -> str:
        """Get the panel ID from the panel class metadata.

        Returns:
            The panel's unique ID (e.g., "lucid.panels.bluesky").
        """
        return self.get_panel_class().panel_metadata.id

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with panel plugin information.
        """
        panel_class = self.get_panel_class()
        metadata = panel_class.panel_metadata

        return {
            "type": self.type_name,
            "name": self.name,
            "panel_id": metadata.id,
            "panel_name": metadata.name,
            "panel_description": metadata.description,
            "panel_category": metadata.category,
            "panel_singleton": metadata.singleton,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
            "panel_class": panel_class.__name__,
            "panel_module": panel_class.__module__,
        }

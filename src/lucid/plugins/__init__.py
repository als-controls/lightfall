"""NCS Plugin System.

A manifest-based plugin system supporting multiple plugin types with
background loading and graceful error handling.

Plugin Manifests
----------------
Plugins are discovered via manifests pointed to by entry points.
This allows modifying plugins without reinstalling packages.

Example manifest in a plugin package::

    # my_beamline/manifest.py
    from lucid.plugins import PluginManifest, PluginEntry

    manifest = PluginManifest(
        name="beamline-7.0.1.1",
        version="1.0.0",
        plugins=[
            PluginEntry("plan", "my_scan", "my_beamline.plans:MyScanPlan"),
        ]
    )

Entry point in pyproject.toml::

    [project.entry-points."lucid.plugins"]
    my_beamline = "my_beamline.manifest:manifest"

Plugin Types
------------
The system supports multiple plugin types. Each type defines an interface
that plugins must implement:

- **PlanPlugin**: Bluesky plan plugins for data acquisition

Example plan plugin::

    from lucid.plugins import PlanPlugin

    class MyScanPlan(PlanPlugin):
        @property
        def name(self) -> str:
            return "my_scan"

        def get_plan_function(self):
            return self._scan

        def _scan(self, detectors, motor, start, stop, num):
            import bluesky.plans as bp
            yield from bp.scan(detectors, motor, start, stop, num)

Usage
-----
The plugin system is typically used via the ServiceRegistry::

    from lucid.core.services import ServiceRegistry
    from lucid.plugins import PluginRegistry, PluginLoader, PlanPlugin

    services = ServiceRegistry.get_instance()

    # Register PluginRegistry as a service
    services.register(PluginRegistry, PluginRegistry)

    # Create and configure loader
    def create_loader():
        registry = services.get(PluginRegistry)
        loader = PluginLoader(registry)
        loader.register_plugin_type("plan", PlanPlugin)
        return loader

    services.register(PluginLoader, create_loader)

    # Discover and load plugins
    loader = services.get(PluginLoader)
    loader.discover_manifests()
    loader.start_loading()  # Background loading
"""

from lucid.plugins.controller_plugin import ControllerPlugin
from lucid.plugins.errors import (
    PluginError,
    PluginInitError,
    PluginLoadError,
    PluginNotFoundError,
    PluginStatus,
    PluginTypeNotFoundError,
)
from lucid.plugins.info import PluginInfo
from lucid.plugins.loader import PluginLoader
from lucid.plugins.manifest import PluginEntry, PluginManifest
from lucid.plugins.mcp_tool import MCPToolPlugin
from lucid.plugins.panel_plugin import PanelPlugin
from lucid.plugins.plan_plugin import PlanPlugin
from lucid.plugins.registry import PluginRegistry
from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.plugins.skill_plugin import SkillPlugin
from lucid.plugins.types import PluginType

__all__ = [
    # Core classes
    "PluginType",
    "PluginManifest",
    "PluginEntry",
    "PluginInfo",
    "PluginRegistry",
    "PluginLoader",
    # Plugin types
    "ControllerPlugin",
    "PanelPlugin",
    "PlanPlugin",
    "SettingsPlugin",
    "MCPToolPlugin",
    "SkillPlugin",
    # Status and errors
    "PluginStatus",
    "PluginError",
    "PluginLoadError",
    "PluginInitError",
    "PluginNotFoundError",
    "PluginTypeNotFoundError",
]

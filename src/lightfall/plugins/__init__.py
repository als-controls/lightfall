"""NCS Plugin System.

A manifest-based plugin system supporting multiple plugin types with
background loading and graceful error handling.

Plugin Manifests
----------------
Plugins are discovered via manifests pointed to by entry points.
This allows modifying plugins without reinstalling packages.

Example manifest in a plugin package::

    # my_beamline/manifest.py
    from lightfall.plugins import PluginManifest, PluginEntry

    manifest = PluginManifest(
        name="beamline-7.0.1.1",
        version="1.0.0",
        plugins=[
            PluginEntry("plan", "my_scan", "my_beamline.plans:MyScanPlan"),
        ]
    )

Entry point in pyproject.toml::

    [project.entry-points."lightfall.plugins"]
    my_beamline = "my_beamline.manifest:manifest"

Plugin Types
------------
The system supports multiple plugin types. Each type defines an interface
that plugins must implement:

- **PlanPlugin**: Bluesky plan plugins for data acquisition

Example plan plugin::

    from lightfall.plugins import PlanPlugin

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

    from lightfall.core.services import ServiceRegistry
    from lightfall.plugins import PluginRegistry, PluginLoader, PlanPlugin

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

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.plugins.controller_plugin import ControllerPlugin
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin
from lightfall.plugins.errors import (
    PluginError,
    PluginInitError,
    PluginLoadError,
    PluginNotFoundError,
    PluginStatus,
    PluginTypeNotFoundError,
)
from lightfall.plugins.happi_database_plugin import HappiDatabasePlugin
from lightfall.plugins.info import PluginInfo
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.manifest import PluginEntry, PluginManifest
from lightfall.plugins.panel_plugin import PanelPlugin
from lightfall.plugins.plan_plugin import PlanPlugin
from lightfall.plugins.registry import PluginRegistry
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.plugins.types import PluginType
from lightfall.plugins.visualization_plugin import VisualizationPlugin

__all__ = [
    # Core classes
    "PluginType",
    "PluginManifest",
    "PluginEntry",
    "PluginInfo",
    "PluginRegistry",
    "PluginLoader",
    # Plugin types
    "AgentPlugin",
    "ControllerPlugin",
    "DeviceBackendPlugin",
    "HappiDatabasePlugin",
    "PanelPlugin",
    "PlanPlugin",
    "SettingsPlugin",
    "VisualizationPlugin",
    # Status and errors
    "PluginStatus",
    "PluginError",
    "PluginLoadError",
    "PluginInitError",
    "PluginNotFoundError",
    "PluginTypeNotFoundError",
]

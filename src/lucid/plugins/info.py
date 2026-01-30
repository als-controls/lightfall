"""Runtime plugin information.

PluginInfo tracks the state of a plugin through the loading pipeline,
including the loaded class, instance, status, and any errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lucid.plugins.errors import PluginStatus


@dataclass
class PluginInfo:
    """Runtime information about a plugin.

    PluginInfo tracks a plugin through its lifecycle from discovery
    to ready (or failed) state. It holds references to the loaded
    class and instance.

    Attributes:
        type_name: Plugin type identifier (e.g., "plan").
        name: Plugin name.
        import_path: Original import path from manifest.
        plugin_class: The loaded plugin class (after LOADING phase).
        instance: Plugin instance (after INITIALIZING phase).
        status: Current plugin status.
        manifest_name: Name of manifest that provided this plugin.
        load_time: When the plugin was loaded.
        error: Error message if loading failed.
        preload: If True, load synchronously before main window.

    Example::

        info = PluginInfo(
            type_name="plan",
            name="my_scan",
            import_path="my_beamline.plans:MyScanPlan",
            manifest_name="my-beamline-plans",
        )
    """

    type_name: str
    name: str
    import_path: str
    plugin_class: type | None = None
    instance: Any = None
    status: PluginStatus = field(default=PluginStatus.DISCOVERED)
    manifest_name: str = ""
    load_time: datetime | None = None
    error: str | None = None
    preload: bool = False

    @property
    def unique_id(self) -> str:
        """Unique identifier combining type and name."""
        return f"{self.type_name}:{self.name}"

    @property
    def is_ready(self) -> bool:
        """Check if plugin is ready for use."""
        return self.status == PluginStatus.READY

    @property
    def is_failed(self) -> bool:
        """Check if plugin failed to load."""
        return self.status in (PluginStatus.FAILED_LOAD, PluginStatus.FAILED_INIT)

    @property
    def is_loading(self) -> bool:
        """Check if plugin is currently being loaded."""
        return self.status in (
            PluginStatus.QUEUED_LOAD,
            PluginStatus.LOADING,
            PluginStatus.QUEUED_INIT,
            PluginStatus.INITIALIZING,
        )

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with plugin information.
        """
        data: dict[str, Any] = {
            "type": self.type_name,
            "name": self.name,
            "unique_id": self.unique_id,
            "status": self.status.name,
            "manifest": self.manifest_name,
            "is_ready": self.is_ready,
            "is_failed": self.is_failed,
        }

        if self.error:
            data["error"] = self.error

        if self.load_time:
            data["load_time"] = self.load_time.isoformat()

        if self.plugin_class:
            data["class_name"] = self.plugin_class.__name__
            data["module"] = self.plugin_class.__module__

        # Include instance introspection if available
        if self.instance and hasattr(self.instance, "get_introspection_data"):
            data["instance_data"] = self.instance.get_introspection_data()

        return data

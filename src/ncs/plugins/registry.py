"""Plugin registry for managing loaded plugins.

The PluginRegistry provides thread-safe storage and retrieval of plugins
by type and name. It is registered with ServiceRegistry for dependency
injection throughout the application.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from loguru import logger

from ncs.plugins.errors import PluginStatus
from ncs.plugins.info import PluginInfo

if TYPE_CHECKING:
    pass


class PluginRegistry:
    """Central registry for all NCS plugins.

    Thread-safe registry providing:
    - Registration and storage of plugin info
    - Lookup by name, type, or both
    - Duplicate detection across manifests
    - Introspection API for MCP tools

    This registry is designed to be registered with ServiceRegistry::

        services.register(PluginRegistry, PluginRegistry)

    Example::

        registry = services.get(PluginRegistry)
        registry.register(plugin_info)
        plan = registry.get("plan", "my_scan")
        all_plans = registry.get_by_type("plan")
    """

    _lock = threading.RLock()

    def __init__(self) -> None:
        """Initialize the plugin registry."""
        # Map: type_name -> {plugin_name -> PluginInfo}
        self._plugins: dict[str, dict[str, PluginInfo]] = {}
        # Track unique IDs to prevent duplicates
        self._seen_ids: set[str] = set()
        # Track which manifest provided each plugin
        self._manifest_plugins: dict[str, list[str]] = {}

    def register(
        self,
        plugin_info: PluginInfo,
        *,
        replace: bool = False,
    ) -> bool:
        """Register a plugin.

        Args:
            plugin_info: Plugin information to register.
            replace: If True, replace existing plugin with same ID.

        Returns:
            True if registered, False if duplicate (and not replacing).
        """
        with self._lock:
            unique_id = plugin_info.unique_id

            # Check for duplicate
            if unique_id in self._seen_ids and not replace:
                existing = self._get_by_unique_id(unique_id)
                logger.warning(
                    "Plugin {} already registered from manifest '{}', "
                    "ignoring duplicate from '{}'",
                    unique_id,
                    existing.manifest_name if existing else "unknown",
                    plugin_info.manifest_name,
                )
                return False

            # Initialize type dict if needed
            if plugin_info.type_name not in self._plugins:
                self._plugins[plugin_info.type_name] = {}

            # Register
            self._plugins[plugin_info.type_name][plugin_info.name] = plugin_info
            self._seen_ids.add(unique_id)

            # Track manifest association
            manifest_name = plugin_info.manifest_name
            if manifest_name:
                if manifest_name not in self._manifest_plugins:
                    self._manifest_plugins[manifest_name] = []
                self._manifest_plugins[manifest_name].append(unique_id)

            logger.debug("Registered plugin: {}", unique_id)
            return True

    def unregister(self, type_name: str, name: str) -> bool:
        """Unregister a plugin.

        Args:
            type_name: Plugin type.
            name: Plugin name.

        Returns:
            True if plugin was registered.
        """
        with self._lock:
            unique_id = f"{type_name}:{name}"

            if type_name in self._plugins and name in self._plugins[type_name]:
                del self._plugins[type_name][name]
                self._seen_ids.discard(unique_id)
                logger.debug("Unregistered plugin: {}", unique_id)
                return True
            return False

    def get(self, type_name: str, name: str) -> PluginInfo | None:
        """Get a plugin by type and name.

        Args:
            type_name: Plugin type.
            name: Plugin name.

        Returns:
            PluginInfo or None if not found.
        """
        with self._lock:
            type_plugins = self._plugins.get(type_name, {})
            return type_plugins.get(name)

    def get_plugin_instance(self, type_name: str, name: str) -> Any | None:
        """Get a plugin instance by type and name.

        Args:
            type_name: Plugin type.
            name: Plugin name.

        Returns:
            Plugin instance or None if not found/not ready.
        """
        info = self.get(type_name, name)
        if info and info.is_ready:
            return info.instance
        return None

    def get_by_type(self, type_name: str) -> list[PluginInfo]:
        """Get all plugins of a specific type.

        Args:
            type_name: Plugin type.

        Returns:
            List of PluginInfo for that type.
        """
        with self._lock:
            return list(self._plugins.get(type_name, {}).values())

    def get_ready_by_type(self, type_name: str) -> list[PluginInfo]:
        """Get all ready plugins of a specific type.

        Args:
            type_name: Plugin type.

        Returns:
            List of ready PluginInfo for that type.
        """
        return [p for p in self.get_by_type(type_name) if p.is_ready]

    def get_all(self) -> list[PluginInfo]:
        """Get all registered plugins.

        Returns:
            List of all PluginInfo.
        """
        with self._lock:
            all_plugins = []
            for type_plugins in self._plugins.values():
                all_plugins.extend(type_plugins.values())
            return all_plugins

    def has(self, type_name: str, name: str) -> bool:
        """Check if a plugin is registered.

        Args:
            type_name: Plugin type.
            name: Plugin name.

        Returns:
            True if registered.
        """
        with self._lock:
            return type_name in self._plugins and name in self._plugins[type_name]

    def is_ready(self, type_name: str, name: str) -> bool:
        """Check if a plugin is ready for use.

        Args:
            type_name: Plugin type.
            name: Plugin name.

        Returns:
            True if ready.
        """
        info = self.get(type_name, name)
        return info is not None and info.is_ready

    def _get_by_unique_id(self, unique_id: str) -> PluginInfo | None:
        """Get plugin by unique ID (internal helper).

        Args:
            unique_id: ID in format "type:name".

        Returns:
            PluginInfo or None.
        """
        if ":" in unique_id:
            type_name, name = unique_id.split(":", 1)
            return self.get(type_name, name)
        return None

    def get_types(self) -> list[str]:
        """Get all registered plugin types.

        Returns:
            List of type names.
        """
        with self._lock:
            return list(self._plugins.keys())

    def get_manifests(self) -> list[str]:
        """Get all manifests that provided plugins.

        Returns:
            List of manifest names.
        """
        with self._lock:
            return list(self._manifest_plugins.keys())

    def get_plugins_from_manifest(self, manifest_name: str) -> list[PluginInfo]:
        """Get all plugins from a specific manifest.

        Args:
            manifest_name: The manifest name.

        Returns:
            List of PluginInfo from that manifest.
        """
        with self._lock:
            unique_ids = self._manifest_plugins.get(manifest_name, [])
            return [
                info
                for uid in unique_ids
                if (info := self._get_by_unique_id(uid)) is not None
            ]

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with registry information.
        """
        with self._lock:
            by_type: dict[str, dict[str, Any]] = {}
            for type_name, plugins in self._plugins.items():
                ready = sum(1 for p in plugins.values() if p.is_ready)
                failed = sum(1 for p in plugins.values() if p.is_failed)
                by_type[type_name] = {
                    "total": len(plugins),
                    "ready": ready,
                    "failed": failed,
                    "plugins": [
                        {"name": p.name, "status": p.status.name}
                        for p in plugins.values()
                    ],
                }

            return {
                "total_plugins": len(self._seen_ids),
                "plugin_types": list(self._plugins.keys()),
                "manifests": list(self._manifest_plugins.keys()),
                "by_type": by_type,
            }

    def update_status(
        self,
        type_name: str,
        name: str,
        status: PluginStatus,
        error: str | None = None,
    ) -> None:
        """Update the status of a plugin.

        Args:
            type_name: Plugin type.
            name: Plugin name.
            status: New status.
            error: Error message (for failed states).
        """
        with self._lock:
            info = self.get(type_name, name)
            if info:
                info.status = status
                if error:
                    info.error = error

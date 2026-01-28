"""Plugin system errors and status enum.

This module provides the status tracking enum and custom exceptions
for the NCS plugin system.
"""

from __future__ import annotations

from enum import Enum, auto


class PluginStatus(Enum):
    """Status of a plugin in the loading pipeline.

    Plugins progress through these states during discovery and loading:

    DISCOVERED -> QUEUED_LOAD -> LOADING -> QUEUED_INIT -> INITIALIZING -> READY

    Or they may end up in a failed state:
    - FAILED_LOAD: Error during class import
    - FAILED_INIT: Error during instantiation
    - DISABLED: Explicitly disabled by user/config
    """

    DISCOVERED = auto()
    """Found in manifest, not yet queued for loading."""

    QUEUED_LOAD = auto()
    """In load queue waiting to be loaded."""

    LOADING = auto()
    """Currently being loaded (class import)."""

    QUEUED_INIT = auto()
    """Loaded class, waiting for instantiation."""

    INITIALIZING = auto()
    """Currently being instantiated."""

    READY = auto()
    """Successfully loaded and ready for use."""

    FAILED_LOAD = auto()
    """Failed during class loading/import."""

    FAILED_INIT = auto()
    """Failed during instantiation."""

    DISABLED = auto()
    """Explicitly disabled."""


class PluginError(Exception):
    """Base exception for plugin system errors."""


class PluginLoadError(PluginError):
    """Error during plugin class loading/import.

    Attributes:
        plugin_id: The unique plugin identifier (type:name).
    """

    def __init__(self, plugin_id: str, message: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(f"Failed to load plugin '{plugin_id}': {message}")


class PluginInitError(PluginError):
    """Error during plugin instantiation.

    Attributes:
        plugin_id: The unique plugin identifier (type:name).
    """

    def __init__(self, plugin_id: str, message: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(f"Failed to initialize plugin '{plugin_id}': {message}")


class PluginNotFoundError(PluginError):
    """Plugin not found in registry.

    Attributes:
        type_name: The plugin type.
        name: The plugin name.
    """

    def __init__(self, type_name: str, name: str) -> None:
        self.type_name = type_name
        self.name = name
        super().__init__(f"Plugin '{type_name}:{name}' not found")


class PluginTypeNotFoundError(PluginError):
    """Plugin type not registered.

    Attributes:
        type_name: The plugin type that wasn't found.
    """

    def __init__(self, type_name: str) -> None:
        self.type_name = type_name
        super().__init__(f"Plugin type '{type_name}' is not registered")

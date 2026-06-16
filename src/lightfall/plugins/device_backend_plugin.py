"""DeviceBackendPlugin: contributes a DeviceBackend to the DeviceCatalog."""
from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from lightfall.devices.base import DeviceBackend
from lightfall.plugins.types import PluginType


class DeviceBackendPlugin(PluginType):
    """Plugin type that provides a device backend.

    The loader instantiates the plugin (singleton) and, if enabled by the
    ``device_plugin_<name>_enabled`` preference, calls ``create_backend()`` and
    adds the result to the unified DeviceCatalog.
    """

    type_name: ClassVar[str] = "device_backend"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        return "Device backend plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique backend-plugin name (used for the enable preference key)."""
        ...

    @abstractmethod
    def create_backend(self) -> DeviceBackend:
        """Return a configured, not-yet-connected DeviceBackend instance."""
        ...

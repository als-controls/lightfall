"""HappiDatabasePlugin: ship devices with a plugin as a packaged happi JSON.

Subclass and declare WHERE the database lives; create_backend() is implemented
here to vend a HappiBackend over it. The JSON ships inside the plugin's wheel
and is resolved via importlib.resources, so it works from an installed package
(not just a source checkout).

Example::

    class MyDevicesPlugin(HappiDatabasePlugin):
        database_resource = ("my_plugin", "devices.json")
        beamline = "7.0.1"

        @property
        def name(self) -> str:
            return "my_devices"
"""
from __future__ import annotations

from abc import abstractmethod
from importlib.resources import files
from pathlib import Path
from typing import ClassVar

from lightfall.devices.backends.happi import HappiBackend
from lightfall.devices.base import DeviceBackend
from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin


class HappiDatabasePlugin(DeviceBackendPlugin):
    """A DeviceBackendPlugin backed by a packaged happi JSON database."""

    #: Either ("package", "resource.json") for a packaged resource, or a
    #: filesystem path string.
    database_resource: ClassVar[tuple[str, str] | str]
    beamline: ClassVar[str | None] = None
    instantiate: ClassVar[str] = "background"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique backend-plugin name (used for the enable preference key)."""
        ...

    def database_path(self) -> Path:
        """Resolve ``database_resource`` to a concrete filesystem path.

        - ``("package", "resource.json")`` -> resolved via importlib.resources
          (works from an installed wheel that unpacks to the filesystem).
        - ``str`` -> treated as a filesystem path.

        Raises:
            FileNotFoundError: if the resolved path does not exist.
        """
        res = self.database_resource
        if isinstance(res, tuple):
            package, resource = res
            path = Path(str(files(package).joinpath(resource)))
        else:
            path = Path(res)
        if not path.exists():
            raise FileNotFoundError(
                f"{type(self).__name__}: happi database not found at {path} "
                f"(database_resource={self.database_resource!r})"
            )
        return path

    def create_backend(self) -> DeviceBackend:
        """Vend a HappiBackend over the resolved packaged database.

        The backend is named after the plugin so it is keyed uniquely in the
        DeviceCatalog and never collides with the base "happi" backend.
        """
        return HappiBackend(
            path=str(self.database_path()),
            beamline=self.beamline,
            instantiate=self.instantiate,
            name=self.name,
        )

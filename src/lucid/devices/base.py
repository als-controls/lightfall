"""Abstract base class for device backends.

Device backends provide storage and retrieval of device information.
Different backends can be used depending on deployment needs:
- SQLite for local/embedded storage
- Happi for existing happi databases
- Mock for testing with simulated devices
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from lucid.devices.model import (
        DeviceCategory,
        DeviceConfiguration,
        DeviceInfo,
        MaintenanceRecord,
    )


class DeviceBackend(ABC):
    """Abstract base class for device storage backends.

    DeviceBackend defines the interface that all device storage
    implementations must provide. This allows the DeviceCatalog
    to work with different storage systems transparently.

    Implementations:
    - SQLiteBackend: Local SQLite database storage
    - HappiBackend: Integration with happi device database
    - MockBackend: In-memory backend with simulated devices

    Example:
        >>> class MyBackend(DeviceBackend):
        ...     def get_device(self, device_id: UUID) -> DeviceInfo | None:
        ...         # Implementation
        ...         pass
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Get the backend name.

        Returns:
            Backend identifier string.
        """
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the backend is connected and ready.

        Returns:
            True if backend is operational.
        """
        ...

    @property
    def is_editable(self) -> bool:
        """Whether this backend supports editing (add/update/remove).

        Returns False by default. Backends that persist changes
        (e.g., HappiBackend with JSON) should override to return True.
        """
        return False

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the backend storage.

        Returns:
            True if connection successful.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the backend storage."""
        ...

    # === Device CRUD Operations ===

    @abstractmethod
    def get_device(self, device_id: UUID) -> DeviceInfo | None:
        """Get a device by ID.

        Args:
            device_id: The device unique identifier.

        Returns:
            DeviceInfo or None if not found.
        """
        ...

    @abstractmethod
    def get_device_by_name(self, name: str) -> DeviceInfo | None:
        """Get a device by name.

        Args:
            name: The device name.

        Returns:
            DeviceInfo or None if not found.
        """
        ...

    @abstractmethod
    def get_device_by_prefix(self, prefix: str) -> DeviceInfo | None:
        """Get a device by connection prefix.

        Args:
            prefix: The connection prefix (e.g., EPICS PV prefix).

        Returns:
            DeviceInfo or None if not found.
        """
        ...

    @abstractmethod
    def list_devices(
        self,
        category: DeviceCategory | None = None,
        beamline: str | None = None,
        active_only: bool = True,
    ) -> list[DeviceInfo]:
        """List devices with optional filtering.

        Args:
            category: Filter by device category.
            beamline: Filter by beamline.
            active_only: Only return active devices.

        Returns:
            List of matching DeviceInfo objects.
        """
        ...

    @abstractmethod
    def search_devices(self, query: str) -> list[DeviceInfo]:
        """Search devices by query string.

        Args:
            query: Search string to match against device fields.

        Returns:
            List of matching DeviceInfo objects.
        """
        ...

    @abstractmethod
    def add_device(self, device: DeviceInfo) -> bool:
        """Add a new device to the backend.

        Args:
            device: The device to add.

        Returns:
            True if successfully added.
        """
        ...

    @abstractmethod
    def update_device(self, device: DeviceInfo) -> bool:
        """Update an existing device.

        Args:
            device: The device with updated information.

        Returns:
            True if successfully updated.
        """
        ...

    @abstractmethod
    def remove_device(self, device_id: UUID) -> bool:
        """Remove a device from the backend.

        Args:
            device_id: The device ID to remove.

        Returns:
            True if successfully removed.
        """
        ...

    # === Configuration Operations ===

    @abstractmethod
    def get_device_configurations(
        self, device_id: UUID
    ) -> list[DeviceConfiguration]:
        """Get all configurations for a device.

        Args:
            device_id: The device ID.

        Returns:
            List of configurations.
        """
        ...

    @abstractmethod
    def get_configuration(
        self, device_id: UUID, config_name: str
    ) -> DeviceConfiguration | None:
        """Get a specific configuration by name.

        Args:
            device_id: The device ID.
            config_name: The configuration name.

        Returns:
            Configuration or None if not found.
        """
        ...

    @abstractmethod
    def save_configuration(self, config: DeviceConfiguration) -> bool:
        """Save a device configuration.

        Args:
            config: The configuration to save.

        Returns:
            True if successfully saved.
        """
        ...

    @abstractmethod
    def delete_configuration(self, config_id: UUID) -> bool:
        """Delete a configuration.

        Args:
            config_id: The configuration ID.

        Returns:
            True if successfully deleted.
        """
        ...

    # === Maintenance Records ===

    @abstractmethod
    def get_maintenance_history(
        self, device_id: UUID, limit: int = 100
    ) -> list[MaintenanceRecord]:
        """Get maintenance history for a device.

        Args:
            device_id: The device ID.
            limit: Maximum number of records to return.

        Returns:
            List of maintenance records, newest first.
        """
        ...

    @abstractmethod
    def add_maintenance_record(self, record: MaintenanceRecord) -> bool:
        """Add a maintenance record.

        Args:
            record: The maintenance record to add.

        Returns:
            True if successfully added.
        """
        ...

    # === Bulk Operations ===

    def get_all_devices(self) -> list[DeviceInfo]:
        """Get all devices from the backend.

        Default implementation calls list_devices with no filters.

        Returns:
            List of all devices.
        """
        return self.list_devices(active_only=False)

    def get_devices_by_category(
        self, category: DeviceCategory
    ) -> list[DeviceInfo]:
        """Get all devices of a specific category.

        Args:
            category: The device category.

        Returns:
            List of devices in the category.
        """
        return self.list_devices(category=category)

    def get_devices_by_beamline(self, beamline: str) -> list[DeviceInfo]:
        """Get all devices for a beamline.

        Args:
            beamline: The beamline identifier.

        Returns:
            List of devices for the beamline.
        """
        return self.list_devices(beamline=beamline)

    # === Introspection ===

    def get_backend_info(self) -> dict[str, Any]:
        """Get information about the backend.

        Returns:
            Dictionary with backend information.
        """
        return {
            "name": self.name,
            "connected": self.is_connected,
            "device_count": len(self.get_all_devices()),
        }

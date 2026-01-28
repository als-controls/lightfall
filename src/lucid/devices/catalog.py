"""Device catalog providing unified access to device backends.

The DeviceCatalog is the main interface for device management in NCS.
It provides a facade over one or more device backends, offering a
consistent API regardless of the underlying storage mechanism.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lucid.devices.base import DeviceBackend
from lucid.devices.model import (
    DeviceCategory,
    DeviceConfiguration,
    DeviceInfo,
    DeviceSnapshot,
    DeviceState,
    DeviceStatus,
    MaintenanceRecord,
)

if TYPE_CHECKING:
    pass


class DeviceCatalog(QObject):
    """Singleton catalog providing unified device access.

    DeviceCatalog is the primary interface for all device operations
    in NCS. It manages one or more device backends and provides:

    - Unified API for device CRUD operations
    - Device state monitoring and caching
    - Configuration management
    - Maintenance history tracking
    - Device snapshots for state capture/restore
    - Qt signals for state changes

    Signals:
        device_added: Emitted when a device is added.
        device_removed: Emitted when a device is removed.
        device_updated: Emitted when device info changes.
        device_state_changed: Emitted when device state changes.
        backend_connected: Emitted when a backend connects.
        backend_disconnected: Emitted when a backend disconnects.

    Example:
        >>> catalog = DeviceCatalog.get_instance()
        >>> catalog.set_backend(MockBackend())
        >>> catalog.connect()
        >>> motors = catalog.list_devices(category=DeviceCategory.MOTOR)
        >>> motor = catalog.get_device_by_name("motor")
    """

    _instance: ClassVar[DeviceCatalog | None] = None
    _lock = threading.RLock()

    # Signals
    device_added = Signal(object)  # DeviceInfo
    device_removed = Signal(str)  # device_id as string
    device_updated = Signal(object)  # DeviceInfo
    device_state_changed = Signal(str, object)  # device_id, DeviceState
    backend_connected = Signal(str)  # backend name
    backend_disconnected = Signal(str)  # backend name

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the device catalog.

        Args:
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._backend: DeviceBackend | None = None
        self._device_cache: dict[UUID, DeviceInfo] = {}
        self._name_index: dict[str, UUID] = {}

    @classmethod
    def get_instance(cls) -> DeviceCatalog:
        """Get the singleton instance.

        Returns:
            The DeviceCatalog singleton.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.disconnect()
            cls._instance = None

    # === Backend Management ===

    @property
    def backend(self) -> DeviceBackend | None:
        """Get the current backend."""
        return self._backend

    @property
    def is_connected(self) -> bool:
        """Check if the catalog is connected to a backend."""
        return self._backend is not None and self._backend.is_connected

    def set_backend(self, backend: DeviceBackend) -> None:
        """Set the device backend.

        If already connected to a backend, disconnects first.

        Args:
            backend: The backend to use.
        """
        if self._backend is not None:
            self.disconnect()

        self._backend = backend
        logger.info("Device catalog backend set to: {}", backend.name)

    def connect(self) -> bool:
        """Connect to the backend.

        Returns:
            True if connection successful.
        """
        if self._backend is None:
            logger.error("No backend configured")
            return False

        if self._backend.connect():
            self._rebuild_cache()
            self.backend_connected.emit(self._backend.name)
            logger.info("Device catalog connected")
            return True

        return False

    def disconnect(self) -> None:
        """Disconnect from the backend."""
        if self._backend is not None:
            name = self._backend.name
            self._backend.disconnect()
            self._device_cache.clear()
            self._name_index.clear()
            self.backend_disconnected.emit(name)
            logger.info("Device catalog disconnected")

    def _rebuild_cache(self) -> None:
        """Rebuild the device cache from the backend."""
        if self._backend is None:
            return

        self._device_cache.clear()
        self._name_index.clear()

        for device in self._backend.get_all_devices():
            self._device_cache[device.id] = device
            self._name_index[device.name] = device.id

        logger.debug("Rebuilt device cache with {} devices", len(self._device_cache))

    # === Device Access ===

    def get_device(self, device_id: UUID | str) -> DeviceInfo | None:
        """Get a device by ID.

        Args:
            device_id: Device UUID or string representation.

        Returns:
            DeviceInfo or None if not found.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        # Check cache first
        if device_id in self._device_cache:
            return self._device_cache[device_id]

        # Fall back to backend
        if self._backend:
            device = self._backend.get_device(device_id)
            if device:
                self._device_cache[device_id] = device
                self._name_index[device.name] = device_id
            return device

        return None

    def get_device_by_name(self, name: str) -> DeviceInfo | None:
        """Get a device by name.

        Args:
            name: Device name.

        Returns:
            DeviceInfo or None if not found.
        """
        # Check index first
        if name in self._name_index:
            return self._device_cache.get(self._name_index[name])

        # Fall back to backend
        if self._backend:
            device = self._backend.get_device_by_name(name)
            if device:
                self._device_cache[device.id] = device
                self._name_index[device.name] = device.id
            return device

        return None

    def get_device_by_prefix(self, prefix: str) -> DeviceInfo | None:
        """Get a device by connection prefix.

        Args:
            prefix: Connection prefix (e.g., EPICS PV prefix).

        Returns:
            DeviceInfo or None if not found.
        """
        if self._backend:
            return self._backend.get_device_by_prefix(prefix)
        return None

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
            List of matching devices.
        """
        if self._backend:
            return self._backend.list_devices(
                category=category,
                beamline=beamline,
                active_only=active_only,
            )
        return []

    def search_devices(self, query: str) -> list[DeviceInfo]:
        """Search devices by query string.

        Args:
            query: Search string.

        Returns:
            List of matching devices.
        """
        if self._backend:
            return self._backend.search_devices(query)
        return []

    def get_all_devices(self) -> list[DeviceInfo]:
        """Get all devices.

        Returns:
            List of all devices.
        """
        if self._backend:
            return self._backend.get_all_devices()
        return []

    # === Device Management ===

    def add_device(self, device: DeviceInfo) -> bool:
        """Add a new device.

        Args:
            device: Device to add.

        Returns:
            True if successfully added.
        """
        if self._backend is None:
            return False

        if self._backend.add_device(device):
            self._device_cache[device.id] = device
            self._name_index[device.name] = device.id
            self.device_added.emit(device)
            logger.info("Added device: {}", device.name)
            return True

        return False

    def update_device(self, device: DeviceInfo) -> bool:
        """Update an existing device.

        Args:
            device: Device with updated information.

        Returns:
            True if successfully updated.
        """
        if self._backend is None:
            return False

        if self._backend.update_device(device):
            # Update cache
            old_name = None
            if device.id in self._device_cache:
                old_name = self._device_cache[device.id].name
                if old_name != device.name:
                    del self._name_index[old_name]

            self._device_cache[device.id] = device
            self._name_index[device.name] = device.id
            self.device_updated.emit(device)
            logger.info("Updated device: {}", device.name)
            return True

        return False

    def remove_device(self, device_id: UUID | str) -> bool:
        """Remove a device.

        Args:
            device_id: Device ID to remove.

        Returns:
            True if successfully removed.
        """
        if self._backend is None:
            return False

        if isinstance(device_id, str):
            device_id = UUID(device_id)

        # Get device name for index cleanup
        device = self._device_cache.get(device_id)
        device_name = device.name if device else None

        if self._backend.remove_device(device_id):
            # Clean up cache
            if device_id in self._device_cache:
                del self._device_cache[device_id]
            if device_name and device_name in self._name_index:
                del self._name_index[device_name]

            self.device_removed.emit(str(device_id))
            logger.info("Removed device: {}", device_id)
            return True

        return False

    # === Device State ===

    def get_device_state(self, device_id: UUID | str) -> DeviceState | None:
        """Get the current state of a device.

        Args:
            device_id: Device ID.

        Returns:
            Current device state or None.
        """
        device = self.get_device(device_id)
        if device:
            return device.state
        return None

    def update_device_state(self, device_id: UUID | str, state: DeviceState) -> None:
        """Update the state of a device.

        Args:
            device_id: Device ID.
            state: New device state.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        device = self._device_cache.get(device_id)
        if device:
            device._state = state
            self.device_state_changed.emit(str(device_id), state)

    def refresh_device_state(self, device_id: UUID | str) -> DeviceState | None:
        """Refresh device state from the actual device.

        Args:
            device_id: Device ID.

        Returns:
            Updated device state.
        """
        device = self.get_device(device_id)
        if device is None or device.ophyd_device is None:
            return None

        try:
            ophyd_dev = device.ophyd_device

            # Build state from ophyd device
            state = DeviceState(
                device_id=device.id,
                status=DeviceStatus.ONLINE,
                connected=True,
            )

            # Get position for positioners
            if hasattr(ophyd_dev, "position"):
                state.position = ophyd_dev.position

            # Get value for signals
            if hasattr(ophyd_dev, "get"):
                try:
                    state.value = ophyd_dev.get()
                except Exception:
                    pass

            device._state = state
            self.device_state_changed.emit(str(device.id), state)
            return state

        except Exception as e:
            logger.error("Error refreshing device state for {}: {}", device.name, e)
            return None

    # === Ophyd Device Access ===

    def get_ophyd_device(self, name: str) -> Any:
        """Get the ophyd device instance by name.

        Args:
            name: Device name.

        Returns:
            Ophyd device instance or None.
        """
        device = self.get_device_by_name(name)
        if device:
            return device.ophyd_device
        return None

    def get_all_ophyd_devices(self) -> dict[str, Any]:
        """Get all ophyd device instances.

        Returns:
            Dictionary mapping name to ophyd device.
        """
        result = {}
        for device in self._device_cache.values():
            if device.ophyd_device is not None:
                result[device.name] = device.ophyd_device
        return result

    # === Configuration Management ===

    def get_device_configurations(
        self, device_id: UUID | str
    ) -> list[DeviceConfiguration]:
        """Get all configurations for a device.

        Args:
            device_id: Device ID.

        Returns:
            List of configurations.
        """
        if self._backend is None:
            return []

        if isinstance(device_id, str):
            device_id = UUID(device_id)

        return self._backend.get_device_configurations(device_id)

    def get_configuration(
        self, device_id: UUID | str, config_name: str
    ) -> DeviceConfiguration | None:
        """Get a specific configuration.

        Args:
            device_id: Device ID.
            config_name: Configuration name.

        Returns:
            Configuration or None.
        """
        if self._backend is None:
            return None

        if isinstance(device_id, str):
            device_id = UUID(device_id)

        return self._backend.get_configuration(device_id, config_name)

    def save_configuration(self, config: DeviceConfiguration) -> bool:
        """Save a device configuration.

        Args:
            config: Configuration to save.

        Returns:
            True if successful.
        """
        if self._backend is None:
            return False
        return self._backend.save_configuration(config)

    def delete_configuration(self, config_id: UUID | str) -> bool:
        """Delete a configuration.

        Args:
            config_id: Configuration ID.

        Returns:
            True if successful.
        """
        if self._backend is None:
            return False

        if isinstance(config_id, str):
            config_id = UUID(config_id)

        return self._backend.delete_configuration(config_id)

    # === Maintenance History ===

    def get_maintenance_history(
        self, device_id: UUID | str, limit: int = 100
    ) -> list[MaintenanceRecord]:
        """Get maintenance history for a device.

        Args:
            device_id: Device ID.
            limit: Maximum records to return.

        Returns:
            List of maintenance records.
        """
        if self._backend is None:
            return []

        if isinstance(device_id, str):
            device_id = UUID(device_id)

        return self._backend.get_maintenance_history(device_id, limit)

    def add_maintenance_record(self, record: MaintenanceRecord) -> bool:
        """Add a maintenance record.

        Args:
            record: Maintenance record to add.

        Returns:
            True if successful.
        """
        if self._backend is None:
            return False
        return self._backend.add_maintenance_record(record)

    # === Snapshots ===

    def take_snapshot(
        self,
        name: str,
        device_ids: list[UUID | str] | None = None,
        description: str = "",
        taken_by: str = "",
    ) -> DeviceSnapshot:
        """Take a snapshot of device states.

        Args:
            name: Snapshot name.
            device_ids: Specific devices to snapshot (None = all).
            description: Snapshot description.
            taken_by: Who is taking the snapshot.

        Returns:
            The created snapshot.
        """
        snapshot = DeviceSnapshot(
            name=name,
            description=description,
            taken_by=taken_by,
        )

        # Get devices to snapshot
        if device_ids is None:
            devices = self.get_all_devices()
        else:
            devices = []
            for did in device_ids:
                device = self.get_device(did)
                if device:
                    devices.append(device)

        # Capture states
        for device in devices:
            # Refresh state first
            self.refresh_device_state(device.id)

            if device.state:
                snapshot.device_states[str(device.id)] = device.state

            # Get current configuration
            configs = self.get_device_configurations(device.id)
            if configs:
                # Use the first (default) configuration
                snapshot.device_configs[str(device.id)] = configs[0]

        logger.info(
            "Created snapshot '{}' with {} devices",
            name,
            len(snapshot.device_states),
        )
        return snapshot

    # === Introspection ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with catalog information.
        """
        devices = self.get_all_devices()

        # Count by category
        by_category: dict[str, int] = {}
        for device in devices:
            cat = device.category.value
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "connected": self.is_connected,
            "backend": self._backend.name if self._backend else None,
            "device_count": len(devices),
            "cached_devices": len(self._device_cache),
            "devices_by_category": by_category,
            "devices": [device.to_summary() for device in devices],
        }

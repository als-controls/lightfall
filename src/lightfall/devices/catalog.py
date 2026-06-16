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

from lightfall.devices.base import DeviceBackend
from lightfall.devices.model import (
    DeviceCategory,
    DeviceConfiguration,
    DeviceInfo,
    DeviceSnapshot,
    DeviceState,
    DeviceStatus,
    MaintenanceRecord,
)

from lightfall.utils.threads import QThreadFuture

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
    device_connecting = Signal(str)  # device_id — connection started
    device_connected = Signal(str)  # device_id — connection succeeded
    device_connection_failed = Signal(str, str)  # device_id, error message
    backend_connected = Signal(str)  # backend name
    backend_disconnected = Signal(str)  # backend name

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the device catalog.

        Args:
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._backend: DeviceBackend | None = None  # primary/legacy
        self._backends: dict[str, DeviceBackend] = {}  # all active backends
        self._connection_manager_connected = False
        self._device_backend_map: dict[UUID, str] = {}  # device_id -> backend name
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
        """Get the primary backend (legacy compatibility)."""
        return self._backend

    @property
    def backends(self) -> dict[str, DeviceBackend]:
        """Get all registered backends."""
        return dict(self._backends)

    @property
    def is_connected(self) -> bool:
        """Check if any backend is connected."""
        return any(b.is_connected for b in self._backends.values())

    def set_backend(self, backend: DeviceBackend) -> None:
        """Set the device backend (legacy single-backend API).

        Clears all existing backends and adds this one.

        Args:
            backend: The backend to use.
        """
        self.disconnect()
        self._backend = backend
        self._backends[backend.name] = backend
        logger.info("Device catalog backend set to: {}", backend.name)

    def add_backend(self, backend: DeviceBackend) -> None:
        """Add a backend to the catalog.

        Multiple backends can be active simultaneously. Devices from all
        connected backends are merged into a single unified view.

        Args:
            backend: The backend to add.
        """
        if self._backend is None:
            self._backend = backend  # first one becomes primary
        self._backends[backend.name] = backend
        logger.info("Added device backend: {}", backend.name)

    def add_and_connect_backend(self, backend: DeviceBackend) -> None:
        """Add a backend after startup and connect it off the UI thread.

        Unlike connect() (which runs at startup before the window exists),
        this is used when a plugin contributes a backend later. The backend is
        registered immediately; backend.connect() (potentially slow, e.g. a
        profile-collection exec) runs on a worker thread; device merge and the
        backend_connected signal fire back on the main thread.
        """
        self.add_backend(backend)
        future = QThreadFuture(
            backend.connect,
            callback_slot=lambda ok: self._finish_backend_connect(backend, bool(ok)),
            key=f"connect_backend_{backend.name}",
            name=f"connect_{backend.name}",
        )
        future.start()

    def _finish_backend_connect(self, backend: DeviceBackend, connected: bool) -> None:
        """Main-thread completion for add_and_connect_backend."""
        if not connected:
            logger.warning("Backend '{}' failed to connect", backend.name)
            return
        self._load_backend_devices(backend)
        self.backend_connected.emit(backend.name)
        self._connect_to_connection_manager()
        logger.info(
            "Device backend '{}' connected late ({} devices total)",
            backend.name,
            len(self._device_cache),
        )

    def remove_backend(self, name: str) -> None:
        """Remove a backend by name.

        Args:
            name: Backend name to remove.
        """
        backend = self._backends.pop(name, None)
        if backend is None:
            return
        if backend.is_connected:
            backend.disconnect()
            self.backend_disconnected.emit(name)

        # Remove devices owned by this backend
        to_remove = [did for did, bn in self._device_backend_map.items() if bn == name]
        for did in to_remove:
            device = self._device_cache.pop(did, None)
            if device and device.name in self._name_index:
                del self._name_index[device.name]
            del self._device_backend_map[did]

        if self._backend is backend:
            self._backend = next(iter(self._backends.values()), None)

        logger.info("Removed device backend: {}", name)

    def connect(self) -> bool:
        """Connect all registered backends.

        Returns:
            True if at least one backend connected successfully.
        """
        if not self._backends:
            logger.error("No backend configured")
            return False

        any_connected = False
        for name, backend in self._backends.items():
            try:
                if backend.connect():
                    self._load_backend_devices(backend)
                    self.backend_connected.emit(name)
                    any_connected = True
                    logger.info("Backend '{}' connected", name)
                else:
                    logger.warning("Backend '{}' failed to connect", name)
            except Exception as e:
                logger.error("Error connecting backend '{}': {}", name, e)

        if any_connected:
            logger.info("Device catalog connected ({} devices)", len(self._device_cache))
            # Connect to DeviceConnectionManager for background connection updates
            self._connect_to_connection_manager()

        return any_connected

    def disconnect(self) -> None:
        """Disconnect all backends."""
        # Cancel any pending connections
        self._disconnect_from_connection_manager()

        for name, backend in list(self._backends.items()):
            if backend.is_connected:
                backend.disconnect()
                self.backend_disconnected.emit(name)

        self._backends.clear()
        self._backend = None
        self._device_cache.clear()
        self._name_index.clear()
        self._device_backend_map.clear()
        logger.info("Device catalog disconnected")

    def _connect_to_connection_manager(self) -> None:
        """Connect to DeviceConnectionManager signals for background updates."""
        if self._connection_manager_connected:
            return

        try:
            from lightfall.devices.connection_manager import DeviceConnectionManager

            manager = DeviceConnectionManager.get_instance()
            manager.device_connecting.connect(self._on_device_connecting)
            manager.device_connected.connect(self._on_device_connected)
            manager.device_failed.connect(self._on_device_failed)
            self._connection_manager_connected = True
            logger.debug("Connected to DeviceConnectionManager")
        except Exception as e:
            logger.warning("Failed to connect to DeviceConnectionManager: {}", e)

    def _disconnect_from_connection_manager(self) -> None:
        """Disconnect from DeviceConnectionManager and cancel pending connections."""
        if not self._connection_manager_connected:
            return

        try:
            from lightfall.devices.connection_manager import DeviceConnectionManager

            manager = DeviceConnectionManager.get_instance()
            manager.cancel_all()
            manager.device_connecting.disconnect(self._on_device_connecting)
            manager.device_connected.disconnect(self._on_device_connected)
            manager.device_failed.disconnect(self._on_device_failed)
            self._connection_manager_connected = False
        except Exception as e:
            logger.debug("Error disconnecting from ConnectionManager: {}", e)

    def _on_device_connecting(self, device_id_str: str) -> None:
        """Handle device connection started."""
        try:
            device_id = UUID(device_id_str)
            device = self._device_cache.get(device_id)
            if device and device._state:
                device._state.status = DeviceStatus.CONNECTING
                self.device_state_changed.emit(device_id_str, device._state)
            self.device_connecting.emit(device_id_str)
        except Exception as e:
            logger.debug("Error handling device_connecting: {}", e)

    def _on_device_connected(self, result: Any) -> None:
        """Handle successful device connection from ConnectionManager."""
        try:
            from lightfall.devices.connection_manager import ConnectionResult

            if not isinstance(result, ConnectionResult):
                return

            device = self._device_cache.get(result.device_id)
            if device is None:
                return

            # Update device with ophyd instance
            device._ophyd_device = result.ophyd_device
            device._state = DeviceState(
                device_id=device.id,
                status=DeviceStatus.ONLINE,
                connected=True,
            )

            self.device_state_changed.emit(str(result.device_id), device._state)
            self.device_connected.emit(str(result.device_id))
            logger.debug("Device '{}' connected via ConnectionManager", device.name)

        except Exception as e:
            logger.warning("Error handling device_connected: {}", e)

    def _on_device_failed(self, result: Any) -> None:
        """Handle failed device connection from ConnectionManager."""
        try:
            from lightfall.devices.connection_manager import ConnectionResult, ConnectionState

            if not isinstance(result, ConnectionResult):
                return

            device = self._device_cache.get(result.device_id)
            if device is None:
                return

            # If the backend already set a state (e.g. CONNECTING for
            # CA tunnel retry), respect it instead of overwriting.
            if device._state and device._state.status == DeviceStatus.CONNECTING:
                # Backend wants to keep retrying — don't override
                self.device_state_changed.emit(str(result.device_id), device._state)
                return

            # Update state to reflect failure
            if result.state == ConnectionState.TIMEOUT:
                status = DeviceStatus.OFFLINE
            else:
                status = DeviceStatus.ERROR

            device._state = DeviceState(
                device_id=device.id,
                status=status,
                connected=False,
            )

            self.device_state_changed.emit(str(result.device_id), device._state)
            self.device_connection_failed.emit(
                str(result.device_id), result.error or "Unknown error"
            )

        except Exception as e:
            logger.warning("Error handling device_failed: {}", e)

    def _load_backend_devices(self, backend: DeviceBackend) -> None:
        """Load devices from a backend into the cache."""
        for device in backend.get_all_devices():
            if device.name in self._name_index:
                # Name conflict — prefix with backend name
                logger.warning(
                    "Device name '{}' from backend '{}' conflicts with existing device, skipping",
                    device.name,
                    backend.name,
                )
                continue
            self._device_cache[device.id] = device
            self._name_index[device.name] = device.id
            self._device_backend_map[device.id] = backend.name

    def _rebuild_cache(self) -> None:
        """Rebuild the device cache from all backends."""
        self._device_cache.clear()
        self._name_index.clear()
        self._device_backend_map.clear()

        for backend in self._backends.values():
            if backend.is_connected:
                self._load_backend_devices(backend)

        logger.debug("Rebuilt device cache with {} devices", len(self._device_cache))

    def reload_backends(self) -> bool:
        """Reload every backend from its source and resync the cache.

        This drives the user-facing "refresh" so out-of-band edits
        (e.g. someone editing the happi JSON) propagate. Each backend's
        reload() is expected to preserve live ophyd instances by name,
        so the cache rebuild here is safe to do without tearing down
        active connections.

        Returns:
            True if at least one backend supported reload.
        """
        any_reloaded = False
        for name, backend in self._backends.items():
            if not backend.is_connected:
                continue
            try:
                if backend.reload():
                    any_reloaded = True
                    logger.info("Reloaded backend: {}", name)
            except Exception as e:
                logger.error("Error reloading backend '{}': {}", name, e)

        # Snapshot pre-reload name -> id map so we can emit add/remove
        # signals (carrying UUID strings per existing contract) for
        # entries that appeared or disappeared.
        old_name_ids = dict(self._name_index)
        self._rebuild_cache()
        new_names = set(self._name_index.keys())

        for added in new_names - old_name_ids.keys():
            device = self._device_cache.get(self._name_index[added])
            if device is not None:
                self.device_added.emit(device)
        for removed in old_name_ids.keys() - new_names:
            self.device_removed.emit(str(old_name_ids[removed]))

        return any_reloaded

    # === On-Demand Connection ===

    def request_device_connection(self, device_id: UUID | str) -> bool:
        """Request connection for a device that hasn't been instantiated yet.

        Use this for on-demand connection when instantiate_mode is "none".
        If the device is already connected or connecting, this is a no-op.

        Args:
            device_id: Device ID to connect.

        Returns:
            True if connection was requested, False if already connected or not found.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        device = self._device_cache.get(device_id)
        if device is None:
            logger.warning("Cannot connect unknown device: {}", device_id)
            return False

        # Skip if already connected
        if device._ophyd_device is not None:
            return False

        # Skip if already connecting
        if device._state and device._state.status == DeviceStatus.CONNECTING:
            return False

        # Check if we have a happi result stored
        happi_result = device.metadata.get("_happi_result")
        if happi_result is None:
            logger.warning(
                "Device '{}' has no happi result for on-demand connection",
                device.name,
            )
            return False

        try:
            from lightfall.devices.connection_manager import DeviceConnectionManager

            manager = DeviceConnectionManager.get_instance()
            manager.connect_device(device, happi_result)
            return True

        except Exception as e:
            logger.error("Failed to request device connection: {}", e)
            return False

    def retry_device_connection(self, device_id: UUID | str) -> bool:
        """Retry a failed device connection.

        Args:
            device_id: Device ID to retry.

        Returns:
            True if retry was requested, False if not applicable.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        device = self._device_cache.get(device_id)
        if device is None:
            return False

        # Only retry if in failed state
        if device._state and device._state.status not in (
            DeviceStatus.ERROR,
            DeviceStatus.OFFLINE,
        ):
            return False

        happi_result = device.metadata.get("_happi_result")
        if happi_result is None:
            return False

        try:
            from lightfall.devices.connection_manager import DeviceConnectionManager

            manager = DeviceConnectionManager.get_instance()
            manager.retry_connection(device, happi_result)
            return True

        except Exception as e:
            logger.error("Failed to retry device connection: {}", e)
            return False

    def reconnect_failed_devices(
        self,
        timeout: float = 15.0,
        callback: Any = None,
    ) -> tuple[int, int]:
        """Reconnect all failed devices across all backends.

        Args:
            timeout: Per-device connection timeout in seconds.
            callback: Optional callable(device_name, success) for progress.

        Returns:
            Tuple of (total_connected, total_failed).
        """
        total_connected = 0
        total_failed = 0

        for _name, backend in self._backends.items():
            if hasattr(backend, "reconnect_failed_devices"):
                connected, failed = backend.reconnect_failed_devices(
                    timeout=timeout, callback=callback,
                )
                total_connected += connected
                total_failed += failed

                # Emit signals for state changes
                for device in backend.list_devices():
                    if device._state:
                        if device._state.connected:
                            self.device_connected.emit(str(device.id))
                        elif device._state.status in (DeviceStatus.OFFLINE, DeviceStatus.ERROR):
                            self.device_state_changed.emit(str(device.id), device._state)

        return (total_connected, total_failed)

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

    def _backend_for_device(self, device_id: UUID) -> DeviceBackend | None:
        """Get the backend that owns a device."""
        name = self._device_backend_map.get(device_id)
        if name:
            return self._backends.get(name)
        return self._backend

    def list_devices(
        self,
        category: DeviceCategory | None = None,
        beamline: str | None = None,
        active_only: bool = True,
    ) -> list[DeviceInfo]:
        """List devices with optional filtering.

        Merges results from all connected backends.

        Args:
            category: Filter by device category.
            beamline: Filter by beamline.
            active_only: Only return active devices.

        Returns:
            List of matching devices.
        """
        results: list[DeviceInfo] = []
        for backend in self._backends.values():
            if backend.is_connected:
                results.extend(
                    backend.list_devices(
                        category=category,
                        beamline=beamline,
                        active_only=active_only,
                    )
                )
        return results

    def search_devices(self, query: str) -> list[DeviceInfo]:
        """Search devices by query string across all backends.

        Args:
            query: Search string.

        Returns:
            List of matching devices.
        """
        results: list[DeviceInfo] = []
        for backend in self._backends.values():
            if backend.is_connected:
                results.extend(backend.search_devices(query))
        return results

    def get_all_devices(self) -> list[DeviceInfo]:
        """Get all devices from all backends.

        Returns:
            List of all devices.
        """
        results: list[DeviceInfo] = []
        for backend in self._backends.values():
            if backend.is_connected:
                results.extend(backend.get_all_devices())
        return results

    # === Device Management ===

    def add_device(self, device: DeviceInfo, backend_name: str | None = None) -> bool:
        """Add a new device.

        Args:
            device: Device to add.
            backend_name: Target backend name. Uses primary if not specified.

        Returns:
            True if successfully added.
        """
        backend = self._backends.get(backend_name) if backend_name else self._backend
        if backend is None:
            return False

        if backend.add_device(device):
            self._device_cache[device.id] = device
            self._name_index[device.name] = device.id
            self._device_backend_map[device.id] = backend.name
            self.device_added.emit(device)
            logger.info("Added device: {} (backend: {})", device.name, backend.name)
            return True

        return False

    def update_device(self, device: DeviceInfo) -> bool:
        """Update an existing device.

        Args:
            device: Device with updated information.

        Returns:
            True if successfully updated.
        """
        backend = self._backend_for_device(device.id)
        if backend is None:
            return False

        if backend.update_device(device):
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
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        backend = self._backend_for_device(device_id)
        if backend is None:
            return False

        device = self._device_cache.get(device_id)
        device_name = device.name if device else None

        if backend.remove_device(device_id):
            if device_id in self._device_cache:
                del self._device_cache[device_id]
            if device_name and device_name in self._name_index:
                del self._name_index[device_name]
            self._device_backend_map.pop(device_id, None)

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

    def get_device_configurations(self, device_id: UUID | str) -> list[DeviceConfiguration]:
        """Get all configurations for a device.

        Args:
            device_id: Device ID.

        Returns:
            List of configurations.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        backend = self._backend_for_device(device_id)
        if backend is None:
            return []

        return backend.get_device_configurations(device_id)

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
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        backend = self._backend_for_device(device_id)
        if backend is None:
            return None

        return backend.get_configuration(device_id, config_name)

    def save_configuration(self, config: DeviceConfiguration) -> bool:
        """Save a device configuration.

        Args:
            config: Configuration to save.

        Returns:
            True if successful.
        """
        if config.device_id is None:
            return False
        backend = self._backend_for_device(config.device_id)
        if backend is None:
            return False
        return backend.save_configuration(config)

    def delete_configuration(self, config_id: UUID | str) -> bool:
        """Delete a configuration.

        Args:
            config_id: Configuration ID.

        Returns:
            True if successful.
        """
        if isinstance(config_id, str):
            config_id = UUID(config_id)

        # Need to search all backends since we only have config_id
        for backend in self._backends.values():
            if backend.is_connected and backend.delete_configuration(config_id):
                return True
        return False

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
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        backend = self._backend_for_device(device_id)
        if backend is None:
            return []

        return backend.get_maintenance_history(device_id, limit)

    def add_maintenance_record(self, record: MaintenanceRecord) -> bool:
        """Add a maintenance record.

        Args:
            record: Maintenance record to add.

        Returns:
            True if successful.
        """
        backend = self._backend_for_device(record.device_id)
        if backend is None:
            return False
        return backend.add_maintenance_record(record)

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
            "backends": {
                name: {"connected": b.is_connected, "info": b.get_backend_info()}
                for name, b in self._backends.items()
            },
            "device_count": len(devices),
            "cached_devices": len(self._device_cache),
            "devices_by_category": by_category,
            "devices": [device.to_summary() for device in devices],
        }

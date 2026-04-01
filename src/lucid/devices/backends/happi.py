"""Happi device backend.

This backend loads devices from a Happi database (JSON file or other
Happi-supported backends). Happi is the standard device metadata store
used at LCLS and other photon science facilities.

See: https://github.com/pcdshub/happi
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from uuid import UUID

from loguru import logger

from lucid.devices.base import DeviceBackend
from lucid.devices.model import (
    ConnectionType,
    DeviceCategory,
    DeviceConfiguration,
    DeviceInfo,
    DeviceState,
    DeviceStatus,
    MaintenanceRecord,
)

# Happi item class to NCS DeviceCategory mapping
_CLASS_CATEGORY_MAP: dict[str, DeviceCategory] = {
    "motor": DeviceCategory.MOTOR,
    "positioner": DeviceCategory.MOTOR,
    "detector": DeviceCategory.DETECTOR,
    "areadetector": DeviceCategory.DETECTOR,
    "signal": DeviceCategory.SIGNAL,
    "trigger": DeviceCategory.OTHER,
    "slit": DeviceCategory.MOTOR,
    "lens": DeviceCategory.OPTIC,
    "mirror": DeviceCategory.OPTIC,
    "attenuator": DeviceCategory.OPTIC,
}


def _guess_category(item: Any) -> DeviceCategory:
    """Guess device category from happi item metadata."""
    # Check device_class name
    device_class = getattr(item, "device_class", "") or ""
    for key, cat in _CLASS_CATEGORY_MAP.items():
        if key in device_class.lower():
            return cat

    # Check item type / functional group
    func_group = getattr(item, "functional_group", "") or ""
    for key, cat in _CLASS_CATEGORY_MAP.items():
        if key in func_group.lower():
            return cat

    return DeviceCategory.OTHER


def _guess_connection_type(item: Any) -> ConnectionType:
    """Guess connection type from happi item."""
    prefix = getattr(item, "prefix", "") or ""
    device_class = getattr(item, "device_class", "") or ""

    if "epics" in device_class.lower() or prefix:
        return ConnectionType.EPICS
    return ConnectionType.OTHER


class HappiBackend(DeviceBackend):
    """Device backend that reads from a Happi database.

    Supports JSON file backends (default) and any backend happi supports.
    Devices are loaded from happi and optionally instantiated as ophyd objects.

    Instantiation modes:
        - "none": Load metadata only, no ophyd devices (fastest startup)
        - "blocking": Instantiate ophyd devices synchronously on connect()
        - "background": Load metadata immediately, instantiate in background threads

    Example:
        >>> backend = HappiBackend(path="/path/to/happi.json", instantiate="background")
        >>> backend.connect()
        >>> devices = backend.list_devices()  # Available immediately
        >>> # Devices connect in background, DeviceConnectionManager emits signals
    """

    def __init__(
        self,
        path: str | None = None,
        beamline: str | None = None,
        instantiate: bool | str = False,
        connection_timeout: float | None = None,
    ) -> None:
        """Initialize the Happi backend.

        Args:
            path: Path to a happi JSON database file. If None, uses
                  the HAPPI_BACKEND environment variable / happi defaults.
            beamline: Beamline identifier for device metadata filtering.
            instantiate: Device instantiation mode:
                - False or "none": Metadata only (no ophyd devices)
                - True or "blocking": Synchronous instantiation on connect()
                - "background": Async instantiation via DeviceConnectionManager
            connection_timeout: Timeout for device connections in seconds.
                Only used for "background" mode. If None, uses the
                DeviceConnectionManager's default timeout.
        """
        self._path = path
        self._beamline = beamline
        self._connection_timeout = connection_timeout

        # Normalize instantiate parameter
        if instantiate is False or instantiate == "none":
            self._instantiate_mode = "none"
        elif instantiate is True or instantiate == "blocking":
            self._instantiate_mode = "blocking"
        elif instantiate == "background":
            self._instantiate_mode = "background"
        else:
            logger.warning(
                "Unknown instantiate mode '{}', defaulting to 'none'",
                instantiate,
            )
            self._instantiate_mode = "none"
        self._instantiate = instantiate

        self._client: Any = None  # happi.Client
        self._devices: dict[UUID, DeviceInfo] = {}
        self._configurations: dict[UUID, list[DeviceConfiguration]] = {}
        self._maintenance: dict[UUID, list[MaintenanceRecord]] = {}
        self._connected = False

    @property
    def name(self) -> str:
        return "happi"

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def path(self) -> str | None:
        return self._path

    def connect(self) -> bool:
        """Connect to the happi database and load devices."""
        logger.info("HappiBackend.connect() START (instantiate={})", self._instantiate_mode)
        if self._connected:
            return True

        try:
            import happi
        except ImportError:
            logger.error("happi package not installed. Install with: pip install lucid[happi]")
            return False

        try:
            if self._path:
                from happi.backends.json_db import JSONBackend

                db = JSONBackend(self._path)
                self._client = happi.Client(database=db)
            else:
                # Use default happi config (env vars, etc.)
                self._client = happi.Client.from_config()

            self._discover_devices()
            self._connected = True
            logger.info(
                "Happi backend connected ({} devices from {}, mode={})",
                len(self._devices),
                self._path or "default config",
                self._instantiate_mode,
            )

            # Start background connections if in background mode
            if self._instantiate_mode == "background":
                self._start_background_connections()

            logger.info("HappiBackend.connect() END")
            return True

        except Exception as e:
            logger.error("Failed to connect Happi backend: {}", e)
            self._client = None
            return False

    def disconnect(self) -> None:
        self._client = None
        self._devices.clear()
        self._configurations.clear()
        self._maintenance.clear()
        self._connected = False
        logger.info("Happi backend disconnected")

    def _discover_devices(self) -> None:
        """Load all devices from the happi client."""
        if self._client is None:
            return

        for result in self._client.search():
            try:
                self._add_device_from_result(result)
            except Exception as e:
                name = getattr(result, "name", "?")
                logger.warning("Failed to load happi device '{}': {}", name, e)

    def _start_background_connections(self) -> None:
        """Start background connections for all devices with pending happi results."""
        import importlib

        from lucid.devices.connection_manager import DeviceConnectionManager

        manager = DeviceConnectionManager.get_instance()

        # Connect the manager's signals to update our device cache
        manager.device_connected.connect(self._on_device_connected)
        manager.device_failed.connect(self._on_device_failed)

        # Collect devices that need connection
        to_connect: list[tuple[DeviceInfo, Any]] = []
        for device in self._devices.values():
            happi_result = device.metadata.pop("_happi_result", None)
            if happi_result is not None:
                to_connect.append((device, happi_result))

        if to_connect:
            # Pre-import all device classes on the main thread to avoid
            # import lock deadlocks when multiple background threads try
            # to import from the same package simultaneously.
            seen_modules: set[str] = set()
            for device, happi_result in to_connect:
                device_class = device.device_class or ""
                if "." in device_class:
                    module_path = device_class.rsplit(".", 1)[0]
                    if module_path not in seen_modules:
                        seen_modules.add(module_path)
                        try:
                            importlib.import_module(module_path)
                            logger.debug("Pre-imported device module: {}", module_path)
                        except Exception as e:
                            logger.warning(
                                "Failed to pre-import device module '{}': {}",
                                module_path,
                                e,
                            )

            logger.info(
                "Starting background connection for {} happi devices",
                len(to_connect),
            )
            manager.connect_all(to_connect, timeout=self._connection_timeout)

    def _on_device_connected(self, result: Any) -> None:
        """Handle successful device connection from ConnectionManager."""
        from lucid.devices.connection_manager import ConnectionResult

        if not isinstance(result, ConnectionResult):
            return

        device = self._devices.get(result.device_id)
        if device is None:
            return

        # Update the device with the ophyd instance
        device._ophyd_device = result.ophyd_device
        device._state = DeviceState(
            device_id=device.id,
            status=DeviceStatus.ONLINE,
            connected=True,
        )
        logger.debug("Happi device '{}' connected", device.name)

    def _on_device_failed(self, result: Any) -> None:
        """Handle failed device connection from ConnectionManager."""
        from lucid.devices.connection_manager import ConnectionResult, ConnectionState

        if not isinstance(result, ConnectionResult):
            return

        device = self._devices.get(result.device_id)
        if device is None:
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
        logger.debug(
            "Happi device '{}' connection failed: {}",
            device.name,
            result.error,
        )

    def _add_device_from_result(self, result: Any) -> None:
        """Create DeviceInfo from a happi SearchResult."""
        item = result.item if hasattr(result, "item") else result

        item_name = getattr(item, "name", str(item))
        prefix = getattr(item, "prefix", "") or ""
        device_class = getattr(item, "device_class", "") or ""
        beamline = getattr(item, "beamline", self._beamline) or self._beamline or ""
        location = getattr(item, "location_group", "") or ""
        func_group = getattr(item, "functional_group", "") or ""

        # Filter by beamline if configured
        if self._beamline and beamline and beamline != self._beamline:
            return

        category = _guess_category(item)
        connection_type = _guess_connection_type(item)

        # Build tags
        tags = ["happi"]
        if func_group:
            tags.append(func_group.lower())

        # Collect all happi metadata
        metadata: dict[str, Any] = {}
        if hasattr(item, "post"):
            # Happi item — extract all fields
            for field in item.info_names:
                try:
                    metadata[field] = getattr(item, field, None)
                except Exception:
                    pass
        elif isinstance(item, dict):
            metadata = dict(item)

        device_info = DeviceInfo(
            name=item_name,
            description=f"Happi: {device_class}" if device_class else f"Happi device: {item_name}",
            category=category,
            device_class=device_class,
            connection_type=connection_type,
            prefix=prefix,
            beamline=beamline,
            location=location,
            tags=tags,
            metadata=metadata,
        )

        # Handle instantiation based on mode
        if self._instantiate_mode == "blocking":
            # Synchronous instantiation (original behavior)
            try:
                ophyd_device = result.get() if hasattr(result, "get") else None
                if ophyd_device is not None:
                    device_info._ophyd_device = ophyd_device
            except Exception as e:
                logger.debug("Could not instantiate '{}': {}", item_name, e)

            # Set state based on connection result
            device_info._state = DeviceState(
                device_id=device_info.id,
                status=DeviceStatus.ONLINE if device_info._ophyd_device else DeviceStatus.UNKNOWN,
                connected=device_info._ophyd_device is not None,
            )

        elif self._instantiate_mode == "background":
            # Queue for background connection
            device_info._state = DeviceState(
                device_id=device_info.id,
                status=DeviceStatus.CONNECTING,
                connected=False,
            )
            # Store the happi result for later connection
            device_info.metadata["_happi_result"] = result

        else:
            # "none" mode — metadata only
            device_info._state = DeviceState(
                device_id=device_info.id,
                status=DeviceStatus.UNKNOWN,
                connected=False,
            )

        self._devices[device_info.id] = device_info
        self._configurations[device_info.id] = []
        self._maintenance[device_info.id] = []

    _reconnect_lock = threading.Lock()
    _permanently_failed: set[str] = set()  # Device names that failed 3+ times

    def reconnect_failed_devices(
        self,
        timeout: float = 5.0,
        callback: Any = None,
    ) -> tuple[int, int]:
        """Reconnect devices that failed their initial connection.

        Goes back to the happi client to re-instantiate ophyd devices.
        This works even after the initial happi_result has been consumed.

        Uses a lock to prevent concurrent reconnection attempts.
        Tracks devices that consistently fail and skips them.

        Args:
            timeout: Per-device connection timeout in seconds.
            callback: Optional callable(device_name, success) for progress.

        Returns:
            Tuple of (connected_count, failed_count).
        """
        if not self._reconnect_lock.acquire(blocking=False):
            logger.debug("Reconnect already in progress, skipping")
            return (0, 0)

        try:
            return self._do_reconnect(timeout, callback)
        finally:
            self._reconnect_lock.release()

    def _do_reconnect(
        self, timeout: float, callback: Any
    ) -> tuple[int, int]:
        if self._client is None:
            logger.warning("Cannot reconnect: happi client not available")
            return (0, 0)

        connected = 0
        failed = 0

        # Track per-device failure counts
        if not hasattr(self, "_fail_counts"):
            self._fail_counts: dict[str, int] = {}

        for device in list(self._devices.values()):
            # Skip already connected devices
            if device._ophyd_device is not None:
                continue
            if device._state and device._state.connected:
                continue

            # Skip devices that have failed too many times
            if device.name in self._permanently_failed:
                continue

            try:
                results = self._client.search(name=device.name)
                if not results:
                    failed += 1
                    continue

                obj = results[0].get()
                obj.wait_for_connection(timeout=timeout)

                if obj.connected:
                    device._ophyd_device = obj
                    device._state = DeviceState(
                        device_id=device.id,
                        status=DeviceStatus.ONLINE,
                        connected=True,
                    )
                    connected += 1
                    self._fail_counts.pop(device.name, None)
                    logger.debug("Reconnected device '{}'", device.name)
                    if callback:
                        callback(device.name, True)
                else:
                    failed += 1
                    count = self._fail_counts.get(device.name, 0) + 1
                    self._fail_counts[device.name] = count
                    if count >= 3:
                        self._permanently_failed.add(device.name)
                        logger.debug(
                            "Device '{}' failed {} times, skipping future retries",
                            device.name,
                            count,
                        )
                    if callback:
                        callback(device.name, False)

            except Exception as e:
                failed += 1
                count = self._fail_counts.get(device.name, 0) + 1
                self._fail_counts[device.name] = count
                if count >= 3:
                    self._permanently_failed.add(device.name)
                logger.debug("Failed to reconnect '{}': {}", device.name, e)
                if callback:
                    callback(device.name, False)

        logger.info(
            "Device reconnection: {} connected, {} failed, {} permanently skipped",
            connected,
            failed,
            len(self._permanently_failed),
        )
        return (connected, failed)

    def reset_failed_devices(self) -> None:
        """Clear the permanently failed device list, allowing retries."""
        self._permanently_failed.clear()
        if hasattr(self, "_fail_counts"):
            self._fail_counts.clear()
        logger.info("Reset failed device tracking")

    # === Device CRUD ===

    def get_device(self, device_id: UUID) -> DeviceInfo | None:
        return self._devices.get(device_id)

    def get_device_by_name(self, name: str) -> DeviceInfo | None:
        for d in self._devices.values():
            if d.name == name:
                return d
        return None

    def get_device_by_prefix(self, prefix: str) -> DeviceInfo | None:
        for d in self._devices.values():
            if d.prefix == prefix:
                return d
        return None

    def list_devices(
        self,
        category: DeviceCategory | None = None,
        beamline: str | None = None,
        active_only: bool = True,
    ) -> list[DeviceInfo]:
        result = []
        for d in self._devices.values():
            if active_only and not d.active:
                continue
            if category and d.category != category:
                continue
            if beamline and d.beamline != beamline:
                continue
            result.append(d)
        return result

    def search_devices(self, query: str) -> list[DeviceInfo]:
        return [d for d in self._devices.values() if d.matches_search(query)]

    def add_device(self, device: DeviceInfo) -> bool:
        """Happi backend is read-only from NCS. Use happi CLI to add devices."""
        logger.warning("Happi backend is read-only from NCS. Use happi CLI to manage devices.")
        return False

    def update_device(self, device: DeviceInfo) -> bool:
        if device.id not in self._devices:
            return False
        device.modified = datetime.now()
        self._devices[device.id] = device
        return True

    def remove_device(self, device_id: UUID) -> bool:
        """Happi backend is read-only from NCS."""
        logger.warning("Happi backend is read-only from NCS. Use happi CLI to manage devices.")
        return False

    # === Configuration ===

    def get_device_configurations(self, device_id: UUID) -> list[DeviceConfiguration]:
        return self._configurations.get(device_id, [])

    def get_configuration(self, device_id: UUID, config_name: str) -> DeviceConfiguration | None:
        for c in self._configurations.get(device_id, []):
            if c.name == config_name:
                return c
        return None

    def save_configuration(self, config: DeviceConfiguration) -> bool:
        if config.device_id is None or config.device_id not in self._configurations:
            return False
        configs = self._configurations[config.device_id]
        for i, existing in enumerate(configs):
            if existing.name == config.name:
                configs[i] = config
                return True
        configs.append(config)
        return True

    def delete_configuration(self, config_id: UUID) -> bool:
        for configs in self._configurations.values():
            for i, c in enumerate(configs):
                if c.id == config_id:
                    del configs[i]
                    return True
        return False

    # === Maintenance ===

    def get_maintenance_history(self, device_id: UUID, limit: int = 100) -> list[MaintenanceRecord]:
        records = self._maintenance.get(device_id, [])
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records[:limit]

    def add_maintenance_record(self, record: MaintenanceRecord) -> bool:
        if record.device_id not in self._maintenance:
            self._maintenance[record.device_id] = []
        self._maintenance[record.device_id].append(record)
        return True

    # === Introspection ===

    def get_backend_info(self) -> dict[str, Any]:
        # Count devices by connection state
        connected_count = sum(1 for d in self._devices.values() if d._ophyd_device is not None)
        connecting_count = sum(
            1
            for d in self._devices.values()
            if d._state and d._state.status == DeviceStatus.CONNECTING
        )

        return {
            "name": self.name,
            "connected": self.is_connected,
            "device_count": len(self._devices),
            "devices_connected": connected_count,
            "devices_connecting": connecting_count,
            "path": self._path,
            "beamline": self._beamline,
            "instantiate_mode": self._instantiate_mode,
            "connection_timeout": self._connection_timeout,
        }

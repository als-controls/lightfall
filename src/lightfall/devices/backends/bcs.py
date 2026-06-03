"""BCS device backend using bcsophyd-zmq.

This backend connects to a BCS (Beamline Control System) server via ZMQ
and discovers devices through the bcsophyd library. Discovered devices
are exposed as ophyd-compatible objects for use with Bluesky.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Any
from uuid import UUID

from loguru import logger

from lightfall.devices.base import DeviceBackend
from lightfall.devices.model import (
    ConnectionType,
    DeviceCategory,
    DeviceConfiguration,
    DeviceInfo,
    DeviceState,
    DeviceStatus,
    MaintenanceRecord,
)

# BCS itemType to NCS DeviceCategory mapping
BCS_TYPE_MAP: dict[str, DeviceCategory] = {
    "motor": DeviceCategory.MOTOR,
    "detector": DeviceCategory.DETECTOR,
    "ai": DeviceCategory.CONTROLLER,
}


class BCSBackend(DeviceBackend):
    """Device backend for BCS systems via ZMQ.

    Connects to a BCS server using the bcsophyd library and discovers
    available devices. Devices are automatically populated from the
    BCS device database and exposed as ophyd objects.

    Note: BCS devices are discovered automatically. Manual add/remove
    operations are not supported - use the BCS system to manage devices.

    Example:
        >>> backend = BCSBackend(host="bcs-server.lbl.gov", port=5577)
        >>> backend.connect()
        >>> motors = backend.list_devices(category=DeviceCategory.MOTOR)
        >>> motor = backend.get_device_by_name("sample_x")
        >>> motor.ophyd_device.set(10).wait()
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5577,
        timeout_ms: int = 5000,
        beamline: str | None = None,
    ) -> None:
        """Initialize the BCS backend.

        Args:
            host: BCS server hostname or IP address.
            port: BCS server ZMQ port.
            timeout_ms: Connection timeout in milliseconds.
            beamline: Beamline identifier for device metadata.
        """
        self._host = host
        self._port = port
        self._timeout_ms = timeout_ms
        self._beamline = beamline

        self._devices: dict[UUID, DeviceInfo] = {}
        self._configurations: dict[UUID, list[DeviceConfiguration]] = {}
        self._maintenance: dict[UUID, list[MaintenanceRecord]] = {}
        self._connected = False
        self._manager: Any = None  # BCSDeviceManager instance

    @property
    def name(self) -> str:
        """Get the backend name."""
        return "bcs_zmq"

    @property
    def is_connected(self) -> bool:
        """Check if backend is connected."""
        return self._connected

    @property
    def host(self) -> str:
        """Get the BCS server host."""
        return self._host

    @property
    def port(self) -> int:
        """Get the BCS server port."""
        return self._port

    def _run_async(self, coro: Any) -> Any:
        """Run async coroutine in dedicated thread to avoid Qt event loop conflict.

        Args:
            coro: Async coroutine to run.

        Returns:
            Result of the coroutine.
        """
        result = None
        exception = None

        def run_in_thread() -> None:
            nonlocal result, exception
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(coro)
            except Exception as e:
                exception = e
            finally:
                loop.close()

        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join()

        if exception:
            raise exception
        return result

    def connect(self) -> bool:
        """Connect to BCS server and discover devices."""
        if self._connected:
            return True

        try:
            from bcsophyd.zmq import BCSDeviceManager
        except ImportError:
            logger.error(
                "bcsophyd package not installed. "
                "Install with: pip install ncs[bcs]"
            )
            return False

        try:
            # Create manager and connect
            self._manager = BCSDeviceManager(
                host=self._host,
                port=self._port,
                timeout_ms=self._timeout_ms,
            )

            # Run async connect in dedicated thread
            self._run_async(self._manager.connect())

            # Discover and populate devices from Happi client
            self._discover_devices()

            self._connected = True
            logger.info(
                "BCS backend connected to {}:{} with {} devices",
                self._host,
                self._port,
                len(self._devices),
            )
            return True

        except Exception as e:
            logger.error("Failed to connect BCS backend: {}", e)
            self._manager = None
            return False

    def disconnect(self) -> None:
        """Disconnect from BCS server."""
        if self._manager is not None:
            try:
                # BCSDeviceManager may have async disconnect
                if hasattr(self._manager, "disconnect"):
                    self._run_async(self._manager.disconnect())
            except Exception as e:
                logger.warning("Error during BCS disconnect: {}", e)

        self._manager = None
        self._devices.clear()
        self._configurations.clear()
        self._maintenance.clear()
        self._connected = False
        logger.info("BCS backend disconnected")

    def _discover_devices(self) -> None:
        """Discover devices from the BCS Happi client."""
        if self._manager is None or self._manager.client is None:
            logger.warning("No Happi client available for device discovery")
            return

        client = self._manager.client

        # client.items() returns (name, SearchResult) tuples
        for item_name, happi_item in client.items():
            try:
                self._add_device_from_happi(happi_item)
            except Exception as e:
                logger.warning("Failed to load device '{}': {}", item_name, e)

        logger.debug("Discovered {} devices from BCS", len(self._devices))

    def _add_device_from_happi(self, happi_item: Any) -> None:
        """Create DeviceInfo from a Happi SearchResult item.

        Args:
            happi_item: Happi SearchResult containing device metadata.
                SearchResult is a Mapping - access metadata via result['key']
                or result.metadata.get('key').
        """
        # SearchResult is a Mapping - use dict-style access for metadata
        metadata = happi_item.metadata if hasattr(happi_item, "metadata") else {}

        # Extract item type and map to category
        item_type = metadata.get("itemType") or metadata.get("item_type", "other")
        category = BCS_TYPE_MAP.get(str(item_type).lower(), DeviceCategory.CONTROLLER)

        # Get device name
        name = metadata.get("name", str(happi_item))
        original_name = metadata.get("originalName") or metadata.get(
            "original_name", name
        )

        # Get units if available
        units = metadata.get("units") or metadata.get("egu", "")

        # Determine device class based on type
        device_class_map = {
            "motor": "bcsophyd.zmq.bcs_motor.BCSMotor",
            "ai": "bcsophyd.zmq.bcs_signal.BCSSignal",
            "detector": "bcsophyd.zmq.bcs_area_detector.BCSAreaDetector",
        }
        device_class = device_class_map.get(
            str(item_type).lower(), "bcsophyd.zmq.bcs_device.BCSDevice"
        )

        # Create DeviceInfo
        device_info = DeviceInfo(
            name=name,
            description=f"BCS {item_type}: {original_name}",
            category=category,
            device_class=device_class,
            connection_type=ConnectionType.BCS_ZMQ,
            prefix=original_name,  # Use original BCS name as prefix
            beamline=self._beamline,
            location="",
            tags=["bcs", str(item_type).lower()],
            metadata={
                "host": self._host,
                "port": self._port,
                "original_name": original_name,
                "units": units,
                "bcs_type": str(item_type),
            },
        )

        # Instantiate the ophyd device
        try:
            ophyd_device = happi_item.get()
            device_info._ophyd_device = ophyd_device
        except Exception as e:
            logger.warning("Failed to instantiate ophyd device '{}': {}", name, e)

        # Add to internal storage
        self._add_device_internal(device_info)

    def _add_device_internal(self, device: DeviceInfo) -> None:
        """Internal method to add device to storage."""
        self._devices[device.id] = device
        self._configurations[device.id] = []
        self._maintenance[device.id] = []

        # Create default configuration from metadata
        default_config = DeviceConfiguration(
            name="default",
            device_id=device.id,
            parameters=device.metadata.copy(),
        )
        self._configurations[device.id].append(default_config)

        # Update device state
        device._state = DeviceState(
            device_id=device.id,
            status=DeviceStatus.ONLINE if device._ophyd_device else DeviceStatus.OFFLINE,
            connected=device._ophyd_device is not None,
        )

    # === Device CRUD Operations ===

    def get_device(self, device_id: UUID) -> DeviceInfo | None:
        """Get a device by ID."""
        return self._devices.get(device_id)

    def get_device_by_name(self, name: str) -> DeviceInfo | None:
        """Get a device by name."""
        for device in self._devices.values():
            if device.name == name:
                return device
        return None

    def get_device_by_prefix(self, prefix: str) -> DeviceInfo | None:
        """Get a device by connection prefix (original BCS name)."""
        for device in self._devices.values():
            if device.prefix == prefix:
                return device
        return None

    def list_devices(
        self,
        category: DeviceCategory | None = None,
        beamline: str | None = None,
        active_only: bool = True,
    ) -> list[DeviceInfo]:
        """List devices with optional filtering."""
        result = []
        for device in self._devices.values():
            if active_only and not device.active:
                continue
            if category and device.category != category:
                continue
            if beamline and device.beamline != beamline:
                continue
            result.append(device)
        return result

    def search_devices(self, query: str) -> list[DeviceInfo]:
        """Search devices by query string."""
        return [d for d in self._devices.values() if d.matches_search(query)]

    def add_device(self, device: DeviceInfo) -> bool:
        """Add a new device.

        Note: BCS devices are discovered automatically from the BCS server.
        Manual device addition is not supported.

        Returns:
            Always False - use BCS system to add devices.
        """
        logger.warning(
            "BCS devices are discovered automatically. "
            "Use the BCS system to add devices."
        )
        return False

    def update_device(self, device: DeviceInfo) -> bool:
        """Update an existing device."""
        if device.id not in self._devices:
            return False
        device.modified = datetime.now()
        self._devices[device.id] = device
        return True

    def remove_device(self, device_id: UUID) -> bool:
        """Remove a device.

        Note: BCS devices are discovered automatically from the BCS server.
        Manual device removal is not supported.

        Returns:
            Always False - use BCS system to remove devices.
        """
        logger.warning(
            "BCS devices are discovered automatically. "
            "Use the BCS system to remove devices."
        )
        return False

    # === Configuration Operations ===

    def get_device_configurations(
        self, device_id: UUID
    ) -> list[DeviceConfiguration]:
        """Get all configurations for a device."""
        return self._configurations.get(device_id, [])

    def get_configuration(
        self, device_id: UUID, config_name: str
    ) -> DeviceConfiguration | None:
        """Get a specific configuration by name."""
        configs = self._configurations.get(device_id, [])
        for config in configs:
            if config.name == config_name:
                return config
        return None

    def save_configuration(self, config: DeviceConfiguration) -> bool:
        """Save a device configuration."""
        if config.device_id is None:
            return False
        if config.device_id not in self._configurations:
            self._configurations[config.device_id] = []

        # Update existing or add new
        configs = self._configurations[config.device_id]
        for i, existing in enumerate(configs):
            if existing.name == config.name:
                configs[i] = config
                return True

        configs.append(config)
        return True

    def delete_configuration(self, config_id: UUID) -> bool:
        """Delete a configuration."""
        for _device_id, configs in self._configurations.items():
            for i, config in enumerate(configs):
                if config.id == config_id:
                    del configs[i]
                    return True
        return False

    # === Maintenance Records ===

    def get_maintenance_history(
        self, device_id: UUID, limit: int = 100
    ) -> list[MaintenanceRecord]:
        """Get maintenance history for a device."""
        records = self._maintenance.get(device_id, [])
        # Sort by timestamp descending
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records[:limit]

    def add_maintenance_record(self, record: MaintenanceRecord) -> bool:
        """Add a maintenance record."""
        if record.device_id not in self._maintenance:
            self._maintenance[record.device_id] = []
        self._maintenance[record.device_id].append(record)
        return True

    # === Introspection ===

    def get_backend_info(self) -> dict[str, Any]:
        """Get information about the backend."""
        return {
            "name": self.name,
            "connected": self.is_connected,
            "device_count": len(self._devices),
            "host": self._host,
            "port": self._port,
            "beamline": self._beamline,
            "categories": list({d.category.value for d in self._devices.values()}),
        }

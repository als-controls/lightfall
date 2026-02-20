"""Happi device backend.

This backend loads devices from a Happi database (JSON file or other
Happi-supported backends). Happi is the standard device metadata store
used at LCLS and other photon science facilities.

See: https://github.com/pcdshub/happi
"""

from __future__ import annotations

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

    Example:
        >>> backend = HappiBackend(path="/path/to/happi.json")
        >>> backend.connect()
        >>> devices = backend.list_devices()
    """

    def __init__(
        self,
        path: str | None = None,
        beamline: str | None = None,
        instantiate: bool = False,
    ) -> None:
        """Initialize the Happi backend.

        Args:
            path: Path to a happi JSON database file. If None, uses
                  the HAPPI_BACKEND environment variable / happi defaults.
            beamline: Beamline identifier for device metadata filtering.
            instantiate: If True, call item.get() to instantiate ophyd
                         devices on load. If False, only load metadata.
        """
        self._path = path
        self._beamline = beamline
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
        if self._connected:
            return True

        try:
            import happi
        except ImportError:
            logger.error(
                "happi package not installed. "
                "Install with: pip install ncs[happi]"
            )
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
                "Happi backend connected ({} devices from {})",
                len(self._devices),
                self._path or "default config",
            )
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

        # Optionally instantiate the ophyd device
        if self._instantiate:
            try:
                ophyd_device = result.get() if hasattr(result, "get") else None
                if ophyd_device is not None:
                    device_info._ophyd_device = ophyd_device
            except Exception as e:
                logger.debug("Could not instantiate '{}': {}", item_name, e)

        # Set initial state
        device_info._state = DeviceState(
            device_id=device_info.id,
            status=DeviceStatus.ONLINE if device_info._ophyd_device else DeviceStatus.UNKNOWN,
            connected=device_info._ophyd_device is not None,
        )

        self._devices[device_info.id] = device_info
        self._configurations[device_info.id] = []
        self._maintenance[device_info.id] = []

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
        return {
            "name": self.name,
            "connected": self.is_connected,
            "device_count": len(self._devices),
            "path": self._path,
            "beamline": self._beamline,
            "instantiate": self._instantiate,
        }

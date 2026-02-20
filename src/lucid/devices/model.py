"""Data models for NCS device management.

This module provides Pydantic models for:
- DeviceInfo: Core device metadata
- DeviceConfiguration: Device settings and parameters
- MaintenanceRecord: Maintenance and calibration history
- DeviceState: Current device state
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DeviceCategory(str, Enum):
    """Categories of devices."""

    MOTOR = "motor"
    DETECTOR = "detector"
    SENSOR = "sensor"
    CONTROLLER = "controller"
    SIGNAL = "signal"
    POSITIONER = "positioner"
    CAMERA = "camera"
    OPTIC = "optic"
    OTHER = "other"


class DeviceStatus(str, Enum):
    """Operational status of a device."""

    ONLINE = "online"  # Device is connected and operational
    OFFLINE = "offline"  # Device is not connected
    ERROR = "error"  # Device has an error condition
    MAINTENANCE = "maintenance"  # Device is under maintenance
    UNKNOWN = "unknown"  # Status cannot be determined


class ConnectionType(str, Enum):
    """Type of device connection."""

    EPICS = "epics"  # EPICS Channel Access
    TANGO = "tango"  # Tango Controls
    SIMULATED = "simulated"  # Simulated/mock device
    SERIAL = "serial"  # Serial/RS-232
    TCP = "tcp"  # TCP/IP socket
    USB = "usb"  # USB connection
    BCS_ZMQ = "bcs_zmq"  # BCS via ZMQ protocol
    OTHER = "other"


class DeviceConfiguration(BaseModel):
    """Configuration settings for a device.

    Stores device-specific configuration parameters that can be
    saved, restored, and version-controlled.

    Attributes:
        id: Unique identifier for this configuration.
        name: Configuration name (e.g., "default", "high_resolution").
        device_id: ID of the device this configuration belongs to.
        created: When this configuration was created.
        modified: When this configuration was last modified.
        parameters: Device-specific parameter values.
        metadata: Additional configuration metadata.
    """

    id: UUID = Field(default_factory=uuid4)
    name: str = "default"
    device_id: UUID | None = None
    created: datetime = Field(default_factory=datetime.now)
    modified: datetime = Field(default_factory=datetime.now)
    parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def update_parameter(self, key: str, value: Any) -> None:
        """Update a configuration parameter.

        Args:
            key: Parameter name.
            value: Parameter value.
        """
        self.parameters[key] = value
        self.modified = datetime.now()


class MaintenanceRecord(BaseModel):
    """Record of device maintenance or calibration.

    Tracks maintenance activities, repairs, calibrations, and
    configuration changes for audit and history purposes.

    Attributes:
        id: Unique identifier for this record.
        device_id: ID of the device.
        timestamp: When the maintenance occurred.
        maintenance_type: Type of maintenance performed.
        description: Detailed description of work done.
        performed_by: Who performed the maintenance.
        notes: Additional notes.
        attachments: References to attached documents/images.
    """

    id: UUID = Field(default_factory=uuid4)
    device_id: UUID
    timestamp: datetime = Field(default_factory=datetime.now)
    maintenance_type: str  # "calibration", "repair", "inspection", "config_change"
    description: str
    performed_by: str = ""
    notes: str = ""
    attachments: list[str] = Field(default_factory=list)


class DeviceState(BaseModel):
    """Current state of a device.

    Captures the real-time state of a device including
    readback values and status indicators.

    Attributes:
        device_id: ID of the device.
        timestamp: When this state was captured.
        status: Current operational status.
        connected: Whether device is connected.
        position: Current position (for positioners).
        value: Current value (for signals/sensors).
        alarm_status: Current alarm status.
        alarm_severity: Current alarm severity.
        additional: Additional state information.
    """

    device_id: UUID
    timestamp: datetime = Field(default_factory=datetime.now)
    status: DeviceStatus = DeviceStatus.UNKNOWN
    connected: bool = False
    position: float | None = None
    value: Any = None
    alarm_status: str = "NO_ALARM"
    alarm_severity: str = "NO_ALARM"
    additional: dict[str, Any] = Field(default_factory=dict)


class DeviceInfo(BaseModel):
    """Core metadata about a device.

    DeviceInfo contains all the information needed to identify,
    locate, and interact with a device in the control system.

    Attributes:
        id: Unique identifier for this device.
        name: Human-readable device name.
        description: Detailed description of the device.
        category: Device category (motor, detector, etc.).
        device_class: Python class path for the device (e.g., "ophyd.EpicsMotor").
        connection_type: How the device connects.
        prefix: Connection prefix (e.g., EPICS PV prefix).
        beamline: Beamline this device belongs to.
        location: Physical location description.
        tags: Tags for searching and grouping.
        created: When this device was added to the catalog.
        modified: When this device info was last modified.
        active: Whether this device is active in the system.
        metadata: Additional device metadata.
    """

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    category: DeviceCategory = DeviceCategory.OTHER
    device_class: str = ""  # e.g., "ophyd.EpicsMotor", "ophyd.sim.SynAxis"
    connection_type: ConnectionType = ConnectionType.SIMULATED
    prefix: str = ""  # EPICS PV prefix or other connection identifier
    beamline: str | None = None
    location: str = ""
    tags: list[str] = Field(default_factory=list)
    created: datetime = Field(default_factory=datetime.now)
    modified: datetime = Field(default_factory=datetime.now)
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Runtime state (not persisted)
    _state: DeviceState | None = None
    _ophyd_device: Any = None  # The actual ophyd device instance

    class Config:
        """Pydantic config."""

        # Allow arbitrary types for ophyd device
        arbitrary_types_allowed = True

    @property
    def state(self) -> DeviceState | None:
        """Get the current device state."""
        return self._state

    @state.setter
    def state(self, value: DeviceState) -> None:
        """Set the device state."""
        self._state = value

    @property
    def ophyd_device(self) -> Any:
        """Get the ophyd device instance."""
        return self._ophyd_device

    @ophyd_device.setter
    def ophyd_device(self, value: Any) -> None:
        """Set the ophyd device instance."""
        self._ophyd_device = value

    def matches_search(self, query: str) -> bool:
        """Check if device matches a search query.

        Args:
            query: Search string.

        Returns:
            True if device matches.
        """
        query_lower = query.lower()
        searchable = [
            self.name.lower(),
            self.description.lower(),
            self.prefix.lower(),
            self.category.value.lower(),
            self.location.lower(),
        ] + [tag.lower() for tag in self.tags]

        return any(query_lower in item for item in searchable)

    def to_summary(self) -> dict[str, Any]:
        """Get a summary dict for display.

        Returns:
            Summary dictionary.
        """
        return {
            "id": str(self.id),
            "name": self.name,
            "category": self.category.value,
            "prefix": self.prefix,
            "status": self._state.status.value if self._state else "unknown",
            "connected": self._state.connected if self._state else False,
            "active": self.active,
        }


class DeviceSnapshot(BaseModel):
    """Snapshot of multiple device states.

    Used for saving and restoring device configurations,
    and for logbook entries capturing system state.

    Attributes:
        id: Unique identifier for this snapshot.
        name: Snapshot name.
        description: Description of when/why snapshot was taken.
        timestamp: When the snapshot was taken.
        taken_by: Who took the snapshot.
        device_states: Map of device ID to captured state.
        device_configs: Map of device ID to configuration at snapshot time.
    """

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    taken_by: str = ""
    device_states: dict[str, DeviceState] = Field(default_factory=dict)
    device_configs: dict[str, DeviceConfiguration] = Field(default_factory=dict)

    def get_device_state(self, device_id: str | UUID) -> DeviceState | None:
        """Get state for a specific device.

        Args:
            device_id: Device ID.

        Returns:
            Device state or None.
        """
        key = str(device_id)
        return self.device_states.get(key)

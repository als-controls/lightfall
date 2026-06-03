"""Device management for NCS.

This package provides:
- DeviceCatalog: Unified device access facade
- DeviceConnectionManager: Background device connection with timeouts
- DeviceBackend: Abstract base for storage backends
- Device models: DeviceInfo, DeviceConfiguration, etc.
- MockBackend: Simulated devices using ophyd.sim
- DeviceMetricsCollector: Device monitoring and health tracking
"""

from lightfall.devices.base import DeviceBackend
from lightfall.devices.catalog import DeviceCatalog
from lightfall.devices.connection_manager import (
    ConnectionResult,
    ConnectionState,
    DeviceConnectionManager,
)
from lightfall.devices.model import (
    ConnectionType,
    DeviceCategory,
    DeviceConfiguration,
    DeviceInfo,
    DeviceSnapshot,
    DeviceState,
    DeviceStatus,
    MaintenanceRecord,
)
from lightfall.devices.monitoring import DeviceHealth, DeviceMetric, DeviceMetricsCollector

__all__ = [
    # Catalog
    "DeviceCatalog",
    # Connection
    "DeviceConnectionManager",
    "ConnectionState",
    "ConnectionResult",
    # Backend
    "DeviceBackend",
    # Models
    "ConnectionType",
    "DeviceCategory",
    "DeviceConfiguration",
    "DeviceInfo",
    "DeviceSnapshot",
    "DeviceState",
    "DeviceStatus",
    "MaintenanceRecord",
    # Monitoring
    "DeviceHealth",
    "DeviceMetric",
    "DeviceMetricsCollector",
]

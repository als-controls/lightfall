"""Device management for NCS.

This package provides:
- DeviceCatalog: Unified device access facade
- DeviceBackend: Abstract base for storage backends
- Device models: DeviceInfo, DeviceConfiguration, etc.
- MockBackend: Simulated devices using ophyd.sim
- DeviceMetricsCollector: Device monitoring and health tracking
"""

from ncs.devices.base import DeviceBackend
from ncs.devices.catalog import DeviceCatalog
from ncs.devices.model import (
    ConnectionType,
    DeviceCategory,
    DeviceConfiguration,
    DeviceInfo,
    DeviceSnapshot,
    DeviceState,
    DeviceStatus,
    MaintenanceRecord,
)
from ncs.devices.monitoring import DeviceHealth, DeviceMetric, DeviceMetricsCollector

__all__ = [
    # Catalog
    "DeviceCatalog",
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

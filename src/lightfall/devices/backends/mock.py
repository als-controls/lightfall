"""Mock device backend with ophyd.sim simulated devices.

This backend provides simulated devices for development and testing.
It uses ophyd.sim to create realistic device simulations that can be
used with Bluesky plans.
"""

from __future__ import annotations

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


class MockBackend(DeviceBackend):
    """In-memory device backend with ophyd.sim devices.

    This backend creates a set of simulated devices using ophyd.sim
    for development and testing. The devices behave like real hardware
    and can be used with Bluesky plans.

    Simulated devices include:
    - Motors (SynAxis): x, y, z linear stages; theta rotation
    - Detectors (SynGauss, SynSignal): point detector, noisy detector
    - Signals (SynSignal): temperature, pressure sensors

    Example:
        >>> backend = MockBackend()
        >>> backend.connect()
        >>> devices = backend.list_devices()
        >>> motor = backend.get_device_by_name("x")
        >>> motor.ophyd_device.set(10).wait()
    """

    def __init__(self, include_noisy: bool = True) -> None:
        """Initialize the mock backend.

        Args:
            include_noisy: Include noisy/random signal devices.
        """
        self._include_noisy = include_noisy
        self._devices: dict[UUID, DeviceInfo] = {}
        self._configurations: dict[UUID, list[DeviceConfiguration]] = {}
        self._maintenance: dict[UUID, list[MaintenanceRecord]] = {}
        self._connected = False
        self._ophyd_devices: dict[str, Any] = {}

    @property
    def name(self) -> str:
        """Get the backend name."""
        return "mock"

    @property
    def is_connected(self) -> bool:
        """Check if backend is connected."""
        return self._connected

    def connect(self) -> bool:
        """Connect and initialize simulated devices."""
        if self._connected:
            return True

        try:
            self._create_simulated_devices()
            self._connected = True
            logger.info("Mock backend connected with {} devices", len(self._devices))
            return True
        except Exception as e:
            logger.error("Failed to connect mock backend: {}", e)
            return False

    def disconnect(self) -> None:
        """Disconnect and cleanup."""
        self._connected = False
        self._ophyd_devices.clear()
        logger.info("Mock backend disconnected")

    def _create_simulated_devices(self) -> None:
        """Create the simulated ophyd devices.

        Note: we deliberately construct fresh ``SynAxis``/``SynGauss`` instances
        with explicit ``value=0.0`` rather than importing the module-level
        singletons (``ophyd.sim.motor``, ``ophyd.sim.det2``, ...). The
        singletons are created with ``SynAxis()``'s default ``value=0`` (a
        Python int), which causes ``setpoint.describe()`` to report
        ``dtype="integer"`` / ``dtype_numpy="<i8"`` at run-start — before any
        ``mv()`` call has had a chance to coerce the sim_state to float.
        TiledWriter then bakes the SQL appendable-table column as int64, and
        the first fractional motor position written into it (e.g. from
        ``tune_centroid``) raises ``ArrowInvalid: Float value … was truncated
        converting to int64`` and aborts the run.

        The fix is to declare the float type at construction. Real EPICS
        signals don't have this problem because the IOC declares the dtype.
        """
        try:
            from ophyd.sim import (
                SynAxis,
                SynGauss,
                SynSignal,  # noqa: F401  (used elsewhere in this method)
            )
        except ImportError:
            logger.warning("ophyd.sim not available, creating minimal mock devices")
            self._create_minimal_mock_devices()
            return

        # Build motors with explicit float starting positions so describe()
        # reports the correct dtype before any move occurs.
        motor = SynAxis(name="motor", value=0.0, labels={"motors"})
        motor1 = SynAxis(name="motor1", value=0.0, labels={"motors"})
        motor2 = SynAxis(name="motor2", value=0.0, labels={"motors"})
        motor3 = SynAxis(name="motor3", value=0.0, labels={"motors"})

        # Recreate the SynGauss detectors against our local motors. Mirror the
        # parameters used by ``ophyd.sim.hw()`` so existing tutorials/tests
        # behave identically.
        det = SynGauss(
            "det", motor, "motor", center=0, Imax=1, sigma=1, labels={"detectors"},
        )
        det1 = SynGauss(
            "det1", motor1, "motor1", center=0, Imax=5, sigma=0.5, labels={"detectors"},
        )
        det2 = SynGauss(
            "det2", motor2, "motor2", center=1, Imax=2, sigma=2, labels={"detectors"},
        )
        noisy_det = SynGauss(
            "noisy_det", motor, "motor",
            center=0, Imax=1, sigma=1,
            noise="uniform", noise_multiplier=0.1,
            labels={"detectors"},
        )

        # === Motors ===

        # Standard motor (motor from ophyd.sim)
        motor_info = DeviceInfo(
            name="motor",
            description="Primary sample motor (X axis)",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="motor",
            location="Sample Stage",
            tags=["motor", "sample", "x-axis", "primary"],
            metadata={"units": "mm", "precision": 3},
        )
        motor_info._ophyd_device = motor
        self._add_device_internal(motor_info)
        self._ophyd_devices["motor"] = motor

        # Motor 1
        motor1_info = DeviceInfo(
            name="motor1",
            description="Secondary motor (Y axis)",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="motor1",
            location="Sample Stage",
            tags=["motor", "sample", "y-axis"],
            metadata={"units": "mm", "precision": 3},
        )
        motor1_info._ophyd_device = motor1
        self._add_device_internal(motor1_info)
        self._ophyd_devices["motor1"] = motor1

        # Motor 2
        motor2_info = DeviceInfo(
            name="motor2",
            description="Tertiary motor (Z axis)",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="motor2",
            location="Sample Stage",
            tags=["motor", "sample", "z-axis"],
            metadata={"units": "mm", "precision": 3},
        )
        motor2_info._ophyd_device = motor2
        self._add_device_internal(motor2_info)
        self._ophyd_devices["motor2"] = motor2

        # Motor 3 (rotation)
        motor3_info = DeviceInfo(
            name="motor3",
            description="Rotation motor (theta)",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="motor3",
            location="Sample Stage",
            tags=["motor", "sample", "rotation", "theta"],
            metadata={"units": "deg", "precision": 2},
        )
        motor3_info._ophyd_device = motor3
        self._add_device_internal(motor3_info)
        self._ophyd_devices["motor3"] = motor3

        # === Detectors ===

        # Primary detector
        det_info = DeviceInfo(
            name="det",
            description="Primary point detector (Gaussian response)",
            category=DeviceCategory.DETECTOR,
            device_class="ophyd.sim.SynGauss",
            connection_type=ConnectionType.SIMULATED,
            prefix="det",
            location="Detector Arm",
            tags=["detector", "primary", "point"],
            metadata={"type": "point_detector"},
        )
        det_info._ophyd_device = det
        self._add_device_internal(det_info)
        self._ophyd_devices["det"] = det

        # Secondary detectors
        det1_info = DeviceInfo(
            name="det1",
            description="Secondary detector 1",
            category=DeviceCategory.DETECTOR,
            device_class="ophyd.sim.SynGauss",
            connection_type=ConnectionType.SIMULATED,
            prefix="det1",
            location="Detector Arm",
            tags=["detector", "secondary"],
            metadata={"type": "point_detector"},
        )
        det1_info._ophyd_device = det1
        self._add_device_internal(det1_info)
        self._ophyd_devices["det1"] = det1

        det2_info = DeviceInfo(
            name="det2",
            description="Secondary detector 2",
            category=DeviceCategory.DETECTOR,
            device_class="ophyd.sim.SynGauss",
            connection_type=ConnectionType.SIMULATED,
            prefix="det2",
            location="Detector Arm",
            tags=["detector", "secondary"],
            metadata={"type": "point_detector"},
        )
        det2_info._ophyd_device = det2
        self._add_device_internal(det2_info)
        self._ophyd_devices["det2"] = det2

        # Noisy detector
        if self._include_noisy:
            noisy_det_info = DeviceInfo(
                name="noisy_det",
                description="Noisy detector with random fluctuations",
                category=DeviceCategory.DETECTOR,
                device_class="ophyd.sim.SynGauss",
                connection_type=ConnectionType.SIMULATED,
                prefix="noisy_det",
                location="Detector Arm",
                tags=["detector", "noisy", "testing"],
                metadata={"type": "point_detector", "noise_level": "high"},
            )
            noisy_det_info._ophyd_device = noisy_det
            self._add_device_internal(noisy_det_info)
            self._ophyd_devices["noisy_det"] = noisy_det

        # === Additional Simulated Sensors ===

        # Create custom SynSignal devices for sensors
        temperature = SynSignal(name="temperature", func=lambda: 22.5 + 0.1 * (datetime.now().second % 10))
        temp_info = DeviceInfo(
            name="temperature",
            description="Sample temperature sensor",
            category=DeviceCategory.DETECTOR,
            device_class="ophyd.sim.SynSignal",
            connection_type=ConnectionType.SIMULATED,
            prefix="temperature",
            location="Sample Environment",
            tags=["sensor", "temperature", "sample"],
            metadata={"units": "C", "precision": 2},
        )
        temp_info._ophyd_device = temperature
        self._add_device_internal(temp_info)
        self._ophyd_devices["temperature"] = temperature

        pressure = SynSignal(name="pressure", func=lambda: 1.013e5 + 100 * (datetime.now().second % 5))
        pressure_info = DeviceInfo(
            name="pressure",
            description="Chamber pressure sensor",
            category=DeviceCategory.DETECTOR,
            device_class="ophyd.sim.SynSignal",
            connection_type=ConnectionType.SIMULATED,
            prefix="pressure",
            location="Vacuum Chamber",
            tags=["sensor", "pressure", "vacuum"],
            metadata={"units": "Pa", "precision": 0},
        )
        pressure_info._ophyd_device = pressure
        self._add_device_internal(pressure_info)
        self._ophyd_devices["pressure"] = pressure

        # Ring current simulation
        ring_current = SynSignal(name="ring_current", func=lambda: 500.0 - 0.01 * datetime.now().minute)
        ring_info = DeviceInfo(
            name="ring_current",
            description="Storage ring current",
            category=DeviceCategory.DETECTOR,
            device_class="ophyd.sim.SynSignal",
            connection_type=ConnectionType.SIMULATED,
            prefix="ring_current",
            location="Machine",
            tags=["sensor", "machine", "beam"],
            metadata={"units": "mA", "precision": 1},
        )
        ring_info._ophyd_device = ring_current
        self._add_device_internal(ring_info)
        self._ophyd_devices["ring_current"] = ring_current

        # Create additional custom motors for a more complete simulation
        self._create_additional_motors()

        # === Area Detector ===
        try:
            from lightfall.devices.sim.areadetector import SimDetector

            sim_det = SimDetector(
                name="sim_det",
                motors={
                    "x": self._ophyd_devices.get("sample_x"),
                    "y": self._ophyd_devices.get("sample_y"),
                },
            )
            sim_det_info = DeviceInfo(
                name="sim_det",
                description="Simulated area detector for testing",
                category=DeviceCategory.DETECTOR,
                device_class="lightfall.devices.sim.areadetector.SimDetector",
                connection_type=ConnectionType.SIMULATED,
                prefix="sim_det",
                location="Detector Arm",
                tags=["detector", "camera", "area", "simulated"],
                metadata={
                    "size_x": 256,
                    "size_y": 256,
                    "data_type": "uint8",
                },
            )
            sim_det_info._ophyd_device = sim_det
            self._add_device_internal(sim_det_info)
            self._ophyd_devices["sim_det"] = sim_det
        except ImportError:
            logger.warning("SimDetector not available")

        logger.debug("Created {} simulated devices", len(self._devices))

    def _create_additional_motors(self) -> None:
        """Create additional motors for beamline simulation."""
        try:
            from ophyd.sim import SynAxis
        except ImportError:
            return

        # Sample position stages
        sample_x = SynAxis(name="sample_x")
        sample_x_info = DeviceInfo(
            name="sample_x",
            description="Sample X position",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="sample_x",
            location="Sample Stage",
            tags=["motor", "sample", "position"],
            metadata={"units": "um", "precision": 1},
        )
        sample_x_info._ophyd_device = sample_x
        self._add_device_internal(sample_x_info)
        self._ophyd_devices["sample_x"] = sample_x

        sample_y = SynAxis(name="sample_y")
        sample_y_info = DeviceInfo(
            name="sample_y",
            description="Sample Y position",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="sample_y",
            location="Sample Stage",
            tags=["motor", "sample", "position"],
            metadata={"units": "um", "precision": 1},
        )
        sample_y_info._ophyd_device = sample_y
        self._add_device_internal(sample_y_info)
        self._ophyd_devices["sample_y"] = sample_y

        # Slit motors
        slit_gap = SynAxis(name="slit_gap")
        slit_gap_info = DeviceInfo(
            name="slit_gap",
            description="Slit gap size",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="slit_gap",
            location="Optics",
            tags=["motor", "optics", "slit"],
            metadata={"units": "mm", "precision": 3},
        )
        slit_gap_info._ophyd_device = slit_gap
        self._add_device_internal(slit_gap_info)
        self._ophyd_devices["slit_gap"] = slit_gap

        slit_center = SynAxis(name="slit_center")
        slit_center_info = DeviceInfo(
            name="slit_center",
            description="Slit center position",
            category=DeviceCategory.MOTOR,
            device_class="ophyd.sim.SynAxis",
            connection_type=ConnectionType.SIMULATED,
            prefix="slit_center",
            location="Optics",
            tags=["motor", "optics", "slit"],
            metadata={"units": "mm", "precision": 3},
        )
        slit_center_info._ophyd_device = slit_center
        self._add_device_internal(slit_center_info)
        self._ophyd_devices["slit_center"] = slit_center

    def _create_minimal_mock_devices(self) -> None:
        """Create minimal mock devices when ophyd.sim is not available."""
        # Create basic device info entries without ophyd devices
        motor_info = DeviceInfo(
            name="motor",
            description="Primary motor (ophyd.sim not available)",
            category=DeviceCategory.MOTOR,
            device_class="mock",
            connection_type=ConnectionType.SIMULATED,
            prefix="motor",
            tags=["motor", "mock"],
        )
        self._add_device_internal(motor_info)

        det_info = DeviceInfo(
            name="det",
            description="Primary detector (ophyd.sim not available)",
            category=DeviceCategory.DETECTOR,
            device_class="mock",
            connection_type=ConnectionType.SIMULATED,
            prefix="det",
            tags=["detector", "mock"],
        )
        self._add_device_internal(det_info)

    def _add_device_internal(self, device: DeviceInfo) -> None:
        """Internal method to add device to storage."""
        self._devices[device.id] = device
        self._configurations[device.id] = []
        self._maintenance[device.id] = []

        # Create default configuration
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
        """Get a device by connection prefix."""
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
        """Add a new device."""
        if device.id in self._devices:
            return False
        self._add_device_internal(device)
        return True

    def update_device(self, device: DeviceInfo) -> bool:
        """Update an existing device."""
        if device.id not in self._devices:
            return False
        device.modified = datetime.now()
        self._devices[device.id] = device
        return True

    def remove_device(self, device_id: UUID) -> bool:
        """Remove a device."""
        if device_id not in self._devices:
            return False
        del self._devices[device_id]
        self._configurations.pop(device_id, None)
        self._maintenance.pop(device_id, None)
        return True

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

    # === Ophyd Device Access ===

    def get_ophyd_device(self, name: str) -> Any:
        """Get the ophyd device instance by name.

        Args:
            name: Device name.

        Returns:
            The ophyd device or None.
        """
        return self._ophyd_devices.get(name)

    def get_all_ophyd_devices(self) -> dict[str, Any]:
        """Get all ophyd device instances.

        Returns:
            Dictionary mapping name to ophyd device.
        """
        return dict(self._ophyd_devices)

    # === Introspection ===

    def get_backend_info(self) -> dict[str, Any]:
        """Get information about the backend."""
        return {
            "name": self.name,
            "connected": self.is_connected,
            "device_count": len(self._devices),
            "ophyd_device_count": len(self._ophyd_devices),
            "categories": list({d.category.value for d in self._devices.values()}),
            "include_noisy": self._include_noisy,
        }

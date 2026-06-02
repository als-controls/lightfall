"""Generic device wrapper adding NCS-specific behavior to ophyd devices.

The NCSDevice wrapper provides a transparent layer over any ophyd device,
adding metadata injection, permission checking, and action logging while
preserving full access to the underlying device's functionality.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from ophyd import Device, StatusBase

if TYPE_CHECKING:
    from lucid.devices.model import DeviceInfo


class NCSDevice:
    """Generic wrapper for ophyd devices adding NCS metadata and policy.

    NCSDevice wraps any ophyd device (EpicsMotor, AreaDetector, etc.) to add:
    - Metadata injection (project_id, user_id, timestamp)
    - Permission checking before operations
    - Action logging to the logbook
    - Transparent passthrough for all native ophyd operations

    Attributes:
        device: The underlying ophyd device.
        info: DeviceInfo metadata from the catalog.

    Example:
        >>> from lucid.devices import DeviceCatalog
        >>> catalog = DeviceCatalog.get_instance()
        >>> motor_info = catalog.get_device_by_name("sample_x")
        >>> ncs_motor = NCSDevice(motor_info.ophyd_device, motor_info)
        >>> # Use like any ophyd device
        >>> ncs_motor.move(10)  # Logged and permission-checked
        >>> ncs_motor.position  # Direct passthrough
    """

    def __init__(
        self,
        device: Device,
        info: DeviceInfo,
        *,
        enable_logging: bool = True,
        enable_permissions: bool = True,
    ) -> None:
        """Initialize the NCS device wrapper.

        Args:
            device: The ophyd device to wrap.
            info: DeviceInfo metadata from the catalog.
            enable_logging: Whether to log actions to the logbook.
            enable_permissions: Whether to check permissions.
        """
        self._device = device
        self._info = info
        self._enable_logging = enable_logging
        self._enable_permissions = enable_permissions

        # Metadata to inject into runs
        self._project_id: str | None = None
        self._user_id: str | None = None
        self._extra_metadata: dict[str, Any] = {}

    # === Properties ===

    @property
    def device(self) -> Device:
        """Get the underlying ophyd device."""
        return self._device

    @property
    def info(self) -> DeviceInfo:
        """Get the device info metadata."""
        return self._info

    @property
    def name(self) -> str:
        """Get the device name."""
        return self._info.name

    @property
    def category(self) -> str:
        """Get the device category."""
        return self._info.category.value

    # === Metadata Management ===

    def set_project(self, project_id: str) -> None:
        """Set the current project for metadata injection.

        Args:
            project_id: Project identifier.
        """
        self._project_id = project_id

    def set_user(self, user_id: str) -> None:
        """Set the current user for metadata injection.

        Args:
            user_id: User identifier.
        """
        self._user_id = user_id

    def set_metadata(self, **kwargs: Any) -> None:
        """Set additional metadata to inject into runs.

        Args:
            **kwargs: Key-value pairs to add to metadata.
        """
        self._extra_metadata.update(kwargs)

    def get_metadata(self) -> dict[str, Any]:
        """Get the current metadata dictionary.

        Returns:
            Dictionary with all metadata to inject.
        """
        metadata = {
            "ncs_device_name": self._info.name,
            "ncs_device_id": str(self._info.id),
            "ncs_category": self._info.category.value,
            "ncs_timestamp": datetime.now().isoformat(),
        }
        if self._project_id:
            metadata["ncs_project_id"] = self._project_id
        if self._user_id:
            metadata["ncs_user_id"] = self._user_id
        if self._info.beamline:
            metadata["ncs_beamline"] = self._info.beamline
        metadata.update(self._extra_metadata)
        return metadata

    # === Permission Checking ===

    def _check_permission(self, operation: str) -> bool:
        """Check if the current user has permission for an operation.

        Args:
            operation: Operation name (e.g., "read", "set", "move").

        Returns:
            True if permitted.

        Raises:
            PermissionError: If operation is not permitted.
        """
        if not self._enable_permissions:
            return True

        # TODO: Integrate with PolicyEngine when implemented
        # For now, all operations are permitted
        return True

    # === Action Logging ===

    def _log_action(
        self,
        action_type: str,
        old_value: Any = None,
        new_value: Any = None,
        unit: str = "",
    ) -> None:
        """Log an action to the logbook.

        Args:
            action_type: Type of action (e.g., "move", "set").
            old_value: Value before the action.
            new_value: Value after the action.
            unit: Unit string for values.
        """
        if not self._enable_logging:
            return

        try:
            from lucid.logbook import DeviceActionLogger

            action_logger = DeviceActionLogger.get_instance()
            action_logger.record_action(
                device_name=self._info.name,
                action_type=action_type,
                old_value=old_value,
                new_value=new_value,
                unit=unit,
            )
        except ImportError:
            # Logbook module not available
            pass
        except Exception as e:
            logger.warning(f"Failed to log action: {e}")

    def _log_move_start(
        self,
        old_value: Any,
        target_value: Any,
        unit: str = "",
    ) -> None:
        """Log the start of a move operation.

        Args:
            old_value: Current position.
            target_value: Target position.
            unit: Unit string.
        """
        if not self._enable_logging:
            return

        try:
            from lucid.logbook import DeviceActionLogger

            action_logger = DeviceActionLogger.get_instance()
            action_logger.record_move_start(
                device_name=self._info.name,
                old_value=old_value,
                target_value=target_value,
                unit=unit,
            )
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Failed to log move start: {e}")

    # === Ophyd Method Wrappers ===

    def read(self) -> dict[str, Any]:
        """Read the device and log the action.

        Returns:
            Dictionary of readings from the device.
        """
        self._check_permission("read")
        result = self._device.read()
        logger.debug(f"Read {self.name}: {result}")
        return result

    def describe(self) -> dict[str, Any]:
        """Describe the device's data keys.

        Returns:
            Dictionary describing the device's signals.
        """
        return self._device.describe()

    def read_configuration(self) -> dict[str, Any]:
        """Read configuration values.

        Returns:
            Dictionary of configuration values.
        """
        return self._device.read_configuration()

    def describe_configuration(self) -> dict[str, Any]:
        """Describe configuration data keys.

        Returns:
            Dictionary describing configuration signals.
        """
        return self._device.describe_configuration()

    def set(self, value: Any, **kwargs: Any) -> StatusBase:
        """Set the device to a value.

        Args:
            value: Target value.
            **kwargs: Additional arguments for the set operation.

        Returns:
            Status object tracking the operation.
        """
        self._check_permission("set")

        # Get current value for logging
        old_value = None
        if hasattr(self._device, "position"):
            old_value = self._device.position
        elif hasattr(self._device, "get"):
            try:
                old_value = self._device.get()
            except Exception:
                pass

        # Get unit if available
        unit = getattr(self._info.metadata, "unit", "") if self._info.metadata else ""
        if not unit and hasattr(self._device, "egu"):
            unit = self._device.egu or ""

        # Log move start
        self._log_move_start(old_value, value, unit)

        # Perform the set
        status = self._device.set(value, **kwargs)

        # Log completion when done
        def on_complete(status: StatusBase) -> None:
            if status.success:
                actual_value = None
                if hasattr(self._device, "position"):
                    actual_value = self._device.position
                elif hasattr(self._device, "get"):
                    try:
                        actual_value = self._device.get()
                    except Exception:
                        actual_value = value
                self._log_action("set", old_value, actual_value, unit)
            else:
                logger.warning(f"Set operation failed for {self.name}")

        status.add_callback(on_complete)
        return status

    def trigger(self) -> StatusBase:
        """Trigger the device (for detectors).

        Returns:
            Status object tracking the trigger operation.
        """
        self._check_permission("trigger")
        self._log_action("trigger")
        return self._device.trigger()

    def stop(self, *, success: bool = False) -> None:
        """Stop the device.

        Args:
            success: Whether to mark any pending operations as successful.
        """
        self._check_permission("stop")
        self._log_action("stop")
        if hasattr(self._device, "stop"):
            self._device.stop(success=success)

    # === Motor-specific methods ===

    def move(self, position: float, wait: bool = True, **kwargs: Any) -> StatusBase:
        """Move to a position (for positioners).

        Args:
            position: Target position.
            wait: Whether to wait for completion.
            **kwargs: Additional arguments.

        Returns:
            Status object tracking the move.
        """
        if not hasattr(self._device, "move"):
            raise AttributeError(f"{self.name} does not support move()")

        self._check_permission("move")

        old_position = getattr(self._device, "position", None)
        unit = getattr(self._device, "egu", "") or ""

        self._log_move_start(old_position, position, unit)

        status = self._device.move(position, wait=wait, **kwargs)

        if not wait:
            # Add callback for async logging
            def on_complete(status: StatusBase) -> None:
                if status.success:
                    self._log_action("move", old_position, self._device.position, unit)

            status.add_callback(on_complete)
        else:
            # Log immediately for synchronous moves
            self._log_action("move", old_position, self._device.position, unit)

        return status

    @property
    def position(self) -> Any:
        """Get current position (for positioners)."""
        if hasattr(self._device, "position"):
            return self._device.position
        raise AttributeError(f"{self.name} does not have position")

    # === Passthrough ===

    def __getattr__(self, name: str) -> Any:
        """Pass through attribute access to the underlying device.

        This allows transparent access to all ophyd device attributes
        and methods not explicitly wrapped.

        Args:
            name: Attribute name.

        Returns:
            Attribute from the underlying device.
        """
        # Avoid infinite recursion for private attributes
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

        return getattr(self._device, name)

    def __repr__(self) -> str:
        """String representation."""
        return f"NCSDevice({self.name}, category={self.category})"


def wrap_device(device: Device, info: DeviceInfo) -> NCSDevice:
    """Convenience function to wrap an ophyd device.

    Args:
        device: The ophyd device to wrap.
        info: DeviceInfo metadata.

    Returns:
        Wrapped NCSDevice.
    """
    return NCSDevice(device, info)

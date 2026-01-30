"""MCP tools for device interaction via the DeviceCatalog.

Provides tools for Claude to interact with NCS devices:
- List and search devices
- Read device values and state
- Control devices (with permission checks)
"""

from __future__ import annotations

from typing import Any

from lucid.plugins.mcp_tool import MCPToolPlugin
from lucid.utils.logging import logger


class DeviceToolPlugin(MCPToolPlugin):
    """MCP tools for device interaction via the DeviceCatalog.

    This plugin provides tools for Claude to:
    - List and search devices in the catalog
    - Read device values and positions
    - Get device state and status information
    - Control devices (requires DEVICE_CONTROL permission)
    """

    @property
    def name(self) -> str:
        """Plugin name."""
        return "device_tools"

    @property
    def tool_description(self) -> str:
        """Plugin description."""
        return "Tools for interacting with NCS devices"

    def _get_catalog(self):
        """Get the device catalog instance."""
        from lucid.devices import DeviceCatalog

        return DeviceCatalog.get_instance()

    def _get_session_manager(self):
        """Get the session manager instance."""
        from lucid.auth.session import SessionManager

        return SessionManager.get_instance()

    def _check_device_control_permission(self) -> tuple[bool, str | None]:
        """Check if user has DEVICE_CONTROL permission.

        Returns:
            Tuple of (has_permission, error_message).
        """
        from lucid.auth.policy import Permission

        session = self._get_session_manager()
        if not session.check_permission(Permission.DEVICE_CONTROL):
            return False, "Permission denied: DEVICE_CONTROL required"
        return True, None

    def create_tools(self) -> list[Any]:
        """Create device MCP tools.

        Returns:
            List of tool functions.
        """
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, device tools disabled")
            return []

        @tool(
            name="ncs_list_devices",
            description="List devices in the NCS catalog with optional filtering by category, beamline, or search query",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Filter by device category",
                        "enum": [
                            "motor",
                            "detector",
                            "sensor",
                            "controller",
                            "signal",
                            "positioner",
                            "camera",
                            "other",
                        ],
                    },
                    "beamline": {
                        "type": "string",
                        "description": "Filter by beamline name",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search string for name/description/tags",
                    },
                    "active_only": {
                        "type": "boolean",
                        "description": "Only return active devices (default: true)",
                        "default": True,
                    },
                },
            },
        )
        async def list_devices(args: dict) -> dict[str, Any]:
            """List devices with optional filtering."""
            from pyside_claude._internal.threading import run_on_main_thread

            from lucid.devices.model import DeviceCategory

            category_str = args.get("category")
            beamline = args.get("beamline")
            query = args.get("query")
            active_only = args.get("active_only", True)

            def _list():
                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return {
                        "success": False,
                        "error": "Device catalog not connected",
                        "devices": [],
                    }

                # If query is provided, use search
                if query:
                    devices = catalog.search_devices(query)
                else:
                    # Convert category string to enum
                    category = None
                    if category_str:
                        try:
                            category = DeviceCategory(category_str)
                        except ValueError:
                            return {
                                "success": False,
                                "error": f"Invalid category: {category_str}",
                                "devices": [],
                            }

                    devices = catalog.list_devices(
                        category=category,
                        beamline=beamline,
                        active_only=active_only,
                    )

                # Build summary list
                device_list = []
                for device in devices:
                    state = device.state
                    device_list.append(
                        {
                            "id": str(device.id),
                            "name": device.name,
                            "description": device.description,
                            "category": device.category.value,
                            "prefix": device.prefix,
                            "beamline": device.beamline,
                            "status": state.status.value if state else "unknown",
                            "connected": state.connected if state else False,
                            "active": device.active,
                        }
                    )

                return {
                    "success": True,
                    "count": len(device_list),
                    "devices": device_list,
                }

            return run_on_main_thread(_list)

        @tool(
            name="ncs_get_device",
            description="Get detailed information about a device by its name",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The device name",
                    },
                },
                "required": ["name"],
            },
        )
        async def get_device(args: dict) -> dict[str, Any]:
            """Get detailed device information."""
            from pyside_claude._internal.threading import run_on_main_thread

            device_name = args["name"]

            def _get():
                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return {
                        "success": False,
                        "error": "Device catalog not connected",
                        "device": device_name,
                    }

                device = catalog.get_device_by_name(device_name)
                if device is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' not found",
                        "device": device_name,
                    }

                state = device.state
                ophyd_dev = device.ophyd_device

                # Determine capabilities
                capabilities = []
                if ophyd_dev is not None:
                    if hasattr(ophyd_dev, "read"):
                        capabilities.append("read")
                    if hasattr(ophyd_dev, "set"):
                        capabilities.append("set")
                    if hasattr(ophyd_dev, "move"):
                        capabilities.append("move")
                    if hasattr(ophyd_dev, "stop"):
                        capabilities.append("stop")
                    if hasattr(ophyd_dev, "position"):
                        capabilities.append("position")

                return {
                    "success": True,
                    "device": {
                        "id": str(device.id),
                        "name": device.name,
                        "description": device.description,
                        "category": device.category.value,
                        "device_class": device.device_class,
                        "connection_type": device.connection_type.value,
                        "prefix": device.prefix,
                        "beamline": device.beamline,
                        "location": device.location,
                        "tags": device.tags,
                        "active": device.active,
                        "capabilities": capabilities,
                        "state": {
                            "status": state.status.value if state else "unknown",
                            "connected": state.connected if state else False,
                            "position": state.position if state else None,
                            "value": state.value if state else None,
                            "alarm_status": state.alarm_status if state else None,
                            "alarm_severity": state.alarm_severity if state else None,
                        },
                        "metadata": device.metadata,
                    },
                }

            return run_on_main_thread(_get)

        @tool(
            name="ncs_read_device",
            description="Read the current value or position from a device",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The device name",
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Force refresh from hardware (default: false)",
                        "default": False,
                    },
                },
                "required": ["name"],
            },
        )
        async def read_device(args: dict) -> dict[str, Any]:
            """Read current value/position from a device."""
            from pyside_claude._internal.threading import run_on_main_thread

            device_name = args["name"]
            refresh = args.get("refresh", False)

            def _read():
                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return {
                        "success": False,
                        "error": "Device catalog not connected",
                        "device": device_name,
                    }

                device = catalog.get_device_by_name(device_name)
                if device is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' not found",
                        "device": device_name,
                    }

                ophyd_dev = device.ophyd_device
                if ophyd_dev is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' has no hardware connection",
                        "device": device_name,
                    }

                # Refresh state if requested
                if refresh:
                    catalog.refresh_device_state(device.id)

                result = {
                    "success": True,
                    "device": device_name,
                    "readings": {},
                }

                # Get position for positioners
                if hasattr(ophyd_dev, "position"):
                    try:
                        result["position"] = ophyd_dev.position
                    except Exception as e:
                        logger.warning("Failed to read position for {}: {}", device_name, e)

                # Get value for signals
                if hasattr(ophyd_dev, "get"):
                    try:
                        result["value"] = ophyd_dev.get()
                    except Exception as e:
                        logger.warning("Failed to get value for {}: {}", device_name, e)

                # Get full read data
                if hasattr(ophyd_dev, "read"):
                    try:
                        read_data = ophyd_dev.read()
                        # Convert to serializable format
                        for key, val in read_data.items():
                            if isinstance(val, dict):
                                result["readings"][key] = {
                                    "value": val.get("value"),
                                    "timestamp": val.get("timestamp"),
                                }
                            else:
                                result["readings"][key] = val
                    except Exception as e:
                        logger.warning("Failed to read device {}: {}", device_name, e)

                # Include unit if available
                if hasattr(ophyd_dev, "egu"):
                    try:
                        result["unit"] = ophyd_dev.egu
                    except Exception:
                        pass

                # Include state info
                state = device.state
                if state:
                    result["status"] = state.status.value
                    result["connected"] = state.connected

                return result

            return run_on_main_thread(_read)

        @tool(
            name="ncs_get_device_state",
            description="Get the current state of a device including status, alarms, and connection info",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The device name",
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Force refresh from hardware (default: true)",
                        "default": True,
                    },
                },
                "required": ["name"],
            },
        )
        async def get_device_state(args: dict) -> dict[str, Any]:
            """Get device state with status and alarms."""
            from pyside_claude._internal.threading import run_on_main_thread

            device_name = args["name"]
            refresh = args.get("refresh", True)

            def _get_state():
                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return {
                        "success": False,
                        "error": "Device catalog not connected",
                        "device": device_name,
                    }

                device = catalog.get_device_by_name(device_name)
                if device is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' not found",
                        "device": device_name,
                    }

                # Refresh state if requested
                if refresh and device.ophyd_device is not None:
                    catalog.refresh_device_state(device.id)

                state = device.state
                if state is None:
                    return {
                        "success": True,
                        "device": device_name,
                        "state": {
                            "status": "unknown",
                            "connected": False,
                            "position": None,
                            "value": None,
                            "alarm_status": None,
                            "alarm_severity": None,
                            "timestamp": None,
                        },
                    }

                return {
                    "success": True,
                    "device": device_name,
                    "state": {
                        "status": state.status.value,
                        "connected": state.connected,
                        "position": state.position,
                        "value": state.value,
                        "alarm_status": state.alarm_status,
                        "alarm_severity": state.alarm_severity,
                        "timestamp": state.timestamp.isoformat() if state.timestamp else None,
                        "additional": state.additional,
                    },
                }

            return run_on_main_thread(_get_state)

        @tool(
            name="ncs_set_device",
            description="Set a value on a device. Requires DEVICE_CONTROL permission.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The device name",
                    },
                    "value": {
                        "description": "The value to set (number, string, or boolean)",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Wait for the operation to complete (default: true)",
                        "default": True,
                    },
                },
                "required": ["name", "value"],
            },
        )
        async def set_device(args: dict) -> dict[str, Any]:
            """Set a value on a device."""
            from pyside_claude._internal.threading import run_on_main_thread

            device_name = args["name"]
            value = args["value"]
            wait = args.get("wait", True)

            def _set():
                # Check permission
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return {
                        "success": False,
                        "error": error,
                        "device": device_name,
                    }

                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return {
                        "success": False,
                        "error": "Device catalog not connected",
                        "device": device_name,
                    }

                device = catalog.get_device_by_name(device_name)
                if device is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' not found",
                        "device": device_name,
                    }

                ophyd_dev = device.ophyd_device
                if ophyd_dev is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' has no hardware connection",
                        "device": device_name,
                    }

                if not hasattr(ophyd_dev, "set"):
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' does not support set operation",
                        "device": device_name,
                    }

                # Get old value if possible
                old_value = None
                if hasattr(ophyd_dev, "get"):
                    try:
                        old_value = ophyd_dev.get()
                    except Exception:
                        pass

                try:
                    status = ophyd_dev.set(value)
                    if wait and hasattr(status, "wait"):
                        status.wait()

                    # Get new value
                    new_value = None
                    if hasattr(ophyd_dev, "get"):
                        try:
                            new_value = ophyd_dev.get()
                        except Exception:
                            pass

                    logger.info("Device {} set to {} (was {})", device_name, value, old_value)

                    return {
                        "success": True,
                        "device": device_name,
                        "old_value": old_value,
                        "new_value": new_value,
                        "requested_value": value,
                    }

                except Exception as e:
                    logger.error("Failed to set device {}: {}", device_name, e)
                    return {
                        "success": False,
                        "error": f"Set operation failed: {e}",
                        "device": device_name,
                    }

            return run_on_main_thread(_set)

        @tool(
            name="ncs_move_motor",
            description="Move a motor to a specific position. Requires DEVICE_CONTROL permission.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The motor device name",
                    },
                    "position": {
                        "type": "number",
                        "description": "The target position",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Wait for the move to complete (default: true)",
                        "default": True,
                    },
                },
                "required": ["name", "position"],
            },
        )
        async def move_motor(args: dict) -> dict[str, Any]:
            """Move a motor to a position."""
            from pyside_claude._internal.threading import run_on_main_thread

            device_name = args["name"]
            position = args["position"]
            wait = args.get("wait", True)

            def _move():
                # Check permission
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return {
                        "success": False,
                        "error": error,
                        "device": device_name,
                    }

                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return {
                        "success": False,
                        "error": "Device catalog not connected",
                        "device": device_name,
                    }

                device = catalog.get_device_by_name(device_name)
                if device is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' not found",
                        "device": device_name,
                    }

                ophyd_dev = device.ophyd_device
                if ophyd_dev is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' has no hardware connection",
                        "device": device_name,
                    }

                # Check if device supports move
                if not hasattr(ophyd_dev, "move"):
                    # Fall back to set if available
                    if hasattr(ophyd_dev, "set"):
                        try:
                            old_position = None
                            if hasattr(ophyd_dev, "position"):
                                old_position = ophyd_dev.position

                            status = ophyd_dev.set(position)
                            if wait and hasattr(status, "wait"):
                                status.wait()

                            new_position = None
                            if hasattr(ophyd_dev, "position"):
                                new_position = ophyd_dev.position

                            logger.info(
                                "Motor {} moved to {} (was {})",
                                device_name,
                                position,
                                old_position,
                            )

                            result = {
                                "success": True,
                                "device": device_name,
                                "old_position": old_position,
                                "new_position": new_position,
                                "requested_position": position,
                            }

                            # Include unit if available
                            if hasattr(ophyd_dev, "egu"):
                                try:
                                    result["unit"] = ophyd_dev.egu
                                except Exception:
                                    pass

                            return result

                        except Exception as e:
                            logger.error("Failed to move motor {}: {}", device_name, e)
                            return {
                                "success": False,
                                "error": f"Move operation failed: {e}",
                                "device": device_name,
                            }
                    else:
                        return {
                            "success": False,
                            "error": f"Device '{device_name}' does not support move operation",
                            "device": device_name,
                        }

                # Get old position
                old_position = None
                if hasattr(ophyd_dev, "position"):
                    try:
                        old_position = ophyd_dev.position
                    except Exception:
                        pass

                try:
                    status = ophyd_dev.move(position, wait=wait)
                    if wait and hasattr(status, "wait"):
                        status.wait()

                    # Get new position
                    new_position = None
                    if hasattr(ophyd_dev, "position"):
                        try:
                            new_position = ophyd_dev.position
                        except Exception:
                            pass

                    logger.info(
                        "Motor {} moved to {} (was {})",
                        device_name,
                        position,
                        old_position,
                    )

                    result = {
                        "success": True,
                        "device": device_name,
                        "old_position": old_position,
                        "new_position": new_position,
                        "requested_position": position,
                    }

                    # Include unit if available
                    if hasattr(ophyd_dev, "egu"):
                        try:
                            result["unit"] = ophyd_dev.egu
                        except Exception:
                            pass

                    return result

                except Exception as e:
                    logger.error("Failed to move motor {}: {}", device_name, e)
                    return {
                        "success": False,
                        "error": f"Move operation failed: {e}",
                        "device": device_name,
                    }

            return run_on_main_thread(_move)

        @tool(
            name="ncs_stop_device",
            description="Stop a device (emergency stop). Requires DEVICE_CONTROL permission.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The device name to stop",
                    },
                },
                "required": ["name"],
            },
        )
        async def stop_device(args: dict) -> dict[str, Any]:
            """Stop a device."""
            from pyside_claude._internal.threading import run_on_main_thread

            device_name = args["name"]

            def _stop():
                # Check permission
                has_perm, error = self._check_device_control_permission()
                if not has_perm:
                    return {
                        "success": False,
                        "error": error,
                        "device": device_name,
                    }

                catalog = self._get_catalog()

                if not catalog.is_connected:
                    return {
                        "success": False,
                        "error": "Device catalog not connected",
                        "device": device_name,
                    }

                device = catalog.get_device_by_name(device_name)
                if device is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' not found",
                        "device": device_name,
                    }

                ophyd_dev = device.ophyd_device
                if ophyd_dev is None:
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' has no hardware connection",
                        "device": device_name,
                    }

                if not hasattr(ophyd_dev, "stop"):
                    return {
                        "success": False,
                        "error": f"Device '{device_name}' does not support stop operation",
                        "device": device_name,
                    }

                try:
                    ophyd_dev.stop()
                    logger.info("Device {} stopped", device_name)
                    return {
                        "success": True,
                        "device": device_name,
                        "message": f"Device '{device_name}' stopped",
                    }

                except Exception as e:
                    logger.error("Failed to stop device {}: {}", device_name, e)
                    return {
                        "success": False,
                        "error": f"Stop operation failed: {e}",
                        "device": device_name,
                    }

            return run_on_main_thread(_stop)

        return [
            list_devices,
            get_device,
            read_device,
            get_device_state,
            set_device,
            move_motor,
            stop_device,
        ]

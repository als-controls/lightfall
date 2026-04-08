"""Controller widget tool - shows hardware control widgets for PVs."""

from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget


def create_controller_tool(target_window: QWidget):
    """
    Create a tool that shows controller widgets for hardware PVs.

    Args:
        target_window: The main window (used for parenting dialogs)

    Returns:
        show_controller tool function
    """
    from claude_agent_sdk import tool

    # Keep track of open controller windows
    _controller_windows: dict[str, QWidget] = {}

    @tool(
        name="show_controller",
        description="Show a hardware controller widget for a given PV prefix. Currently supports motor records. Example: show_controller('motor1:') to show a motor control panel.",
        input_schema={
            "pv_prefix": {
                "type": "string",
                "description": "The PV prefix for the hardware (e.g., 'IOC:m1' for a motor record)"
            },
            "controller_type": {
                "type": "string",
                "description": "The type of controller to show. Currently supported: 'motor'. If not specified, will attempt to auto-detect (defaults to 'motor' for now).",
                "default": "motor"
            }
        }
    )
    async def show_controller(args: dict) -> dict[str, Any]:
        """
        Show a controller widget for a hardware PV.

        Args:
            args: Dictionary with pv_prefix and optional controller_type

        Returns:
            MCP tool result indicating success or failure
        """
        try:
            pv_prefix = args.get("pv_prefix")
            if not pv_prefix:
                return {
                    "content": [{"type": "text", "text": "pv_prefix parameter is required"}],
                    "is_error": True
                }

            # Normalize prefix (remove trailing colon/dot if present for consistency)
            pv_prefix = pv_prefix.rstrip(":.")

            controller_type = args.get("controller_type", "motor").lower()

            # Check if we already have a controller for this PV
            window_key = f"{controller_type}:{pv_prefix}"
            if window_key in _controller_windows:
                existing = _controller_windows[window_key]
                if existing.isVisible():
                    # Bring to front
                    existing.raise_()
                    existing.activateWindow()
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Controller for '{pv_prefix}' is already open. Brought to front."
                        }]
                    }
                else:
                    # Window was closed, remove from tracking
                    del _controller_windows[window_key]

            # Create the appropriate controller
            if controller_type == "motor":
                widget = _create_motor_controller(pv_prefix)
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Unknown controller type: '{controller_type}'. Supported types: motor"
                    }],
                    "is_error": True
                }

            if widget is None:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Failed to create {controller_type} controller. Make sure epics-pyside is installed."
                    }],
                    "is_error": True
                }

            # Configure and show the widget
            widget.setWindowTitle(f"{controller_type.title()} Controller: {pv_prefix}")
            widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            widget.show()

            # Track the window
            _controller_windows[window_key] = widget

            return {
                "content": [{
                    "type": "text",
                    "text": f"Opened {controller_type} controller for '{pv_prefix}'"
                }]
            }

        except Exception as e:
            import traceback
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error creating controller: {str(e)}\n{traceback.format_exc()}"
                }],
                "is_error": True
            }

    return show_controller


def _create_motor_controller(pv_prefix: str) -> QWidget | None:
    """
    Create a motor controller widget.

    Args:
        pv_prefix: The motor record PV prefix (e.g., 'IOC:m1')

    Returns:
        PVMotor widget instance or None if epics-pyside is not available
    """
    try:
        from lucid.epics.widgets.motor import PVMotor
        return PVMotor(prefix=pv_prefix)
    except ImportError:
        return None

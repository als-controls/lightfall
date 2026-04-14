"""Motor movement mixin for visualization widgets.

Provides right-click context menu functionality for moving motors
to clicked positions in visualization widgets. Integrates with
PyQtGraph's native context menu system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

if TYPE_CHECKING:
    import pyqtgraph as pg

    from lucid.devices.model import DeviceInfo

# TODO: VisualizationMotorMixin currently accesses self._spec (VisualizationSpec)
# to determine motor fields via dim_fields. VisualizationSpec has been removed as
# part of the new widget architecture. This mixin needs to be updated to work with
# the new BaseVisualization-based widgets (which don't have _spec).


class VisualizationMotorMixin:
    """Mixin that adds motor movement capability via right-click context menu.

    This mixin can be added to any visualization widget that has:
    - A _spec attribute (VisualizationSpec)
    - A _plot_widget attribute (pg.PlotWidget)

    The mixin integrates with PyQtGraph's native context menu, adding
    motor movement options alongside the standard View All, Export, etc.

    The mixin provides:
    - Right-click context menu with "Go to X/Y/X,Y" options
    - Motor field detection via dim_fields
    - Motor device lookup via DeviceCatalog
    - Safety checks (scan running, motor connected)
    - Move execution with toast feedback

    Example:
        class MyVisualization(VisualizationMotorMixin, BaseVisualizationWidget):
            def _setup_ui(self):
                # ... setup plot widget ...
                self._setup_motor_context_menu()
    """

    # Session-level preference to skip confirmation dialog
    _skip_move_confirmation: bool = False

    # Instance attributes for tracking mouse position and menu actions
    _last_data_pos: QPointF | None
    _motor_menu_actions: list[QAction]
    _motor_menu: QMenu | None

    def _setup_motor_context_menu(self) -> None:
        """Setup motor movement integration with PyQtGraph context menu.

        Call this in _setup_ui() after creating the plot widget.
        """
        self._last_data_pos = None
        self._motor_menu_actions = []
        self._motor_menu = None

        if not hasattr(self, "_plot_widget") or self._plot_widget is None:
            return

        plot_item = self._plot_widget.getPlotItem()
        if plot_item is None:
            return

        vb = plot_item.vb
        if vb is None:
            return

        # Track mouse position for context menu
        self._plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved_for_menu)

        # Get the ViewBox's context menu and connect to aboutToShow
        viewbox_menu = vb.menu
        if viewbox_menu is not None:
            viewbox_menu.aboutToShow.connect(self._on_viewbox_menu_about_to_show)
            logger.debug(
                "Motor context menu integrated with PyQtGraph for {}",
                type(self).__name__,
            )

    def _on_mouse_moved_for_menu(self, pos: QPointF) -> None:
        """Track mouse position for context menu coordinate lookup.

        Args:
            pos: Mouse position in scene coordinates.
        """
        if not hasattr(self, "_plot_widget") or self._plot_widget is None:
            return

        plot_item = self._plot_widget.getPlotItem()
        if plot_item is None:
            return

        vb = plot_item.vb
        if vb is None:
            return

        # Convert to data coordinates and store
        self._last_data_pos = vb.mapSceneToView(pos)

    def _on_viewbox_menu_about_to_show(self) -> None:
        """Called when PyQtGraph's ViewBox context menu is about to show.

        Adds motor movement actions to the menu dynamically.
        """
        if not hasattr(self, "_plot_widget") or self._plot_widget is None:
            return

        plot_item = self._plot_widget.getPlotItem()
        if plot_item is None:
            return

        vb = plot_item.vb
        if vb is None:
            return

        viewbox_menu = vb.menu
        if viewbox_menu is None:
            return

        # Remove previous motor actions
        self._remove_motor_actions_from_menu(viewbox_menu)

        # Add new motor actions based on current mouse position
        if self._last_data_pos is not None:
            self._add_motor_actions_to_menu(viewbox_menu, self._last_data_pos)

    def _remove_motor_actions_from_menu(self, menu: QMenu) -> None:
        """Remove previously added motor actions from the menu.

        Args:
            menu: The PyQtGraph ViewBox menu.
        """
        for action in self._motor_menu_actions:
            menu.removeAction(action)
        self._motor_menu_actions.clear()

        # Also remove the submenu if it exists
        if self._motor_menu is not None:
            menu.removeAction(self._motor_menu.menuAction())
            self._motor_menu = None

    def _add_motor_actions_to_menu(self, menu: QMenu, data_pos: QPointF) -> None:
        """Add motor movement actions to the PyQtGraph context menu.

        Args:
            menu: The PyQtGraph ViewBox menu.
            data_pos: Clicked position in data coordinates.
        """
        if not hasattr(self, "_spec"):
            return

        spec: VisualizationSpec = self._spec  # type: ignore[assignment]

        x_val = data_pos.x()
        y_val = data_pos.y()

        # Check X field
        x_field = spec.x_field
        x_is_motor = self._is_motor_field(x_field) if x_field else False

        # Check Y field
        y_field = spec.y_field
        y_is_motor = self._is_motor_field(y_field) if y_field else False

        # Debug logging to help diagnose motor detection issues
        logger.info(
            "Motor menu check: x_field='{}', y_field='{}', z_field='{}', "
            "dim_fields={}, x_is_motor={}, y_is_motor={}",
            x_field,
            y_field,
            getattr(spec, "z_field", None),
            spec.characteristics.dim_fields,
            x_is_motor,
            y_is_motor,
        )

        if not x_is_motor and not y_is_motor:
            return

        # Create a submenu for motor actions
        self._motor_menu = QMenu("Move Motor", menu)

        # Add "Go to X" action
        if x_is_motor and x_field:
            can_move, reason = self._can_move_motor(x_field)
            action = QAction(f"Go to {x_field} = {x_val:.4g}", self._motor_menu)
            action.setEnabled(can_move)
            if not can_move:
                action.setToolTip(reason)
            action.triggered.connect(
                lambda checked, f=x_field, v=x_val: self._initiate_motor_move(f, v)
            )
            self._motor_menu.addAction(action)

        # Add "Go to Y" action
        if y_is_motor and y_field:
            can_move, reason = self._can_move_motor(y_field)
            action = QAction(f"Go to {y_field} = {y_val:.4g}", self._motor_menu)
            action.setEnabled(can_move)
            if not can_move:
                action.setToolTip(reason)
            action.triggered.connect(
                lambda checked, f=y_field, v=y_val: self._initiate_motor_move(f, v)
            )
            self._motor_menu.addAction(action)

        # Add "Go to X, Y" action for 2D visualizations
        if x_is_motor and y_is_motor and x_field and y_field:
            self._motor_menu.addSeparator()
            can_move_x, reason_x = self._can_move_motor(x_field)
            can_move_y, reason_y = self._can_move_motor(y_field)
            can_move_both = can_move_x and can_move_y

            action = QAction(
                f"Go to ({x_field}, {y_field}) = ({x_val:.4g}, {y_val:.4g})",
                self._motor_menu,
            )
            action.setEnabled(can_move_both)
            if not can_move_both:
                reasons = []
                if not can_move_x:
                    reasons.append(f"{x_field}: {reason_x}")
                if not can_move_y:
                    reasons.append(f"{y_field}: {reason_y}")
                action.setToolTip("; ".join(reasons))
            action.triggered.connect(
                lambda checked, xf=x_field, xv=x_val, yf=y_field, yv=y_val: self._initiate_motor_move_2d(
                    xf, xv, yf, yv
                )
            )
            self._motor_menu.addAction(action)

        # Add the submenu to the main menu at the top
        if self._motor_menu.actions():
            # Insert separator and submenu at the beginning
            first_action = menu.actions()[0] if menu.actions() else None
            separator = menu.insertSeparator(first_action)
            menu.insertMenu(separator, self._motor_menu)
            self._motor_menu_actions.append(separator)

    def _is_motor_field(self, field_name: str | None) -> bool:
        """Check if a field represents a motor (independent variable).

        A field is considered a motor if it's in the dim_fields
        (independent variables) of the data characteristics.

        Args:
            field_name: Name of the field to check.

        Returns:
            True if the field is a motor/independent variable.
        """
        if not field_name or not hasattr(self, "_spec"):
            return False

        spec: VisualizationSpec = self._spec  # type: ignore[assignment]
        return field_name in spec.characteristics.dim_fields

    def _get_motor_device(self, field_name: str) -> tuple[Any, DeviceInfo] | None:
        """Look up motor device from DeviceCatalog by field name.

        Args:
            field_name: Name of the motor field.

        Returns:
            Tuple of (ophyd_device, DeviceInfo) or None if not found.
        """
        from lucid.devices.catalog import DeviceCatalog
        from lucid.devices.model import DeviceCategory

        catalog = DeviceCatalog.get_instance()
        device_info = catalog.get_device_by_name(field_name)

        if device_info is None:
            logger.debug("Motor '{}' not found in device catalog", field_name)
            return None

        # Verify it's a motor or positioner
        if device_info.category != DeviceCategory.MOTOR:
            logger.debug(
                "Device '{}' is not a motor (category: {})",
                field_name,
                device_info.category,
            )
            return None

        ophyd_device = device_info.ophyd_device
        if ophyd_device is None:
            logger.debug("Motor '{}' has no ophyd device instance", field_name)
            return None

        return (ophyd_device, device_info)

    def _can_move_motor(self, field_name: str) -> tuple[bool, str]:
        """Check if a motor can be moved.

        Checks:
        1. Motor exists in catalog
        2. Motor is connected
        3. No scan is currently running

        Args:
            field_name: Name of the motor field.

        Returns:
            Tuple of (can_move, reason_if_not).
        """
        # Check if motor exists
        motor_info = self._get_motor_device(field_name)
        if motor_info is None:
            return (False, f"Motor '{field_name}' not found in device catalog")

        ophyd_device, device_info = motor_info

        # Check if motor is connected
        state = device_info.state
        if state is not None and not state.connected:
            return (False, f"Motor '{field_name}' is not connected")

        # Check if scan is running
        if self._is_scan_running():
            return (False, "Cannot move motor while scan is running")

        return (True, "")

    def _is_scan_running(self) -> bool:
        """Check if a scan is currently running.

        Returns:
            True if a scan is in progress.
        """
        try:
            from lucid.acquire.engine import EngineState, get_engine

            engine = get_engine()
            return engine.state == EngineState.RUNNING
        except Exception as e:
            logger.debug("Could not check engine state: {}", e)
            return False

    def _initiate_motor_move(self, field_name: str, target: float) -> None:
        """Initiate a single motor move, possibly showing confirmation dialog.

        Args:
            field_name: Name of the motor.
            target: Target position.
        """
        motor_info = self._get_motor_device(field_name)
        if motor_info is None:
            self._show_move_error(field_name, "Motor not found")
            return

        ophyd_device, device_info = motor_info

        # Get current position
        current_pos = None
        if hasattr(ophyd_device, "position"):
            current_pos = ophyd_device.position

        # Get units if available
        units = ""
        field_info = self._spec.characteristics.get_field(field_name)  # type: ignore[union-attr]
        if field_info and field_info.units:
            units = field_info.units

        # Show confirmation dialog unless skipped
        if not VisualizationMotorMixin._skip_move_confirmation:
            from lucid.ui.dialogs.go_to_position_dialog import GoToPositionDialog

            dialog = GoToPositionDialog(
                motor_name=field_name,
                current_position=current_pos,
                target_position=target,
                units=units,
                parent=self if hasattr(self, "window") else None,  # type: ignore[arg-type]
            )

            if not dialog.exec():
                logger.debug("Motor move cancelled by user")
                return

            if dialog.dont_ask_again:
                VisualizationMotorMixin._skip_move_confirmation = True

        # Execute the move
        self._execute_motor_move(ophyd_device, target, device_info)

    def _initiate_motor_move_2d(
        self, x_field: str, x_target: float, y_field: str, y_target: float
    ) -> None:
        """Initiate a 2D motor move (both X and Y).

        Args:
            x_field: Name of the X motor.
            x_target: Target X position.
            y_field: Name of the Y motor.
            y_target: Target Y position.
        """
        x_motor_info = self._get_motor_device(x_field)
        y_motor_info = self._get_motor_device(y_field)

        if x_motor_info is None or y_motor_info is None:
            self._show_move_error(
                f"{x_field}, {y_field}", "One or more motors not found"
            )
            return

        x_ophyd, x_device_info = x_motor_info
        y_ophyd, y_device_info = y_motor_info

        # Get current positions
        x_current = x_ophyd.position if hasattr(x_ophyd, "position") else None
        y_current = y_ophyd.position if hasattr(y_ophyd, "position") else None

        # Show confirmation dialog unless skipped
        if not VisualizationMotorMixin._skip_move_confirmation:
            from lucid.ui.dialogs.go_to_position_dialog import GoToPositionDialog

            dialog = GoToPositionDialog(
                motor_name=f"{x_field}, {y_field}",
                current_position=(x_current, y_current),
                target_position=(x_target, y_target),
                units="",
                is_2d=True,
                motor_names=(x_field, y_field),
                parent=self if hasattr(self, "window") else None,  # type: ignore[arg-type]
            )

            if not dialog.exec():
                logger.debug("2D motor move cancelled by user")
                return

            if dialog.dont_ask_again:
                VisualizationMotorMixin._skip_move_confirmation = True

        # Execute both moves
        self._execute_motor_move(x_ophyd, x_target, x_device_info)
        self._execute_motor_move(y_ophyd, y_target, y_device_info)

    def _execute_motor_move(
        self, motor: Any, target: float, device_info: DeviceInfo
    ) -> None:
        """Execute a motor move with status tracking and toast feedback.

        Args:
            motor: The ophyd motor device.
            target: Target position.
            device_info: Device metadata.
        """
        from lucid.ui.toast import ToastManager

        toast = ToastManager.get_instance()
        motor_name = device_info.name

        try:
            logger.info("Moving {} to {}", motor_name, target)
            toast.info(f"Moving {motor_name}", f"Target: {target:.4g}")

            # Use motor.set() which returns a Status object
            if hasattr(motor, "set"):
                status = motor.set(target)

                # Connect callback for completion
                def on_complete(status: Any = None) -> None:
                    if status is None or status.success:
                        logger.info("{} move complete", motor_name)
                        toast.success(
                            f"{motor_name} move complete",
                            f"Position: {target:.4g}",
                        )
                    else:
                        error_msg = (
                            str(status.exception())
                            if hasattr(status, "exception")
                            else "Unknown error"
                        )
                        logger.error("{} move failed: {}", motor_name, error_msg)
                        toast.error(f"{motor_name} move failed", error_msg)

                # Subscribe to status completion
                if hasattr(status, "add_callback"):
                    status.add_callback(on_complete)
                else:
                    # Fallback for status objects without callbacks
                    on_complete()

            elif hasattr(motor, "move"):
                # Legacy interface - blocking move
                motor.move(target)
                toast.success(
                    f"{motor_name} move complete",
                    f"Position: {target:.4g}",
                )
            else:
                raise AttributeError(
                    f"Motor {motor_name} has no set() or move() method"
                )

        except Exception as e:
            logger.error("Failed to move {}: {}", motor_name, e)
            toast.error(f"Failed to move {motor_name}", str(e))

    def _show_move_error(self, motor_name: str, reason: str) -> None:
        """Show an error toast for a failed motor move attempt.

        Args:
            motor_name: Name of the motor.
            reason: Reason for the failure.
        """
        from lucid.ui.toast import ToastManager

        toast = ToastManager.get_instance()
        toast.error(f"Cannot move {motor_name}", reason)

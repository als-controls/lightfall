"""Editing widgets for the synoptic view.

This module provides:
- SynopticPropertyEditor: Widget for editing device properties
- TransformGizmo: 3D gizmo for interactive positioning
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from pyqtgraph.opengl import GLLinePlotItem

from lucid.ui.panels.synoptic.models import (
    DeviceSynopticData,
    PrimitiveShape,
)

if TYPE_CHECKING:
    from lucid.ui.panels.synoptic.items import DeviceItem


class SynopticPropertyEditor(QWidget):
    """Property editor for synoptic device properties.

    Provides spinboxes for position, rotation, scale, and controls
    for shape and color. Read-only in view mode.

    Signals:
        property_changed: Emitted when any property changes (device_id, property_name, value).
        data_changed: Emitted when synoptic data is fully updated (device_id, DeviceSynopticData).
    """

    property_changed = Signal(str, str, object)  # device_id, property, value
    data_changed = Signal(str, object)  # device_id, DeviceSynopticData

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the property editor.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)

        self._current_device_id: str | None = None
        self._current_data: DeviceSynopticData | None = None
        self._edit_mode = False
        self._updating = False  # Prevent recursive updates

        self._setup_ui()
        self._update_enabled_state()

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header with device name
        self._header_label = QLabel("No Selection")
        self._header_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addWidget(self._header_label)

        # Position group
        pos_group = QGroupBox("Position (m)")
        pos_layout = QFormLayout(pos_group)
        pos_layout.setContentsMargins(4, 4, 4, 4)

        self._pos_x = self._create_spinbox(-100.0, 100.0, 3)
        self._pos_y = self._create_spinbox(-100.0, 100.0, 3)
        self._pos_z = self._create_spinbox(-100.0, 100.0, 3)

        pos_layout.addRow("X:", self._pos_x)
        pos_layout.addRow("Y:", self._pos_y)
        pos_layout.addRow("Z:", self._pos_z)

        layout.addWidget(pos_group)

        # Rotation group
        rot_group = QGroupBox("Rotation (deg)")
        rot_layout = QFormLayout(rot_group)
        rot_layout.setContentsMargins(4, 4, 4, 4)

        self._rot_x = self._create_spinbox(-180.0, 180.0, 1)
        self._rot_y = self._create_spinbox(-180.0, 180.0, 1)
        self._rot_z = self._create_spinbox(-180.0, 180.0, 1)

        rot_layout.addRow("Rx:", self._rot_x)
        rot_layout.addRow("Ry:", self._rot_y)
        rot_layout.addRow("Rz:", self._rot_z)

        layout.addWidget(rot_group)

        # Scale group
        scale_group = QGroupBox("Scale (m)")
        scale_layout = QFormLayout(scale_group)
        scale_layout.setContentsMargins(4, 4, 4, 4)

        self._scale_x = self._create_spinbox(0.01, 10.0, 3)
        self._scale_y = self._create_spinbox(0.01, 10.0, 3)
        self._scale_z = self._create_spinbox(0.01, 10.0, 3)

        scale_layout.addRow("W:", self._scale_x)
        scale_layout.addRow("D:", self._scale_y)
        scale_layout.addRow("H:", self._scale_z)

        layout.addWidget(scale_group)

        # Appearance group
        appear_group = QGroupBox("Appearance")
        appear_layout = QFormLayout(appear_group)
        appear_layout.setContentsMargins(4, 4, 4, 4)

        # Shape dropdown
        self._shape_combo = QComboBox()
        self._shape_combo.addItem("Box", PrimitiveShape.BOX)
        self._shape_combo.addItem("Cylinder", PrimitiveShape.CYLINDER)
        self._shape_combo.addItem("Sphere", PrimitiveShape.SPHERE)
        self._shape_combo.currentIndexChanged.connect(self._on_shape_changed)
        appear_layout.addRow("Shape:", self._shape_combo)

        # Color button
        color_row = QHBoxLayout()
        self._color_button = QPushButton()
        self._color_button.setFixedSize(40, 24)
        self._color_button.clicked.connect(self._on_color_clicked)
        self._color_label = QLabel("(0.5, 0.5, 0.5)")
        color_row.addWidget(self._color_button)
        color_row.addWidget(self._color_label)
        color_row.addStretch()
        appear_layout.addRow("Color:", color_row)

        layout.addWidget(appear_group)

        # Connect spinbox signals
        for spinbox in [
            self._pos_x, self._pos_y, self._pos_z,
            self._rot_x, self._rot_y, self._rot_z,
            self._scale_x, self._scale_y, self._scale_z,
        ]:
            spinbox.valueChanged.connect(self._on_value_changed)

        layout.addStretch()

    def _create_spinbox(
        self,
        min_val: float,
        max_val: float,
        decimals: int,
    ) -> QDoubleSpinBox:
        """Create a configured double spinbox.

        Args:
            min_val: Minimum value.
            max_val: Maximum value.
            decimals: Number of decimal places.

        Returns:
            Configured spinbox.
        """
        spinbox = QDoubleSpinBox()
        spinbox.setRange(min_val, max_val)
        spinbox.setDecimals(decimals)
        spinbox.setSingleStep(0.01 if decimals > 1 else 1.0)
        return spinbox

    def set_edit_mode(self, enabled: bool) -> None:
        """Enable or disable edit mode.

        Args:
            enabled: Whether editing is enabled.
        """
        self._edit_mode = enabled
        self._update_enabled_state()

    def is_edit_mode(self) -> bool:
        """Check if edit mode is enabled."""
        return self._edit_mode

    def _update_enabled_state(self) -> None:
        """Update widget enabled states based on edit mode."""
        editable = self._edit_mode and self._current_device_id is not None
        widgets = [
            self._pos_x, self._pos_y, self._pos_z,
            self._rot_x, self._rot_y, self._rot_z,
            self._scale_x, self._scale_y, self._scale_z,
            self._shape_combo, self._color_button,
        ]
        for w in widgets:
            w.setEnabled(editable)

    def set_device(
        self,
        device_id: str | None,
        device_name: str | None,
        data: DeviceSynopticData | None,
    ) -> None:
        """Set the device to edit.

        Args:
            device_id: Device identifier.
            device_name: Device display name.
            data: Device synoptic data.
        """
        self._current_device_id = device_id
        self._current_data = data

        if device_id is None or data is None:
            self._header_label.setText("No Selection")
            self._clear_values()
        else:
            self._header_label.setText(device_name or device_id)
            self._load_values(data)

        self._update_enabled_state()

    def _clear_values(self) -> None:
        """Clear all input values."""
        self._updating = True
        for spinbox in [
            self._pos_x, self._pos_y, self._pos_z,
            self._rot_x, self._rot_y, self._rot_z,
            self._scale_x, self._scale_y, self._scale_z,
        ]:
            spinbox.setValue(0.0)
        self._shape_combo.setCurrentIndex(0)
        self._color_button.setStyleSheet("")
        self._color_label.setText("")
        self._updating = False

    def _load_values(self, data: DeviceSynopticData) -> None:
        """Load values from synoptic data.

        Args:
            data: Data to load.
        """
        self._updating = True

        # Position
        self._pos_x.setValue(data.position[0])
        self._pos_y.setValue(data.position[1])
        self._pos_z.setValue(data.position[2])

        # Rotation
        self._rot_x.setValue(data.rotation[0])
        self._rot_y.setValue(data.rotation[1])
        self._rot_z.setValue(data.rotation[2])

        # Scale
        self._scale_x.setValue(data.scale[0])
        self._scale_y.setValue(data.scale[1])
        self._scale_z.setValue(data.scale[2])

        # Shape
        shape_index = self._shape_combo.findData(data.primitive_shape)
        if shape_index >= 0:
            self._shape_combo.setCurrentIndex(shape_index)

        # Color
        self._update_color_display(data.color)

        self._updating = False

    def _update_color_display(self, color: tuple[float, float, float, float]) -> None:
        """Update color button and label.

        Args:
            color: RGBA color tuple (0.0-1.0).
        """
        r, g, b, a = color
        qcolor = QColor.fromRgbF(r, g, b, a)
        self._color_button.setStyleSheet(
            f"background-color: {qcolor.name()}; border: 1px solid #666;"
        )
        self._color_label.setText(f"({r:.2f}, {g:.2f}, {b:.2f})")

    def _on_value_changed(self) -> None:
        """Handle spinbox value change."""
        if self._updating or not self._edit_mode:
            return
        if self._current_device_id is None or self._current_data is None:
            return

        # Update data from spinboxes
        self._current_data.position = (
            self._pos_x.value(),
            self._pos_y.value(),
            self._pos_z.value(),
        )
        self._current_data.rotation = (
            self._rot_x.value(),
            self._rot_y.value(),
            self._rot_z.value(),
        )
        self._current_data.scale = (
            self._scale_x.value(),
            self._scale_y.value(),
            self._scale_z.value(),
        )

        self.data_changed.emit(self._current_device_id, self._current_data)

    def _on_shape_changed(self, index: int) -> None:
        """Handle shape selection change."""
        if self._updating or not self._edit_mode:
            return
        if self._current_device_id is None or self._current_data is None:
            return

        shape = self._shape_combo.currentData()
        if shape:
            self._current_data.primitive_shape = shape
            self.property_changed.emit(
                self._current_device_id, "primitive_shape", shape
            )
            self.data_changed.emit(self._current_device_id, self._current_data)

    def _on_color_clicked(self) -> None:
        """Handle color button click."""
        if not self._edit_mode or self._current_data is None:
            return

        # Get current color
        r, g, b, a = self._current_data.color
        initial_color = QColor.fromRgbF(r, g, b, a)

        # Show color dialog
        color = QColorDialog.getColor(
            initial_color,
            self,
            "Select Device Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )

        if color.isValid() and self._current_device_id:
            new_color = (
                color.redF(),
                color.greenF(),
                color.blueF(),
                color.alphaF(),
            )
            self._current_data.color = new_color
            self._update_color_display(new_color)
            self.property_changed.emit(
                self._current_device_id, "color", new_color
            )
            self.data_changed.emit(self._current_device_id, self._current_data)


class TransformGizmo(GLLinePlotItem):
    """3D transform gizmo for interactive device positioning.

    Shows X/Y/Z axis arrows that can be dragged to move devices.
    Only visible in edit mode when a device is selected.

    Signals:
        axis_drag_started: Emitted when axis drag begins (axis: 'x'|'y'|'z').
        axis_dragged: Emitted during drag (axis, delta_value).
        axis_drag_ended: Emitted when drag ends.
    """

    AXIS_LENGTH = 0.3
    AXIS_COLORS = {
        "x": (1.0, 0.2, 0.2, 1.0),  # Red
        "y": (0.2, 1.0, 0.2, 1.0),  # Green
        "z": (0.2, 0.2, 1.0, 1.0),  # Blue
    }

    def __init__(self) -> None:
        """Initialize the transform gizmo."""
        # Initialize position before building gizmo data
        self._position = (0.0, 0.0, 0.0)
        self._visible = False

        # Build axis lines
        pos, colors = self._build_gizmo_data()

        super().__init__(
            pos=pos,
            color=colors,
            width=3.0,
            antialias=True,
            mode="lines",
        )

        self.setVisible(False)

    def _build_gizmo_data(
        self,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build gizmo line data.

        Returns:
            Tuple of (positions, colors) arrays.
        """
        length = self.AXIS_LENGTH
        pos = self._position

        # Each axis is a line from origin to axis direction
        positions = np.array([
            # X axis
            [pos[0], pos[1], pos[2]],
            [pos[0] + length, pos[1], pos[2]],
            # Y axis
            [pos[0], pos[1], pos[2]],
            [pos[0], pos[1] + length, pos[2]],
            # Z axis
            [pos[0], pos[1], pos[2]],
            [pos[0], pos[1], pos[2] + length],
        ], dtype=np.float32)

        colors = np.array([
            self.AXIS_COLORS["x"], self.AXIS_COLORS["x"],
            self.AXIS_COLORS["y"], self.AXIS_COLORS["y"],
            self.AXIS_COLORS["z"], self.AXIS_COLORS["z"],
        ], dtype=np.float32)

        return positions, colors

    def set_position(self, position: tuple[float, float, float]) -> None:
        """Set the gizmo position.

        Args:
            position: New position (X, Y, Z).
        """
        self._position = position
        pos, colors = self._build_gizmo_data()
        self.setData(pos=pos, color=colors)

    def show_at_device(self, device_item: DeviceItem) -> None:
        """Show gizmo at a device's position.

        Args:
            device_item: Device to attach to.
        """
        self.set_position(device_item.get_position())
        self.setVisible(True)
        self._visible = True

    def hide(self) -> None:
        """Hide the gizmo."""
        self.setVisible(False)
        self._visible = False

    def is_visible(self) -> bool:
        """Check if gizmo is visible."""
        return self._visible

    def get_axis_at_ray(
        self,
        ray_origin: np.ndarray,
        ray_direction: np.ndarray,
        threshold: float = 0.05,
    ) -> str | None:
        """Test which axis a ray is closest to.

        Args:
            ray_origin: Ray origin.
            ray_direction: Ray direction.
            threshold: Maximum distance to consider a hit.

        Returns:
            Axis name ('x', 'y', 'z') or None if no hit.
        """
        pos = np.array(self._position)
        length = self.AXIS_LENGTH

        # Axis endpoints
        axes = {
            "x": (pos, pos + np.array([length, 0, 0])),
            "y": (pos, pos + np.array([0, length, 0])),
            "z": (pos, pos + np.array([0, 0, length])),
        }

        closest_axis = None
        closest_distance = threshold

        for axis_name, (start, end) in axes.items():
            distance = self._ray_line_distance(
                ray_origin, ray_direction, start, end
            )
            if distance < closest_distance:
                closest_distance = distance
                closest_axis = axis_name

        return closest_axis

    @staticmethod
    def _ray_line_distance(
        ray_origin: np.ndarray,
        ray_direction: np.ndarray,
        line_start: np.ndarray,
        line_end: np.ndarray,
    ) -> float:
        """Calculate minimum distance between ray and line segment.

        Args:
            ray_origin: Ray origin.
            ray_direction: Ray direction.
            line_start: Line segment start.
            line_end: Line segment end.

        Returns:
            Minimum distance.
        """
        # Vector from ray origin to line start
        w0 = ray_origin - line_start
        # Line direction
        u = ray_direction
        v = line_end - line_start

        a = np.dot(u, u)
        b = np.dot(u, v)
        c = np.dot(v, v)
        d = np.dot(u, w0)
        e = np.dot(v, w0)

        denom = a * c - b * b

        if abs(denom) < 1e-10:
            # Lines are parallel
            return np.linalg.norm(w0 - (np.dot(w0, v) / c) * v)

        s = (b * e - c * d) / denom
        t = (a * e - b * d) / denom

        # Clamp t to [0, 1] for line segment
        t = max(0.0, min(1.0, t))
        # s must be positive (forward along ray)
        s = max(0.0, s)

        # Closest points
        point_on_ray = ray_origin + s * u
        point_on_line = line_start + t * v

        return float(np.linalg.norm(point_on_ray - point_on_line))

    def get_introspection_data(self) -> dict[str, Any]:
        """Get gizmo data for introspection.

        Returns:
            Dictionary with gizmo state.
        """
        return {
            "visible": self._visible,
            "position": self._position,
        }

"""Editing widgets for the synoptic view.

This module provides:
- SynopticPropertyEditor: Widget for editing device properties
- TransformGizmo: 2D gizmo for showing selected device position
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
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

from lightfall.ui.panels.synoptic.models import (
    DeviceSynopticData,
    PrimitiveShape,
    ViewPreset,
)
from lightfall.ui.theme import scaled_pt

if TYPE_CHECKING:
    from lightfall.ui.panels.synoptic.items import Device2DItem


class SynopticPropertyEditor(QWidget):
    """Property editor for synoptic device properties.

    Provides spinboxes for position and scale, and controls
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
        self._header_label.setStyleSheet(f"font-weight: bold; font-size: {scaled_pt(11)}pt;")
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
        self._shape_combo.addItem("Square", PrimitiveShape.SQUARE)
        self._shape_combo.addItem("Circle", PrimitiveShape.CIRCLE)
        self._shape_combo.addItem("Diamond", PrimitiveShape.DIAMOND)
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

        # Scale
        self._scale_x.setValue(data.scale[0])
        self._scale_y.setValue(data.scale[1])
        self._scale_z.setValue(data.scale[2])

        # Shape - normalize legacy values for display
        normalized_shape = PrimitiveShape.normalize(data.primitive_shape)
        shape_index = self._shape_combo.findData(normalized_shape)
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


class TransformGizmo(pg.GraphicsObject):
    """2D transform gizmo for showing selected device position.

    Shows a crosshair marker at the selected device location.
    Only visible in edit mode when a device is selected.

    Note: Uses Qt's item coordinate system - boundingRect() is in local coords
    centered at origin, and setPos() places the item in scene coords.
    """

    GIZMO_SIZE = 0.15  # Size of the crosshair arms in data units (meters)
    GIZMO_COLOR = QColor(255, 200, 0)  # Yellow
    GIZMO_PEN_WIDTH = 2.0  # Pen width in pixels (cosmetic)
    GIZMO_CENTER_RADIUS = 0.02  # Center dot radius in data units (meters)

    def __init__(self) -> None:
        """Initialize the transform gizmo."""
        super().__init__()

        self._position_3d: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._view_preset = ViewPreset.SIDE
        self._visible = False

        self.setVisible(False)

    def _project_position(self) -> tuple[float, float]:
        """Project 3D position to 2D based on view preset.

        Returns:
            2D position (x, y).
        """
        pos = self._position_3d
        preset = self._view_preset

        if preset == ViewPreset.SIDE:
            return (pos[0], pos[2])
        elif preset == ViewPreset.TOP:
            return (pos[0], pos[1])
        elif preset == ViewPreset.FRONT:
            return (pos[1], pos[2])
        else:
            return (pos[0], pos[2])

    def boundingRect(self) -> QRectF:
        """Return the bounding rectangle for this item in local coordinates.

        Returns:
            The bounding rectangle centered at origin.
        """
        # Use GIZMO_SIZE plus small padding for the center dot
        size = self.GIZMO_SIZE + self.GIZMO_CENTER_RADIUS
        return QRectF(-size, -size, size * 2, size * 2)

    def paint(
        self,
        painter: QPainter,
        option: Any,
        widget: Any = None,
    ) -> None:
        """Paint the gizmo crosshair in local coordinates.

        Args:
            painter: The QPainter to use.
            option: Style options.
            widget: The widget being painted on.
        """
        if not self._visible:
            return

        pen = QPen(self.GIZMO_COLOR, self.GIZMO_PEN_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setCosmetic(True)  # Keep width constant when zooming
        painter.setPen(pen)

        size = self.GIZMO_SIZE

        # Draw crosshair at origin (local coordinates)
        # Horizontal line
        painter.drawLine(QPointF(-size, 0), QPointF(size, 0))
        # Vertical line
        painter.drawLine(QPointF(0, -size), QPointF(0, size))

        # Draw small center circle (radius in data units)
        painter.setBrush(QBrush(self.GIZMO_COLOR))
        painter.drawEllipse(QPointF(0, 0), self.GIZMO_CENTER_RADIUS, self.GIZMO_CENTER_RADIUS)

    def _update_scene_position(self) -> None:
        """Update the item's position in the scene."""
        self.prepareGeometryChange()
        pos_2d = self._project_position()
        self.setPos(pos_2d[0], pos_2d[1])

    def set_position(self, position: tuple[float, float, float]) -> None:
        """Set the gizmo position (3D coordinates).

        Args:
            position: New position (X, Y, Z).
        """
        self._position_3d = position
        self._update_scene_position()
        self.update()

    def set_view_preset(self, preset: ViewPreset) -> None:
        """Set the view preset for projection.

        Args:
            preset: The view preset.
        """
        self._view_preset = preset
        self._update_scene_position()
        self.update()

    def show_at_device(self, device_item: Device2DItem) -> None:
        """Show gizmo at a device's position.

        Args:
            device_item: Device to attach to.
        """
        self.set_position(device_item.get_position())
        self.setVisible(True)
        self._visible = True
        self.update()

    def hide(self) -> None:
        """Hide the gizmo."""
        self.setVisible(False)
        self._visible = False

    def is_visible(self) -> bool:
        """Check if gizmo is visible."""
        return self._visible

    def get_introspection_data(self) -> dict[str, Any]:
        """Get gizmo data for introspection.

        Returns:
            Dictionary with gizmo state.
        """
        return {
            "visible": self._visible,
            "position_3d": self._position_3d,
            "position_2d": self._project_position(),
            "view_preset": self._view_preset.value,
        }

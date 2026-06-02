"""2D synoptic view widget using PyQtGraph GraphicsLayoutWidget.

This module provides SynopticView, a 2D view for visualizing beamline
hardware with orthographic projections and mouse interaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lucid.ui.panels.synoptic.models import SynopticViewState, ViewPreset
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.ui.panels.synoptic.items import Device2DItem


class SynopticView(QWidget):
    """2D view widget for beamline synoptic visualization.

    Features:
    - View presets as 2D projections:
      - Side: X-Z plane (beam direction × height)
      - Top: X-Y plane (beam direction × lateral)
      - Front: Y-Z plane (lateral × height)
    - Mouse controls:
      - Left click: device selection (Ctrl+click for multi-select)
      - Left drag (edit mode): move device (via ROI)
      - Right drag: pan
      - Wheel: zoom
    - Keyboard shortcuts: 1-3 for presets, F to frame, G for grid, Esc to deselect

    Device items extend pg.ROI for built-in drag support. When edit mode is
    enabled, devices become draggable and emit device_moved on drag.

    Signals:
        device_clicked: Emitted when a device is clicked (device_id).
        device_double_clicked: Emitted on double-click (device_id).
        selection_changed: Emitted when selection changes (list of device_ids).
        device_moved: Emitted when device is dragged (device_id, new_position).
        view_changed: Emitted when view settings change.
    """

    device_clicked = Signal(str)  # device_id
    device_double_clicked = Signal(str)  # device_id
    selection_changed = Signal(list)  # list[str] of device_ids
    device_moved = Signal(str, tuple)  # device_id, new_position (x, y, z)
    view_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the synoptic view.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)

        # View state
        self._view_preset = ViewPreset.SIDE
        self._grid_visible = True
        self._labels_visible = True
        self._edit_mode = False

        # Device items for picking
        self._device_items: dict[str, Device2DItem] = {}
        self._selected_device_ids: set[str] = set()

        # Setup UI
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create graphics widget with plot
        self._graphics_widget = pg.GraphicsLayoutWidget()
        self._graphics_widget.setBackground(QColor(30, 30, 35))
        layout.addWidget(self._graphics_widget)

        # Create plot item
        self._plot = self._graphics_widget.addPlot()
        self._plot.setAspectLocked(True)
        self._plot.showGrid(x=True, y=True, alpha=0.3)

        # Configure axes labels based on view preset
        self._update_axis_labels()

        # Disable auto-ranging to allow manual zoom
        self._plot.enableAutoRange(enable=False)

        # Set initial view range
        self._plot.setXRange(-2, 2)
        self._plot.setYRange(-1, 1)

        # Connect mouse events for selection
        self._plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)

    def _update_axis_labels(self) -> None:
        """Update axis labels based on current view preset."""
        if self._view_preset == ViewPreset.SIDE:
            self._plot.setLabel("bottom", "X (beam direction)", units="m")
            self._plot.setLabel("left", "Z (height)", units="m")
        elif self._view_preset == ViewPreset.TOP:
            self._plot.setLabel("bottom", "X (beam direction)", units="m")
            self._plot.setLabel("left", "Y (lateral)", units="m")
        elif self._view_preset == ViewPreset.FRONT:
            self._plot.setLabel("bottom", "Y (lateral)", units="m")
            self._plot.setLabel("left", "Z (height)", units="m")

    def apply_view_preset(self, preset: ViewPreset | str) -> None:
        """Apply a predefined view projection.

        Args:
            preset: The view preset to apply (enum or string).
        """
        # Convert string to enum if needed
        if isinstance(preset, str):
            try:
                preset = ViewPreset(preset)
            except ValueError:
                preset = ViewPreset.SIDE

        # Filter out 3D presets - fall back to SIDE
        if preset not in (ViewPreset.SIDE, ViewPreset.TOP, ViewPreset.FRONT):
            preset = ViewPreset.SIDE

        self._view_preset = preset
        self._update_axis_labels()

        # Update all device items with new projection
        for item in self._device_items.values():
            item.set_view_preset(preset)

        self.view_changed.emit()
        logger.debug("Applied view preset: {}", preset.value)

    def get_view_preset(self) -> ViewPreset:
        """Get the current view preset.

        Returns:
            Current view preset.
        """
        return self._view_preset

    def set_grid_visible(self, visible: bool) -> None:
        """Set grid visibility.

        Args:
            visible: Whether the grid should be visible.
        """
        self._grid_visible = visible
        self._plot.showGrid(x=visible, y=visible, alpha=0.3 if visible else 0)

    def is_grid_visible(self) -> bool:
        """Check if grid is visible."""
        return self._grid_visible

    def set_labels_visible(self, visible: bool) -> None:
        """Set device labels visibility.

        Args:
            visible: Whether labels should be visible.
        """
        self._labels_visible = visible
        # TODO: Implement label items

    def is_labels_visible(self) -> bool:
        """Check if labels are visible."""
        return self._labels_visible

    def set_edit_mode(self, enabled: bool) -> None:
        """Enable or disable edit mode.

        In edit mode, devices can be dragged to reposition them.

        Args:
            enabled: Whether edit mode is enabled.
        """
        self._edit_mode = enabled
        # Enable/disable dragging on all devices
        for item in self._device_items.values():
            item.set_movable(enabled)

    def is_edit_mode(self) -> bool:
        """Check if edit mode is enabled."""
        return self._edit_mode

    # Device management

    def add_device_item(self, device_id: str, item: Device2DItem) -> None:
        """Register a device item.

        Args:
            device_id: Unique device identifier.
            item: The Device2DItem to register.
        """
        self._device_items[device_id] = item
        item.set_view_preset(self._view_preset)
        item.set_movable(self._edit_mode)

        # Connect to ROI's region changed signal for drag handling
        item.sigRegionChanged.connect(lambda: self._on_device_dragged(device_id))

        self._plot.addItem(item)

    def _on_device_dragged(self, device_id: str) -> None:
        """Handle device drag via ROI sigRegionChanged.

        Args:
            device_id: ID of device that was dragged.
        """
        item = self._device_items.get(device_id)
        if item:
            new_pos = item.get_current_position_from_roi()
            self.device_moved.emit(device_id, new_pos)

    def remove_device_item(self, device_id: str) -> None:
        """Remove a device item.

        Args:
            device_id: Device identifier to remove.
        """
        item = self._device_items.pop(device_id, None)
        if item:
            self._plot.removeItem(item)
            if device_id in self._selected_device_ids:
                self._selected_device_ids.discard(device_id)
                self.selection_changed.emit(list(self._selected_device_ids))

    def get_device_item(self, device_id: str) -> Device2DItem | None:
        """Get a device item by ID.

        Args:
            device_id: Device identifier.

        Returns:
            The Device2DItem or None if not found.
        """
        return self._device_items.get(device_id)

    def clear_device_items(self) -> None:
        """Remove all device items."""
        for device_id in list(self._device_items.keys()):
            self.remove_device_item(device_id)
        self._selected_device_ids.clear()

    def addItem(self, item: pg.GraphicsObject) -> None:
        """Add a graphics item to the plot.

        This method provides compatibility with the old GLViewWidget API.

        Args:
            item: The graphics item to add.
        """
        self._plot.addItem(item)

    def removeItem(self, item: pg.GraphicsObject) -> None:
        """Remove a graphics item from the plot.

        This method provides compatibility with the old GLViewWidget API.

        Args:
            item: The graphics item to remove.
        """
        self._plot.removeItem(item)

    # Selection

    def select_device(self, device_id: str, add_to_selection: bool = False) -> None:
        """Select a device.

        Args:
            device_id: Device identifier to select.
            add_to_selection: If True, add to existing selection; otherwise replace.
        """
        if not add_to_selection:
            # Deselect all others
            for old_id in self._selected_device_ids:
                if old_id != device_id:
                    item = self._device_items.get(old_id)
                    if item:
                        item.set_selected(False)
            self._selected_device_ids.clear()

        if device_id in self._device_items:
            self._selected_device_ids.add(device_id)
            self._device_items[device_id].set_selected(True)

        self.selection_changed.emit(list(self._selected_device_ids))

    def deselect_device(self, device_id: str) -> None:
        """Deselect a device.

        Args:
            device_id: Device identifier to deselect.
        """
        if device_id in self._selected_device_ids:
            self._selected_device_ids.discard(device_id)
            item = self._device_items.get(device_id)
            if item:
                item.set_selected(False)
            self.selection_changed.emit(list(self._selected_device_ids))

    def clear_selection(self) -> None:
        """Clear all device selection."""
        for device_id in self._selected_device_ids:
            item = self._device_items.get(device_id)
            if item:
                item.set_selected(False)
        self._selected_device_ids.clear()
        self.selection_changed.emit([])

    def get_selected_device_ids(self) -> list[str]:
        """Get list of selected device IDs."""
        return list(self._selected_device_ids)

    # Mouse handling

    def _on_mouse_clicked(self, event) -> None:
        """Handle mouse click for device selection.

        Args:
            event: The mouse click event from pyqtgraph scene.
        """
        # Get click position in scene coordinates
        pos = event.scenePos()

        # Convert to view coordinates
        view_pos = self._plot.vb.mapSceneToView(pos)
        point = QPointF(view_pos.x(), view_pos.y())

        # Check for double-click
        if event.double():
            device_id = self._pick_device_at(point)
            if device_id:
                self.device_double_clicked.emit(device_id)
            return

        # Single click - handle selection
        if event.button() == Qt.MouseButton.LeftButton:
            device_id = self._pick_device_at(point)

            if device_id:
                # Check for Ctrl modifier for multi-select
                modifiers = event.modifiers()
                add_to_selection = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
                self.select_device(device_id, add_to_selection=add_to_selection)
                self.device_clicked.emit(device_id)
            else:
                # Clicked on empty space - clear selection
                self.clear_selection()

    def _pick_device_at(self, point: QPointF) -> str | None:
        """Find device at view coordinates.

        Args:
            point: Point in view coordinates.

        Returns:
            Device ID at position or None.
        """
        # Check all visible devices
        for device_id, item in self._device_items.items():
            if not item.isVisible():
                continue
            if item.contains_point(point):
                return device_id

        return None

    # Keyboard shortcuts

    def keyPressEvent(self, event) -> None:
        """Handle keyboard shortcuts."""
        key = event.key()

        if key == Qt.Key.Key_1:
            self.apply_view_preset(ViewPreset.SIDE)
        elif key == Qt.Key.Key_2:
            self.apply_view_preset(ViewPreset.TOP)
        elif key == Qt.Key.Key_3:
            self.apply_view_preset(ViewPreset.FRONT)
        elif key == Qt.Key.Key_F:
            self._frame_selected()
        elif key == Qt.Key.Key_Escape:
            self.clear_selection()
        elif key == Qt.Key.Key_G:
            self.set_grid_visible(not self.is_grid_visible())
        else:
            super().keyPressEvent(event)

    def _frame_selected(self) -> None:
        """Frame the selected devices in view."""
        if not self._selected_device_ids:
            return

        # Calculate bounding box of selected devices
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")

        for device_id in self._selected_device_ids:
            item = self._device_items.get(device_id)
            if item:
                pos = item.get_projected_position()
                scale = item.get_projected_scale()

                min_x = min(min_x, pos[0] - scale[0] / 2)
                max_x = max(max_x, pos[0] + scale[0] / 2)
                min_y = min(min_y, pos[1] - scale[1] / 2)
                max_y = max(max_y, pos[1] + scale[1] / 2)

        if min_x < float("inf"):
            # Add padding
            padding = 0.2
            self._plot.setXRange(min_x - padding, max_x + padding)
            self._plot.setYRange(min_y - padding, max_y + padding)
            self.view_changed.emit()

    # State save/restore

    def get_view_state(self) -> SynopticViewState:
        """Get current view state for saving.

        Returns:
            Current view state.
        """
        # Get current view range
        view_range = self._plot.viewRange()
        x_range = view_range[0]
        y_range = view_range[1]

        # Calculate center and zoom
        center_x = (x_range[0] + x_range[1]) / 2
        center_y = (y_range[0] + y_range[1]) / 2
        zoom = max(x_range[1] - x_range[0], y_range[1] - y_range[0])

        return SynopticViewState(
            view_preset=self._view_preset,
            view_center=(center_x, center_y),
            zoom_level=zoom,
            labels_visible=self._labels_visible,
            beam_path_visible=True,  # TODO: track beam path visibility
            grid_visible=self._grid_visible,
        )

    def restore_view_state(self, state: SynopticViewState) -> None:
        """Restore view from saved state.

        Args:
            state: View state to restore.
        """
        # Apply preset (filter 3D presets)
        preset = state.view_preset
        # Convert string to enum if needed
        if isinstance(preset, str):
            try:
                preset = ViewPreset(preset)
            except ValueError:
                preset = ViewPreset.SIDE
        if preset not in (ViewPreset.SIDE, ViewPreset.TOP, ViewPreset.FRONT):
            preset = ViewPreset.SIDE
        self._view_preset = preset
        self._update_axis_labels()

        # Restore view range from center and zoom
        if hasattr(state, "view_center") and hasattr(state, "zoom_level"):
            center = state.view_center
            zoom = state.zoom_level
            half_zoom = zoom / 2
            self._plot.setXRange(center[0] - half_zoom, center[0] + half_zoom)
            self._plot.setYRange(center[1] - half_zoom, center[1] + half_zoom)

        # Restore visibility settings
        self._labels_visible = state.labels_visible
        self.set_grid_visible(state.grid_visible)

        # Update all device items with the preset
        for item in self._device_items.values():
            item.set_view_preset(self._view_preset)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get view data for MCP introspection.

        Returns:
            Dictionary with view state and device info.
        """
        view_range = self._plot.viewRange()

        return {
            "view_preset": self._view_preset.value,
            "grid_visible": self._grid_visible,
            "labels_visible": self._labels_visible,
            "device_count": len(self._device_items),
            "selected_devices": list(self._selected_device_ids),
            "view_range_x": view_range[0],
            "view_range_y": view_range[1],
        }

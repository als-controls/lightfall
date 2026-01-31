"""3D synoptic view widget using PyQtGraph GLViewWidget.

This module provides SynopticView, a customized 3D view for
visualizing beamline hardware with orthographic/perspective
camera control and mouse picking.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QVector3D
from PySide6.QtWidgets import QWidget
from pyqtgraph.opengl import GLGridItem, GLViewWidget

from lucid.ui.panels.synoptic.models import SynopticViewState, ViewPreset
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.ui.panels.synoptic.items import DeviceItem


class SynopticView(GLViewWidget):
    """3D view widget for beamline synoptic visualization.

    Features:
    - Orthographic (default) and perspective projection modes
    - View presets (Side, Top, Front, 3D)
    - Mouse picking for device selection
    - Keyboard shortcuts for camera control

    Signals:
        device_clicked: Emitted when a device is clicked (device_id).
        device_double_clicked: Emitted on double-click (device_id).
        selection_changed: Emitted when selection changes (list of device_ids).
        view_changed: Emitted when camera view changes.
    """

    device_clicked = Signal(str)  # device_id
    device_double_clicked = Signal(str)  # device_id
    selection_changed = Signal(list)  # list[str] of device_ids
    view_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the synoptic view.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)

        # Prevent OpenGL context creation from stealing focus on Windows
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # View state
        self._orthographic = True
        self._fov = 60.0
        self._view_preset = ViewPreset.SIDE
        self._initialized = False

        # Device items for picking
        self._device_items: dict[str, DeviceItem] = {}
        self._selected_device_ids: set[str] = set()

        # Grid
        self._grid: GLGridItem | None = None

        # Set background color (safe to call before show)
        self.setBackgroundColor(QColor(30, 30, 35))

        # Enable mouse tracking for hover effects
        self.setMouseTracking(True)

    def showEvent(self, event) -> None:
        """Handle show event - initialize OpenGL items on first show."""
        super().showEvent(event)
        if not self._initialized:
            self._setup_view()
            self._initialized = True

    def _setup_view(self) -> None:
        """Configure initial view settings (called on first show)."""
        # Add grid
        self._grid = GLGridItem()
        self._grid.setSize(10, 10, 1)
        self._grid.setSpacing(0.5, 0.5, 0.5)
        self._grid.setColor((80, 80, 80, 100))
        self.addItem(self._grid)

        # Set initial camera to side view
        self.apply_view_preset(ViewPreset.SIDE)

    def apply_view_preset(self, preset: ViewPreset) -> None:
        """Apply a predefined camera view.

        Args:
            preset: The view preset to apply.
        """
        self._view_preset = preset

        if preset == ViewPreset.SIDE:
            # Side view: looking along Y axis at X-Z plane
            self.setCameraPosition(distance=5.0, elevation=0.0, azimuth=90.0)
            self._orthographic = True
        elif preset == ViewPreset.TOP:
            # Top view: looking down Z axis at X-Y plane
            self.setCameraPosition(distance=5.0, elevation=90.0, azimuth=0.0)
            self._orthographic = True
        elif preset == ViewPreset.FRONT:
            # Front view: looking along X axis at Y-Z plane
            self.setCameraPosition(distance=5.0, elevation=0.0, azimuth=0.0)
            self._orthographic = True
        elif preset == ViewPreset.PERSPECTIVE:
            # 3D perspective view
            self.setCameraPosition(distance=5.0, elevation=30.0, azimuth=45.0)
            self._orthographic = False

        self._update_projection()
        self.view_changed.emit()
        logger.debug("Applied view preset: {}", preset.value)

    def set_orthographic(self, orthographic: bool) -> None:
        """Set orthographic or perspective projection mode.

        Args:
            orthographic: True for orthographic, False for perspective.
        """
        if self._orthographic != orthographic:
            self._orthographic = orthographic
            self._update_projection()
            self.view_changed.emit()

    def is_orthographic(self) -> bool:
        """Check if view is in orthographic mode."""
        return self._orthographic

    def toggle_projection(self) -> None:
        """Toggle between orthographic and perspective projection."""
        self.set_orthographic(not self._orthographic)

    def _update_projection(self) -> None:
        """Update the projection matrix based on current mode."""
        # PyQtGraph's GLViewWidget doesn't have direct ortho support,
        # so we use a very low FOV to approximate orthographic
        if self._orthographic:
            # Use very narrow FOV for pseudo-orthographic
            self.opts["fov"] = 1.0
        else:
            self.opts["fov"] = self._fov

        self.update()

    def set_grid_visible(self, visible: bool) -> None:
        """Set grid visibility.

        Args:
            visible: Whether the grid should be visible.
        """
        if self._grid:
            self._grid.setVisible(visible)

    def is_grid_visible(self) -> bool:
        """Check if grid is visible."""
        return self._grid.visible() if self._grid else False

    # Device management

    def add_device_item(self, device_id: str, item: DeviceItem) -> None:
        """Register a device item for picking.

        Args:
            device_id: Unique device identifier.
            item: The DeviceItem to register.
        """
        self._device_items[device_id] = item
        self.addItem(item)

    def remove_device_item(self, device_id: str) -> None:
        """Remove a device item.

        Args:
            device_id: Device identifier to remove.
        """
        item = self._device_items.pop(device_id, None)
        if item:
            self.removeItem(item)
            if device_id in self._selected_device_ids:
                self._selected_device_ids.discard(device_id)
                self.selection_changed.emit(list(self._selected_device_ids))

    def get_device_item(self, device_id: str) -> DeviceItem | None:
        """Get a device item by ID.

        Args:
            device_id: Device identifier.

        Returns:
            The DeviceItem or None if not found.
        """
        return self._device_items.get(device_id)

    def clear_device_items(self) -> None:
        """Remove all device items."""
        for device_id in list(self._device_items.keys()):
            self.remove_device_item(device_id)
        self._selected_device_ids.clear()

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

    # Mouse picking

    def _pick_device_at(self, pos: QPoint) -> str | None:
        """Find device at screen position using ray casting.

        Args:
            pos: Screen position (widget coordinates).

        Returns:
            Device ID at position or None.
        """
        # Get ray from camera through screen point
        ray_origin, ray_direction = self._get_pick_ray(pos)

        # Find closest device intersection
        closest_device_id = None
        closest_distance = float("inf")

        for device_id, item in self._device_items.items():
            if not item.visible():
                continue

            distance = item.intersect_ray(ray_origin, ray_direction)
            if distance is not None and distance < closest_distance:
                closest_distance = distance
                closest_device_id = device_id

        return closest_device_id

    def _get_pick_ray(
        self, pos: QPoint
    ) -> tuple[np.ndarray, np.ndarray]:
        """Get ray from camera through screen point.

        Args:
            pos: Screen position.

        Returns:
            Tuple of (ray_origin, ray_direction) as numpy arrays.
        """
        # Normalize screen coordinates to [-1, 1]
        width = self.width()
        height = self.height()
        x = (2.0 * pos.x() / width - 1.0)
        y = -(2.0 * pos.y() / height - 1.0)  # Flip Y

        # Get camera parameters
        cam_pos = self.cameraPosition()
        center_raw = self.opts.get("center", QVector3D(0, 0, 0))
        # Handle both QVector3D and tuple
        if isinstance(center_raw, QVector3D):
            center = (center_raw.x(), center_raw.y(), center_raw.z())
        else:
            center = center_raw
        distance = self.opts.get("distance", 5.0)
        elevation = math.radians(self.opts.get("elevation", 0.0))
        azimuth = math.radians(self.opts.get("azimuth", 0.0))

        # Calculate camera position in world coordinates
        cam_x = center[0] + distance * math.cos(elevation) * math.sin(azimuth)
        cam_y = center[1] + distance * math.cos(elevation) * math.cos(azimuth)
        cam_z = center[2] + distance * math.sin(elevation)
        ray_origin = np.array([cam_x, cam_y, cam_z])

        # Calculate view direction
        view_dir = np.array([
            center[0] - cam_x,
            center[1] - cam_y,
            center[2] - cam_z,
        ])
        view_dir = view_dir / np.linalg.norm(view_dir)

        # Calculate right and up vectors
        world_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(view_dir, world_up)
        if np.linalg.norm(right) < 1e-6:
            # View is straight up/down, use different up
            world_up = np.array([0.0, 1.0, 0.0])
            right = np.cross(view_dir, world_up)
        right = right / np.linalg.norm(right)
        up = np.cross(right, view_dir)
        up = up / np.linalg.norm(up)

        # Calculate ray direction
        if self._orthographic:
            # For orthographic, ray is parallel to view direction
            # but origin is offset
            aspect = width / height
            ortho_size = distance * 0.035  # Approximate ortho size
            ray_origin = ray_origin + right * x * ortho_size * aspect + up * y * ortho_size
            ray_direction = view_dir
        else:
            # For perspective, ray goes through screen point
            fov_rad = math.radians(self._fov)
            aspect = width / height
            tan_fov = math.tan(fov_rad / 2)
            ray_direction = view_dir + right * x * tan_fov * aspect + up * y * tan_fov
            ray_direction = ray_direction / np.linalg.norm(ray_direction)

        return ray_origin, ray_direction

    # Mouse events

    def mousePressEvent(self, event) -> None:
        """Handle mouse press for selection."""
        if event.button() == Qt.MouseButton.LeftButton:
            device_id = self._pick_device_at(event.pos())
            if device_id:
                # Check for multi-select
                add_to_selection = event.modifiers() & Qt.KeyboardModifier.ControlModifier
                self.select_device(device_id, add_to_selection=add_to_selection)
                self.device_clicked.emit(device_id)
            else:
                # Click on empty space clears selection
                self.clear_selection()

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Handle mouse double-click for device focus."""
        if event.button() == Qt.MouseButton.LeftButton:
            device_id = self._pick_device_at(event.pos())
            if device_id:
                self.device_double_clicked.emit(device_id)
        super().mouseDoubleClickEvent(event)

    # Keyboard shortcuts

    def keyPressEvent(self, event) -> None:
        """Handle keyboard shortcuts."""
        key = event.key()

        if key == Qt.Key.Key_P:
            self.toggle_projection()
        elif key == Qt.Key.Key_1:
            self.apply_view_preset(ViewPreset.SIDE)
        elif key == Qt.Key.Key_2:
            self.apply_view_preset(ViewPreset.TOP)
        elif key == Qt.Key.Key_3:
            self.apply_view_preset(ViewPreset.FRONT)
        elif key == Qt.Key.Key_4:
            self.apply_view_preset(ViewPreset.PERSPECTIVE)
        elif key == Qt.Key.Key_F:
            self._frame_selected()
        elif key == Qt.Key.Key_Escape:
            self.clear_selection()
        elif key == Qt.Key.Key_G:
            self.set_grid_visible(not self.is_grid_visible())
        else:
            super().keyPressEvent(event)

    def _frame_selected(self) -> None:
        """Frame the selected device in view."""
        if not self._selected_device_ids:
            return

        # Calculate bounding center of selected devices
        positions = []
        for device_id in self._selected_device_ids:
            item = self._device_items.get(device_id)
            if item:
                pos = item.get_position()
                positions.append(pos)

        if positions:
            center = np.mean(positions, axis=0)
            self.opts["center"] = QVector3D(float(center[0]), float(center[1]), float(center[2]))
            self.update()
            self.view_changed.emit()

    # State save/restore

    def get_view_state(self) -> SynopticViewState:
        """Get current view state for saving.

        Returns:
            Current view state.
        """
        cam_pos = self.cameraPosition()
        center = self.opts.get("center", QVector3D(0, 0, 0))
        # Handle both QVector3D and tuple for center
        if isinstance(center, QVector3D):
            center_tuple = (center.x(), center.y(), center.z())
        else:
            center_tuple = tuple(center)

        return SynopticViewState(
            camera_position=tuple(cam_pos),
            camera_target=center_tuple,
            camera_distance=self.opts.get("distance", 5.0),
            camera_elevation=self.opts.get("elevation", 0.0),
            camera_azimuth=self.opts.get("azimuth", 0.0),
            orthographic=self._orthographic,
            fov=self._fov,
            view_preset=self._view_preset,
            labels_visible=True,  # TODO: track labels visibility
            beam_path_visible=True,  # TODO: track beam path visibility
            grid_visible=self.is_grid_visible(),
        )

    def restore_view_state(self, state: SynopticViewState) -> None:
        """Restore view from saved state.

        Args:
            state: View state to restore.
        """
        target = state.camera_target
        self.opts["center"] = QVector3D(float(target[0]), float(target[1]), float(target[2]))
        self.setCameraPosition(
            distance=state.camera_distance,
            elevation=state.camera_elevation,
            azimuth=state.camera_azimuth,
        )
        self._orthographic = state.orthographic
        self._fov = state.fov
        self._view_preset = state.view_preset
        self.set_grid_visible(state.grid_visible)
        self._update_projection()

    def get_introspection_data(self) -> dict[str, Any]:
        """Get view data for MCP introspection.

        Returns:
            Dictionary with view state and device info.
        """
        return {
            "orthographic": self._orthographic,
            "view_preset": self._view_preset.value,
            "grid_visible": self.is_grid_visible(),
            "device_count": len(self._device_items),
            "selected_devices": list(self._selected_device_ids),
            "camera_distance": self.opts.get("distance", 5.0),
            "camera_elevation": self.opts.get("elevation", 0.0),
            "camera_azimuth": self.opts.get("azimuth", 0.0),
        }

"""2D items for synoptic visualization.

This module provides PyQtGraph graphics items for rendering devices and
beam paths in the 2D synoptic view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF

from lightfall.ui.panels.synoptic.models import (
    BeamPathSegment,
    DeviceSynopticData,
    PrimitiveShape,
    ViewPreset,
)

if TYPE_CHECKING:
    pass




class Device2DItem(pg.ROI):
    """2D draggable item representing a device in the synoptic view.

    Extends pg.ROI for built-in drag support. Renders devices as 2D shapes
    (square, circle, diamond) projected from 3D coordinates.

    Signals:
        sigRegionChanged: Emitted when device is dragged (inherited from ROI).

    Note: Position is at bottom-left corner (ROI convention), not center.
    """

    HIGHLIGHT_COLOR = QColor(255, 200, 0)  # Yellow selection highlight
    HIGHLIGHT_WIDTH = 3.0  # Pen width in pixels (cosmetic)
    DEFAULT_EDGE_COLOR = QColor(50, 50, 50)
    DEFAULT_EDGE_WIDTH = 1.0  # Pen width in pixels (cosmetic)

    def __init__(
        self,
        device_id: str,
        device_name: str,
        synoptic_data: DeviceSynopticData,
        view_preset: ViewPreset = ViewPreset.SIDE,
    ) -> None:
        """Initialize a device item.

        Args:
            device_id: Unique device identifier.
            device_name: Device display name.
            synoptic_data: 3D visualization data.
            view_preset: Current view preset for projection.
        """
        # Get initial size for ROI
        self._synoptic_data = synoptic_data
        self._view_preset = view_preset
        scale_2d = self._get_projected_scale()

        # ROI position is bottom-left corner, so offset from center
        pos_2d = self._get_projected_position()
        roi_pos = [pos_2d[0] - scale_2d[0] / 2, pos_2d[1] - scale_2d[1] / 2]

        super().__init__(
            pos=roi_pos,
            size=scale_2d,
            movable=False,  # Disabled by default, enabled in edit mode
            resizable=False,
            rotatable=False,
            removable=False,
        )

        self._device_id = device_id
        self._device_name = device_name
        self._is_selected = False
        self._original_color = synoptic_data.color

        # Disable default ROI handles/hover behavior
        self.handleSize = 0
        self.handlePen = QPen(Qt.PenStyle.NoPen)

    def _get_projected_position(self) -> tuple[float, float]:
        """Get 2D projected center position (internal helper)."""
        pos = self._synoptic_data.position
        return self._project_point(pos)

    def _get_projected_scale(self) -> tuple[float, float]:
        """Get 2D projected scale (internal helper)."""
        scale = self._synoptic_data.scale
        preset = self._view_preset

        if preset == ViewPreset.SIDE:
            return (scale[0], scale[2])
        elif preset == ViewPreset.TOP:
            return (scale[0], scale[1])
        elif preset == ViewPreset.FRONT:
            return (scale[1], scale[2])
        else:
            return (scale[0], scale[2])

    def _update_geometry(self) -> None:
        """Update ROI position and size based on synoptic data."""
        scale_2d = self._get_projected_scale()
        pos_2d = self._get_projected_position()

        # ROI pos is bottom-left, so offset from center
        roi_pos = [pos_2d[0] - scale_2d[0] / 2, pos_2d[1] - scale_2d[1] / 2]

        # Block signals during programmatic update
        self.blockSignals(True)
        self.setPos(roi_pos)
        self.setSize(scale_2d)
        self.blockSignals(False)

    def get_projected_position(self) -> tuple[float, float]:
        """Get the 2D projected center position.

        Returns:
            (x, y) center position in 2D view coordinates.
        """
        return self._get_projected_position()

    def get_projected_scale(self) -> tuple[float, float]:
        """Get the 2D projected scale.

        Returns:
            (width, height) in 2D view coordinates.
        """
        return self._get_projected_scale()

    def _project_point(
        self, point: tuple[float, float, float]
    ) -> tuple[float, float]:
        """Project a 3D point to 2D based on view preset.

        Args:
            point: 3D point (x, y, z).

        Returns:
            2D projected point (x, y).
        """
        preset = self._view_preset

        if preset == ViewPreset.SIDE:
            return (point[0], point[2])
        elif preset == ViewPreset.TOP:
            return (point[0], point[1])
        elif preset == ViewPreset.FRONT:
            return (point[1], point[2])
        else:
            return (point[0], point[2])

    def _unproject_point(
        self, point_2d: tuple[float, float], original_3d: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """Unproject a 2D point back to 3D, preserving the hidden axis.

        Args:
            point_2d: 2D point (x, y) in view coordinates.
            original_3d: Original 3D position to get hidden axis value.

        Returns:
            3D point (x, y, z).
        """
        preset = self._view_preset
        x, y, z = original_3d

        if preset == ViewPreset.SIDE:
            # X-Z plane: 2D x -> 3D x, 2D y -> 3D z
            return (point_2d[0], y, point_2d[1])
        elif preset == ViewPreset.TOP:
            # X-Y plane: 2D x -> 3D x, 2D y -> 3D y
            return (point_2d[0], point_2d[1], z)
        elif preset == ViewPreset.FRONT:
            # Y-Z plane: 2D x -> 3D y, 2D y -> 3D z
            return (x, point_2d[0], point_2d[1])
        else:
            return (point_2d[0], y, point_2d[1])

    def set_view_preset(self, preset: ViewPreset) -> None:
        """Update the view preset and recalculate projection.

        Args:
            preset: New view preset.
        """
        if self._view_preset != preset:
            self._view_preset = preset
            self._update_geometry()
            self.update()

    def paint(
        self,
        painter: QPainter,
        *args,
    ) -> None:
        """Paint the device shape.

        Args:
            painter: The QPainter to use.
            *args: Additional arguments (ignored).
        """
        # Set up colors
        r, g, b, a = self._original_color
        fill_color = QColor.fromRgbF(r, g, b, a)

        if self._is_selected:
            edge_pen = QPen(self.HIGHLIGHT_COLOR, self.HIGHLIGHT_WIDTH)
        else:
            edge_pen = QPen(self.DEFAULT_EDGE_COLOR, self.DEFAULT_EDGE_WIDTH)

        # Use cosmetic pen so line width doesn't scale with zoom
        edge_pen.setCosmetic(True)

        painter.setPen(edge_pen)
        painter.setBrush(QBrush(fill_color))

        # Get local bounds (ROI uses state().size for dimensions)
        state = self.state
        w, h = state["size"]
        bounds = QRectF(0, 0, w, h)

        # Normalize shape (handles legacy values)
        shape = PrimitiveShape.normalize(self._synoptic_data.primitive_shape)

        # Draw shape based on type
        if shape == PrimitiveShape.CIRCLE:
            painter.drawEllipse(bounds)
        elif shape == PrimitiveShape.DIAMOND:
            cx, cy = w / 2, h / 2
            diamond = QPolygonF([
                QPointF(cx, 0),      # Top
                QPointF(w, cy),      # Right
                QPointF(cx, h),      # Bottom
                QPointF(0, cy),      # Left
            ])
            painter.drawPolygon(diamond)
        else:
            # SQUARE is default
            painter.drawRect(bounds)

    def get_device_id(self) -> str:
        """Get the device ID."""
        return self._device_id

    def get_device_name(self) -> str:
        """Get the device display name."""
        return self._device_name

    def get_position(self) -> tuple[float, float, float]:
        """Get the 3D device position."""
        return self._synoptic_data.position

    def get_current_position_from_roi(self) -> tuple[float, float, float]:
        """Get the current 3D position based on ROI state.

        This reflects the actual ROI position, which may differ from
        synoptic_data during dragging.

        Returns:
            Current 3D position.
        """
        state = self.state
        roi_pos = state["pos"]
        size = state["size"]

        # ROI pos is bottom-left, convert to center
        center_2d = (roi_pos[0] + size[0] / 2, roi_pos[1] + size[1] / 2)

        # Unproject to 3D
        return self._unproject_point(center_2d, self._synoptic_data.position)

    def set_position(self, position: tuple[float, float, float]) -> None:
        """Set the 3D device position.

        Args:
            position: New position (X, Y, Z).
        """
        self._synoptic_data.position = position
        self._update_geometry()
        self.update()

    def get_scale(self) -> tuple[float, float, float]:
        """Get the device scale."""
        return self._synoptic_data.scale

    def set_scale(self, scale: tuple[float, float, float]) -> None:
        """Set the device scale.

        Args:
            scale: New scale (X, Y, Z).
        """
        self._synoptic_data.scale = scale
        self._update_geometry()
        self.update()

    def get_synoptic_data(self) -> DeviceSynopticData:
        """Get the synoptic data."""
        return self._synoptic_data

    def set_synoptic_data(self, data: DeviceSynopticData) -> None:
        """Update all synoptic data.

        Args:
            data: New synoptic data.
        """
        self._synoptic_data = data
        self._original_color = data.color
        self._update_geometry()
        self.setVisible(data.visible)
        self.update()

    def set_selected(self, selected: bool) -> None:
        """Set selection state.

        Args:
            selected: Whether the device is selected.
        """
        if self._is_selected != selected:
            self._is_selected = selected
            self.update()

    def is_selected(self) -> bool:
        """Check if device is selected."""
        return self._is_selected

    def set_movable(self, movable: bool) -> None:
        """Enable or disable dragging.

        Args:
            movable: Whether the device can be dragged.
        """
        self.translatable = movable

    def contains_point(self, point: QPointF) -> bool:
        """Check if a point is inside this device's bounds.

        Args:
            point: Point to test in data/view coordinates.

        Returns:
            True if point is inside the device bounds.
        """
        state = self.state
        roi_pos = state["pos"]
        size = state["size"]
        bounds = QRectF(roi_pos[0], roi_pos[1], size[0], size[1])
        return bounds.contains(point)


class BeamPath2DItem(pg.GraphicsObject):
    """2D graphics item representing the beam path.

    Renders beam path as a series of connected line segments,
    projected from 3D to 2D based on the current view preset.
    """

    DEFAULT_COLOR = QColor(255, 0, 0, 128)  # Semi-transparent red
    DEFAULT_WIDTH = 2.0

    def __init__(
        self,
        segments: list[BeamPathSegment] | None = None,
        view_preset: ViewPreset = ViewPreset.SIDE,
    ) -> None:
        """Initialize beam path item.

        Args:
            segments: Initial beam path segments.
            view_preset: Current view preset for projection.
        """
        super().__init__()

        self._segments: list[BeamPathSegment] = segments or []
        self._view_preset = view_preset
        self._bounds: QRectF | None = None
        self._update_bounds()

    def _update_bounds(self) -> None:
        """Update the bounding rectangle from all segments."""
        if not self._segments:
            self._bounds = QRectF(-0.1, -0.1, 0.2, 0.2)
            return

        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")

        for seg in self._segments:
            start_2d = self._project_point(seg.start)
            end_2d = self._project_point(seg.end)

            min_x = min(min_x, start_2d[0], end_2d[0])
            max_x = max(max_x, start_2d[0], end_2d[0])
            min_y = min(min_y, start_2d[1], end_2d[1])
            max_y = max(max_y, start_2d[1], end_2d[1])

        # Add padding for line width
        padding = self.DEFAULT_WIDTH
        self._bounds = QRectF(
            min_x - padding,
            min_y - padding,
            max_x - min_x + 2 * padding,
            max_y - min_y + 2 * padding,
        )
        self.prepareGeometryChange()

    def _project_point(
        self, point: tuple[float, float, float]
    ) -> tuple[float, float]:
        """Project a 3D point to 2D based on view preset.

        Args:
            point: 3D point (x, y, z).

        Returns:
            2D projected point (x, y).
        """
        preset = self._view_preset

        if preset == ViewPreset.SIDE:
            return (point[0], point[2])
        elif preset == ViewPreset.TOP:
            return (point[0], point[1])
        elif preset == ViewPreset.FRONT:
            return (point[1], point[2])
        else:
            return (point[0], point[2])

    def set_view_preset(self, preset: ViewPreset) -> None:
        """Update the view preset and recalculate projection.

        Args:
            preset: New view preset.
        """
        if self._view_preset != preset:
            self._view_preset = preset
            self._update_bounds()
            self.update()

    def boundingRect(self) -> QRectF:
        """Return the bounding rectangle for this item.

        Returns:
            The bounding rectangle.
        """
        if self._bounds is None:
            return QRectF(-0.1, -0.1, 0.2, 0.2)
        return self._bounds

    def paint(
        self,
        painter: QPainter,
        option: Any,
        widget: Any = None,
    ) -> None:
        """Paint the beam path segments.

        Args:
            painter: The QPainter to use.
            option: Style options.
            widget: The widget being painted on.
        """
        if not self._segments:
            return

        for seg in self._segments:
            # Get segment color
            r, g, b, a = seg.color
            color = QColor.fromRgbF(r, g, b, a)

            # Use cosmetic pen with reasonable pixel width
            # seg.width is in meters, scale to reasonable pixel width (2-10 pixels)
            width = max(2.0, min(10.0, seg.width * 200))

            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setCosmetic(True)  # Width in pixels, not scene units
            painter.setPen(pen)

            # Project endpoints
            start_2d = self._project_point(seg.start)
            end_2d = self._project_point(seg.end)

            painter.drawLine(
                QPointF(start_2d[0], start_2d[1]),
                QPointF(end_2d[0], end_2d[1]),
            )

    def set_segments(self, segments: list[BeamPathSegment]) -> None:
        """Update all beam path segments.

        Args:
            segments: New segment list.
        """
        self._segments = segments
        self._update_bounds()
        self.update()

    def add_segment(self, segment: BeamPathSegment) -> None:
        """Add a beam path segment.

        Args:
            segment: Segment to add.
        """
        self._segments.append(segment)
        self._update_bounds()
        self.update()

    def remove_segment(self, segment_id: str) -> bool:
        """Remove a segment by ID.

        Args:
            segment_id: ID of segment to remove.

        Returns:
            True if segment was found and removed.
        """
        for i, seg in enumerate(self._segments):
            if seg.id == segment_id:
                del self._segments[i]
                self._update_bounds()
                self.update()
                return True
        return False

    def clear_segments(self) -> None:
        """Remove all segments."""
        self._segments.clear()
        self._update_bounds()
        self.update()

    def get_segments(self) -> list[BeamPathSegment]:
        """Get all segments."""
        return list(self._segments)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get beam path data for introspection.

        Returns:
            Dictionary with segment info.
        """
        return {
            "segment_count": len(self._segments),
            "segments": [seg.to_dict() for seg in self._segments],
            "visible": self.isVisible(),
            "view_preset": self._view_preset.value,
        }


# Backward-compatible aliases for existing code
DeviceItem = Device2DItem
BeamPathItem = BeamPath2DItem

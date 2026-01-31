"""3D items for synoptic visualization.

This module provides OpenGL items for rendering devices and
beam paths in the synoptic view.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
from pyqtgraph.opengl import (
    GLLinePlotItem,
    GLMeshItem,
    MeshData,
)

from lucid.ui.panels.synoptic.models import (
    BeamPathSegment,
    DeviceSynopticData,
    PrimitiveShape,
)

if TYPE_CHECKING:
    pass


def create_box_mesh(
    size: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> MeshData:
    """Create a box mesh.

    Args:
        size: Box dimensions (width, depth, height).

    Returns:
        MeshData for the box.
    """
    w, d, h = size[0] / 2, size[1] / 2, size[2] / 2

    # 8 vertices of the box
    vertices = np.array([
        [-w, -d, -h], [w, -d, -h], [w, d, -h], [-w, d, -h],  # Bottom
        [-w, -d, h], [w, -d, h], [w, d, h], [-w, d, h],  # Top
    ], dtype=np.float32)

    # 12 triangles (2 per face)
    faces = np.array([
        [0, 1, 2], [0, 2, 3],  # Bottom
        [4, 6, 5], [4, 7, 6],  # Top
        [0, 4, 5], [0, 5, 1],  # Front
        [2, 6, 7], [2, 7, 3],  # Back
        [0, 3, 7], [0, 7, 4],  # Left
        [1, 5, 6], [1, 6, 2],  # Right
    ], dtype=np.uint32)

    return MeshData(vertexes=vertices, faces=faces)


def create_cylinder_mesh(
    radius: float = 0.5,
    height: float = 1.0,
    segments: int = 16,
) -> MeshData:
    """Create a cylinder mesh.

    Args:
        radius: Cylinder radius.
        height: Cylinder height.
        segments: Number of segments around circumference.

    Returns:
        MeshData for the cylinder.
    """
    h = height / 2
    angles = np.linspace(0, 2 * np.pi, segments, endpoint=False)

    # Create vertices: bottom ring, top ring, bottom center, top center
    vertices = []
    for a in angles:
        vertices.append([radius * np.cos(a), radius * np.sin(a), -h])
    for a in angles:
        vertices.append([radius * np.cos(a), radius * np.sin(a), h])
    vertices.append([0, 0, -h])  # Bottom center
    vertices.append([0, 0, h])  # Top center

    vertices = np.array(vertices, dtype=np.float32)
    n = segments
    bottom_center = 2 * n
    top_center = 2 * n + 1

    faces = []
    for i in range(n):
        j = (i + 1) % n
        # Side faces (2 triangles per segment)
        faces.append([i, j, n + j])
        faces.append([i, n + j, n + i])
        # Bottom face
        faces.append([bottom_center, j, i])
        # Top face
        faces.append([top_center, n + i, n + j])

    return MeshData(vertexes=vertices, faces=np.array(faces, dtype=np.uint32))


def create_sphere_mesh(
    radius: float = 0.5,
    rows: int = 8,
    cols: int = 16,
) -> MeshData:
    """Create a sphere mesh.

    Args:
        radius: Sphere radius.
        rows: Number of latitude divisions.
        cols: Number of longitude divisions.

    Returns:
        MeshData for the sphere.
    """
    vertices = []
    faces = []

    # Generate vertices
    for i in range(rows + 1):
        lat = np.pi * i / rows - np.pi / 2
        for j in range(cols):
            lon = 2 * np.pi * j / cols
            x = radius * np.cos(lat) * np.cos(lon)
            y = radius * np.cos(lat) * np.sin(lon)
            z = radius * np.sin(lat)
            vertices.append([x, y, z])

    vertices = np.array(vertices, dtype=np.float32)

    # Generate faces
    for i in range(rows):
        for j in range(cols):
            p1 = i * cols + j
            p2 = i * cols + (j + 1) % cols
            p3 = (i + 1) * cols + (j + 1) % cols
            p4 = (i + 1) * cols + j

            if i > 0:
                faces.append([p1, p2, p4])
            if i < rows - 1:
                faces.append([p2, p3, p4])

    return MeshData(vertexes=vertices, faces=np.array(faces, dtype=np.uint32))


class DeviceItem(GLMeshItem):
    """3D mesh item representing a device in the synoptic view.

    Renders devices as primitive shapes (box, cylinder, sphere) with
    selection highlighting and label support.
    """

    HIGHLIGHT_COLOR = (1.0, 0.8, 0.0, 1.0)  # Yellow selection highlight

    def __init__(
        self,
        device_id: str,
        device_name: str,
        synoptic_data: DeviceSynopticData,
    ) -> None:
        """Initialize a device item.

        Args:
            device_id: Unique device identifier.
            device_name: Device display name.
            synoptic_data: 3D visualization data.
        """
        self._device_id = device_id
        self._device_name = device_name
        self._synoptic_data = synoptic_data
        self._is_selected = False
        self._original_color = synoptic_data.color

        # Create mesh based on shape
        mesh = self._create_mesh()
        color = self._get_face_colors()

        super().__init__(
            meshdata=mesh,
            smooth=True,
            drawFaces=True,
            drawEdges=True,
            edgeColor=(0.2, 0.2, 0.2, 1.0),
        )

        # Set color
        self.setColor(color)

        # Apply transform
        self._apply_transform()

    def _create_mesh(self) -> MeshData:
        """Create mesh data based on shape type.

        Returns:
            MeshData for the device shape.
        """
        shape = self._synoptic_data.primitive_shape
        scale = self._synoptic_data.scale

        if shape == PrimitiveShape.BOX:
            return create_box_mesh(scale)
        elif shape == PrimitiveShape.CYLINDER:
            return create_cylinder_mesh(
                radius=min(scale[0], scale[1]) / 2,
                height=scale[2],
            )
        elif shape == PrimitiveShape.SPHERE:
            return create_sphere_mesh(
                radius=min(scale) / 2,
            )
        else:
            return create_box_mesh(scale)

    def _get_face_colors(self) -> tuple[float, float, float, float]:
        """Get current face color based on selection state.

        Returns:
            RGBA color tuple.
        """
        if self._is_selected:
            return self.HIGHLIGHT_COLOR
        return self._original_color

    def _apply_transform(self) -> None:
        """Apply position and rotation transforms."""
        # Reset transform
        self.resetTransform()

        # Apply translation
        pos = self._synoptic_data.position
        self.translate(pos[0], pos[1], pos[2])

        # Apply rotation (Euler angles in degrees)
        rot = self._synoptic_data.rotation
        self.rotate(rot[0], 1, 0, 0)  # Rx
        self.rotate(rot[1], 0, 1, 0)  # Ry
        self.rotate(rot[2], 0, 0, 1)  # Rz

    def get_device_id(self) -> str:
        """Get the device ID."""
        return self._device_id

    def get_device_name(self) -> str:
        """Get the device display name."""
        return self._device_name

    def get_position(self) -> tuple[float, float, float]:
        """Get the device position."""
        return self._synoptic_data.position

    def set_position(self, position: tuple[float, float, float]) -> None:
        """Set the device position.

        Args:
            position: New position (X, Y, Z).
        """
        self._synoptic_data.position = position
        self._apply_transform()

    def get_rotation(self) -> tuple[float, float, float]:
        """Get the device rotation."""
        return self._synoptic_data.rotation

    def set_rotation(self, rotation: tuple[float, float, float]) -> None:
        """Set the device rotation.

        Args:
            rotation: New rotation (Rx, Ry, Rz) in degrees.
        """
        self._synoptic_data.rotation = rotation
        self._apply_transform()

    def get_scale(self) -> tuple[float, float, float]:
        """Get the device scale."""
        return self._synoptic_data.scale

    def set_scale(self, scale: tuple[float, float, float]) -> None:
        """Set the device scale (requires mesh rebuild).

        Args:
            scale: New scale (X, Y, Z).
        """
        self._synoptic_data.scale = scale
        # Rebuild mesh with new scale
        mesh = self._create_mesh()
        self.setMeshData(meshdata=mesh)
        self._apply_transform()

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
        mesh = self._create_mesh()
        self.setMeshData(meshdata=mesh)
        self.setColor(self._get_face_colors())
        self._apply_transform()
        self.setVisible(data.visible)

    def set_selected(self, selected: bool) -> None:
        """Set selection state.

        Args:
            selected: Whether the device is selected.
        """
        if self._is_selected != selected:
            self._is_selected = selected
            self.setColor(self._get_face_colors())

    def is_selected(self) -> bool:
        """Check if device is selected."""
        return self._is_selected

    def intersect_ray(
        self,
        ray_origin: np.ndarray,
        ray_direction: np.ndarray,
    ) -> float | None:
        """Test ray intersection with this device's bounding box.

        Args:
            ray_origin: Ray origin point.
            ray_direction: Ray direction vector.

        Returns:
            Distance to intersection or None if no hit.
        """
        # Use axis-aligned bounding box for intersection test
        pos = np.array(self._synoptic_data.position)
        scale = np.array(self._synoptic_data.scale) / 2

        # Bounding box min/max
        bb_min = pos - scale
        bb_max = pos + scale

        return self._ray_box_intersection(
            ray_origin, ray_direction, bb_min, bb_max
        )

    @staticmethod
    def _ray_box_intersection(
        ray_origin: np.ndarray,
        ray_direction: np.ndarray,
        bb_min: np.ndarray,
        bb_max: np.ndarray,
    ) -> float | None:
        """Ray-AABB intersection test.

        Args:
            ray_origin: Ray origin.
            ray_direction: Ray direction (should be normalized).
            bb_min: Bounding box minimum corner.
            bb_max: Bounding box maximum corner.

        Returns:
            Distance to intersection or None.
        """
        # Avoid division by zero
        inv_dir = np.where(
            np.abs(ray_direction) > 1e-10,
            1.0 / ray_direction,
            np.sign(ray_direction) * 1e10,
        )

        t1 = (bb_min - ray_origin) * inv_dir
        t2 = (bb_max - ray_origin) * inv_dir

        t_min = np.minimum(t1, t2)
        t_max = np.maximum(t1, t2)

        t_enter = np.max(t_min)
        t_exit = np.min(t_max)

        if t_enter > t_exit or t_exit < 0:
            return None

        return t_enter if t_enter > 0 else t_exit


class BeamPathItem(GLLinePlotItem):
    """3D line item representing the beam path.

    Renders beam path as a series of connected line segments.
    """

    def __init__(
        self,
        segments: list[BeamPathSegment] | None = None,
    ) -> None:
        """Initialize beam path item.

        Args:
            segments: Initial beam path segments.
        """
        self._segments: list[BeamPathSegment] = segments or []

        # Build line data
        pos, color, width = self._build_line_data()

        super().__init__(
            pos=pos,
            color=color,
            width=width,
            antialias=True,
            mode="lines",
        )

    def _build_line_data(
        self,
    ) -> tuple[np.ndarray, np.ndarray, float]:
        """Build line vertex and color arrays from segments.

        Returns:
            Tuple of (positions, colors, width).
        """
        if not self._segments:
            # Return empty arrays (not None) to avoid paint errors
            empty_pos = np.zeros((0, 3), dtype=np.float32)
            empty_color = np.zeros((0, 4), dtype=np.float32)
            return empty_pos, empty_color, 1.0

        positions = []
        colors = []

        for seg in self._segments:
            positions.append(seg.start)
            positions.append(seg.end)
            colors.append(seg.color)
            colors.append(seg.color)

        pos_array = np.array(positions, dtype=np.float32)
        color_array = np.array(colors, dtype=np.float32)

        # Use average width
        avg_width = sum(s.width for s in self._segments) / len(self._segments)

        return pos_array, color_array, avg_width * 100  # Scale width for visibility

    def set_segments(self, segments: list[BeamPathSegment]) -> None:
        """Update all beam path segments.

        Args:
            segments: New segment list.
        """
        self._segments = segments
        pos, color, width = self._build_line_data()
        self.setData(pos=pos, color=color, width=width)

    def add_segment(self, segment: BeamPathSegment) -> None:
        """Add a beam path segment.

        Args:
            segment: Segment to add.
        """
        self._segments.append(segment)
        pos, color, width = self._build_line_data()
        self.setData(pos=pos, color=color, width=width)

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
                pos, color, width = self._build_line_data()
                self.setData(pos=pos, color=color, width=width)
                return True
        return False

    def clear_segments(self) -> None:
        """Remove all segments."""
        self._segments.clear()
        # Use empty arrays to avoid paint errors
        empty_pos = np.zeros((0, 3), dtype=np.float32)
        empty_color = np.zeros((0, 4), dtype=np.float32)
        self.setData(pos=empty_pos, color=empty_color)

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
            "visible": self.visible(),
        }

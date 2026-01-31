"""Data models for synoptic 3D visualization.

This module provides dataclasses for:
- DeviceSynopticData: 3D position, rotation, scale, and appearance for devices
- BeamPathSegment: Line segments representing the beam path
- SynopticViewState: Camera and view settings (saved locally)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PrimitiveShape(str, Enum):
    """Available 3D primitive shapes for device representation."""

    BOX = "box"
    CYLINDER = "cylinder"
    SPHERE = "sphere"


class ViewPreset(str, Enum):
    """Predefined camera view presets."""

    SIDE = "side"  # X-Z plane, looking along Y
    TOP = "top"  # X-Y plane, looking down Z
    FRONT = "front"  # Y-Z plane, looking along X
    PERSPECTIVE = "perspective"  # 3D perspective view


# Default shapes by device category
DEFAULT_SHAPES: dict[str, PrimitiveShape] = {
    "motor": PrimitiveShape.CYLINDER,
    "detector": PrimitiveShape.BOX,
    "sensor": PrimitiveShape.SPHERE,
    "positioner": PrimitiveShape.CYLINDER,
    "camera": PrimitiveShape.BOX,
    "signal": PrimitiveShape.SPHERE,
    "controller": PrimitiveShape.BOX,
    "other": PrimitiveShape.BOX,
}

# Default colors by device category (RGBA)
DEFAULT_COLORS: dict[str, tuple[float, float, float, float]] = {
    "motor": (0.3, 0.5, 0.8, 1.0),  # Blue
    "detector": (0.8, 0.3, 0.3, 1.0),  # Red
    "sensor": (0.3, 0.8, 0.3, 1.0),  # Green
    "positioner": (0.5, 0.3, 0.8, 1.0),  # Purple
    "camera": (0.8, 0.6, 0.2, 1.0),  # Orange
    "signal": (0.6, 0.6, 0.6, 1.0),  # Gray
    "controller": (0.4, 0.4, 0.4, 1.0),  # Dark gray
    "other": (0.5, 0.5, 0.5, 1.0),  # Medium gray
}


@dataclass
class DeviceSynopticData:
    """3D visualization data for a device.

    This is stored in DeviceInfo.metadata["synoptic"] to persist
    device positions and appearance in the 3D synoptic view.

    Attributes:
        position: X, Y, Z position in meters (beamline coordinates).
        rotation: Euler angles in degrees (Rx, Ry, Rz).
        scale: Scale factors for X, Y, Z dimensions.
        representation_type: Type of 3D representation ("primitive", future: "sprite", "mesh").
        primitive_shape: Shape for primitive representation.
        color: RGBA color tuple (0.0-1.0 range).
        label_text: Optional label text (defaults to device name if None).
        label_offset: Position offset for the label relative to device.
        visible: Whether the device is visible in the synoptic view.
    """

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (0.1, 0.1, 0.1)
    representation_type: str = "primitive"
    primitive_shape: PrimitiveShape = PrimitiveShape.BOX
    color: tuple[float, float, float, float] = (0.5, 0.5, 0.5, 1.0)
    label_text: str | None = None
    label_offset: tuple[float, float, float] = (0.0, 0.15, 0.0)
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage.

        Returns:
            Dictionary representation for JSON storage.
        """
        return {
            "position": list(self.position),
            "rotation": list(self.rotation),
            "scale": list(self.scale),
            "representation_type": self.representation_type,
            "primitive_shape": self.primitive_shape.value,
            "color": list(self.color),
            "label_text": self.label_text,
            "label_offset": list(self.label_offset),
            "visible": self.visible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceSynopticData:
        """Deserialize from dictionary.

        Args:
            data: Dictionary from storage.

        Returns:
            New DeviceSynopticData instance.
        """
        return cls(
            position=tuple(data.get("position", [0.0, 0.0, 0.0])),
            rotation=tuple(data.get("rotation", [0.0, 0.0, 0.0])),
            scale=tuple(data.get("scale", [0.1, 0.1, 0.1])),
            representation_type=data.get("representation_type", "primitive"),
            primitive_shape=PrimitiveShape(data.get("primitive_shape", "box")),
            color=tuple(data.get("color", [0.5, 0.5, 0.5, 1.0])),
            label_text=data.get("label_text"),
            label_offset=tuple(data.get("label_offset", [0.0, 0.15, 0.0])),
            visible=data.get("visible", True),
        )

    @classmethod
    def default_for_category(cls, category: str) -> DeviceSynopticData:
        """Create default synoptic data based on device category.

        Args:
            category: Device category string (e.g., "motor", "detector").

        Returns:
            New DeviceSynopticData with category-appropriate defaults.
        """
        category_lower = category.lower()
        return cls(
            primitive_shape=DEFAULT_SHAPES.get(category_lower, PrimitiveShape.BOX),
            color=DEFAULT_COLORS.get(category_lower, (0.5, 0.5, 0.5, 1.0)),
        )


@dataclass
class BeamPathSegment:
    """A segment of the beam path.

    Beam path is visualized as connected line segments showing
    the nominal beam trajectory through the beamline.

    Attributes:
        start: Starting point (X, Y, Z) in meters.
        end: Ending point (X, Y, Z) in meters.
        color: RGBA color tuple (default: semi-transparent red).
        width: Line width in scene units.
        id: Optional identifier for this segment.
    """

    start: tuple[float, float, float]
    end: tuple[float, float, float]
    color: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.5)
    width: float = 0.02
    id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage.

        Returns:
            Dictionary representation.
        """
        return {
            "start": list(self.start),
            "end": list(self.end),
            "color": list(self.color),
            "width": self.width,
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BeamPathSegment:
        """Deserialize from dictionary.

        Args:
            data: Dictionary from storage.

        Returns:
            New BeamPathSegment instance.
        """
        return cls(
            start=tuple(data["start"]),
            end=tuple(data["end"]),
            color=tuple(data.get("color", [1.0, 0.0, 0.0, 0.5])),
            width=data.get("width", 0.02),
            id=data.get("id"),
        )


@dataclass
class SynopticViewState:
    """Camera and view settings for the synoptic view.

    This is saved locally per-user via PreferencesManager,
    keyed by beamline ID.

    Attributes:
        camera_position: Camera position in world coordinates.
        camera_target: Point the camera is looking at.
        camera_distance: Distance from camera to target.
        camera_elevation: Elevation angle in degrees.
        camera_azimuth: Azimuth angle in degrees.
        orthographic: Whether to use orthographic projection.
        fov: Field of view in degrees (for perspective mode).
        view_preset: Last used view preset.
        hidden_devices: Set of device IDs that are hidden.
        labels_visible: Whether device labels are shown.
        beam_path_visible: Whether beam path is shown.
        grid_visible: Whether floor grid is shown.
    """

    camera_position: tuple[float, float, float] = (0.0, 5.0, 0.0)
    camera_target: tuple[float, float, float] = (0.0, 0.0, 0.0)
    camera_distance: float = 5.0
    camera_elevation: float = 0.0
    camera_azimuth: float = 90.0
    orthographic: bool = True
    fov: float = 60.0
    view_preset: ViewPreset = ViewPreset.SIDE
    hidden_devices: set[str] = field(default_factory=set)
    labels_visible: bool = True
    beam_path_visible: bool = True
    grid_visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage.

        Returns:
            Dictionary representation.
        """
        return {
            "camera_position": list(self.camera_position),
            "camera_target": list(self.camera_target),
            "camera_distance": self.camera_distance,
            "camera_elevation": self.camera_elevation,
            "camera_azimuth": self.camera_azimuth,
            "orthographic": self.orthographic,
            "fov": self.fov,
            "view_preset": self.view_preset.value,
            "hidden_devices": list(self.hidden_devices),
            "labels_visible": self.labels_visible,
            "beam_path_visible": self.beam_path_visible,
            "grid_visible": self.grid_visible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynopticViewState:
        """Deserialize from dictionary.

        Args:
            data: Dictionary from storage.

        Returns:
            New SynopticViewState instance.
        """
        return cls(
            camera_position=tuple(data.get("camera_position", [0.0, 5.0, 0.0])),
            camera_target=tuple(data.get("camera_target", [0.0, 0.0, 0.0])),
            camera_distance=data.get("camera_distance", 5.0),
            camera_elevation=data.get("camera_elevation", 0.0),
            camera_azimuth=data.get("camera_azimuth", 90.0),
            orthographic=data.get("orthographic", True),
            fov=data.get("fov", 60.0),
            view_preset=ViewPreset(data.get("view_preset", "side")),
            hidden_devices=set(data.get("hidden_devices", [])),
            labels_visible=data.get("labels_visible", True),
            beam_path_visible=data.get("beam_path_visible", True),
            grid_visible=data.get("grid_visible", True),
        )

    @classmethod
    def for_preset(cls, preset: ViewPreset) -> SynopticViewState:
        """Create view state for a specific preset.

        Args:
            preset: The view preset to use.

        Returns:
            New SynopticViewState configured for the preset.
        """
        presets = {
            ViewPreset.SIDE: cls(
                camera_elevation=0.0,
                camera_azimuth=90.0,
                orthographic=True,
                view_preset=ViewPreset.SIDE,
            ),
            ViewPreset.TOP: cls(
                camera_elevation=90.0,
                camera_azimuth=0.0,
                orthographic=True,
                view_preset=ViewPreset.TOP,
            ),
            ViewPreset.FRONT: cls(
                camera_elevation=0.0,
                camera_azimuth=0.0,
                orthographic=True,
                view_preset=ViewPreset.FRONT,
            ),
            ViewPreset.PERSPECTIVE: cls(
                camera_elevation=30.0,
                camera_azimuth=45.0,
                orthographic=False,
                view_preset=ViewPreset.PERSPECTIVE,
            ),
        }
        return presets.get(preset, presets[ViewPreset.SIDE])

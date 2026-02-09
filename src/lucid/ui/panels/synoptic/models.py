"""Data models for synoptic 2D visualization.

This module provides dataclasses for:
- DeviceSynopticData: Position, scale, and appearance for devices
- BeamPathSegment: Line segments representing the beam path
- SynopticViewState: View settings (saved locally)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PrimitiveShape(str, Enum):
    """Available 2D shapes for device representation."""

    SQUARE = "square"
    CIRCLE = "circle"
    DIAMOND = "diamond"

    # Legacy values for backward compatibility with saved data
    BOX = "box"
    CYLINDER = "cylinder"
    SPHERE = "sphere"

    @classmethod
    def normalize(cls, shape: PrimitiveShape) -> PrimitiveShape:
        """Normalize legacy 3D shapes to current 2D shapes.

        Args:
            shape: Shape to normalize.

        Returns:
            Normalized 2D shape.
        """
        legacy_map = {
            cls.BOX: cls.SQUARE,
            cls.CYLINDER: cls.CIRCLE,
            cls.SPHERE: cls.CIRCLE,
        }
        return legacy_map.get(shape, shape)


class ViewPreset(str, Enum):
    """Predefined 2D projection presets.

    Each preset defines which plane of the 3D coordinate system
    is displayed in the 2D view.
    """

    SIDE = "side"  # X-Z plane (beam direction × height)
    TOP = "top"  # X-Y plane (beam direction × lateral)
    FRONT = "front"  # Y-Z plane (lateral × height)

    # Legacy presets for backward compatibility - map to SIDE
    ORTHO3D = "ortho3d"  # Deprecated: use SIDE
    PERSPECTIVE = "perspective"  # Deprecated: use SIDE


# Default shapes by device category
DEFAULT_SHAPES: dict[str, PrimitiveShape] = {
    "motor": PrimitiveShape.CIRCLE,
    "detector": PrimitiveShape.SQUARE,
    "sensor": PrimitiveShape.DIAMOND,
    "positioner": PrimitiveShape.CIRCLE,
    "camera": PrimitiveShape.SQUARE,
    "signal": PrimitiveShape.DIAMOND,
    "controller": PrimitiveShape.SQUARE,
    "other": PrimitiveShape.SQUARE,
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
    """2D visualization data for a device.

    This is stored in DeviceInfo.metadata["synoptic"] to persist
    device positions and appearance in the synoptic view.

    Attributes:
        position: X, Y, Z position in meters (beamline coordinates).
        scale: Scale factors for X, Y, Z dimensions.
        primitive_shape: 2D shape (square, circle, diamond).
        color: RGBA color tuple (0.0-1.0 range).
        label_text: Optional label text (defaults to device name if None).
        label_offset: Position offset for the label relative to device.
        visible: Whether the device is visible in the synoptic view.
    """

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (0.1, 0.1, 0.1)
    primitive_shape: PrimitiveShape = PrimitiveShape.SQUARE
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
            "scale": list(self.scale),
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
        # Handle legacy shape values
        shape_str = data.get("primitive_shape", "square")
        try:
            shape = PrimitiveShape(shape_str)
            shape = PrimitiveShape.normalize(shape)
        except ValueError:
            shape = PrimitiveShape.SQUARE

        return cls(
            position=tuple(data.get("position", [0.0, 0.0, 0.0])),
            scale=tuple(data.get("scale", [0.1, 0.1, 0.1])),
            primitive_shape=shape,
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
            primitive_shape=DEFAULT_SHAPES.get(category_lower, PrimitiveShape.SQUARE),
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
    """View settings for the 2D synoptic view.

    This is saved locally per-user via PreferencesManager,
    keyed by beamline ID.

    Attributes:
        view_preset: Current 2D projection preset (Side/Top/Front).
        view_center: Center point of the 2D view (x, y).
        zoom_level: Zoom level (width of visible area).
        hidden_devices: Set of device IDs that are hidden.
        labels_visible: Whether device labels are shown.
        beam_path_visible: Whether beam path is shown.
        grid_visible: Whether grid is shown.
    """

    view_preset: ViewPreset = ViewPreset.SIDE
    view_center: tuple[float, float] = (0.0, 0.0)
    zoom_level: float = 4.0  # Width of visible area in meters
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
            "view_preset": self.view_preset.value,
            "view_center": list(self.view_center),
            "zoom_level": self.zoom_level,
            "hidden_devices": list(self.hidden_devices),
            "labels_visible": self.labels_visible,
            "beam_path_visible": self.beam_path_visible,
            "grid_visible": self.grid_visible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynopticViewState:
        """Deserialize from dictionary.

        Handles backward compatibility with old 3D state format.

        Args:
            data: Dictionary from storage.

        Returns:
            New SynopticViewState instance.
        """
        # Handle view_preset conversion with fallback for unknown/3D values
        preset_str = data.get("view_preset", "side")
        try:
            view_preset = ViewPreset(preset_str)
            # Map legacy 3D presets to SIDE
            if view_preset in (ViewPreset.ORTHO3D, ViewPreset.PERSPECTIVE):
                view_preset = ViewPreset.SIDE
        except ValueError:
            # Unknown preset value, default to SIDE
            view_preset = ViewPreset.SIDE

        # Handle view_center - may be missing in old format
        view_center = data.get("view_center")
        if view_center is None:
            # Try to derive from old camera_target
            camera_target = data.get("camera_target", [0.0, 0.0, 0.0])
            # Use X and Z for side view default
            view_center = (camera_target[0], camera_target[2])
        else:
            view_center = tuple(view_center)

        # Handle zoom_level - may be missing in old format
        zoom_level = data.get("zoom_level")
        if zoom_level is None:
            # Derive from old camera_distance
            camera_distance = data.get("camera_distance", 5.0)
            zoom_level = camera_distance * 0.8  # Approximate conversion

        return cls(
            view_preset=view_preset,
            view_center=view_center,
            zoom_level=zoom_level,
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
        # Map legacy presets to SIDE
        if preset in (ViewPreset.ORTHO3D, ViewPreset.PERSPECTIVE):
            preset = ViewPreset.SIDE

        return cls(
            view_preset=preset,
            view_center=(0.0, 0.0),
            zoom_level=4.0,
        )

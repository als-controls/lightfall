"""COSMIC-Scattering (ALS 7.0.1.1) roster for the mock backend.

Every device ships with ``metadata["synoptic"]`` so the synoptic panel
is populated out of the box. Coordinates: X = beam direction (m),
Y = lateral, Z = height. Simulation is intentionally minimal.
"""

from __future__ import annotations

import copy
import functools
import os
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from lightfall.devices.model import ConnectionType, DeviceCategory, DeviceInfo
from lightfall.devices.sim.actuators import SimTemperatureController

# RGBA per category (matches synoptic DEFAULT_COLORS palette)
_COLORS: dict[DeviceCategory, tuple[float, float, float, float]] = {
    DeviceCategory.MOTOR: (0.3, 0.5, 0.8, 1.0),
    DeviceCategory.DETECTOR: (0.8, 0.3, 0.3, 1.0),
    DeviceCategory.SENSOR: (0.3, 0.8, 0.3, 1.0),
    DeviceCategory.SHUTTER: (0.9, 0.75, 0.2, 1.0),
    DeviceCategory.VALVE: (0.2, 0.7, 0.8, 1.0),
    DeviceCategory.CONTROLLER: (0.4, 0.4, 0.4, 1.0),
}

_SHAPES: dict[DeviceCategory, str] = {
    DeviceCategory.MOTOR: "circle",
    DeviceCategory.DETECTOR: "square",
    DeviceCategory.SENSOR: "diamond",
    DeviceCategory.SHUTTER: "square",
    DeviceCategory.VALVE: "square",
    DeviceCategory.CONTROLLER: "square",
}


def _synoptic(
    category: DeviceCategory,
    x: float,
    z: float = 0.0,
    visible: bool = True,
    label: str | None = None,
    shape: str | None = None,
) -> dict[str, Any]:
    """Build a metadata["synoptic"] dict (DeviceSynopticData shape).

    ``shape`` overrides the category default (e.g. the slit-blade
    double-rect shapes for slit motors).
    """
    return {
        "position": [x, 0.0, z],
        "scale": [0.4, 0.4, 0.4],
        "primitive_shape": shape or _SHAPES[category],
        "color": list(_COLORS[category]),
        "label_text": label,
        # Offset in +Z so labels sit above devices in the default SIDE view.
        "label_offset": [0.0, 0.0, 0.35],
        "visible": visible,
    }


def _beam_path() -> list[dict[str, Any]]:
    from lightfall.devices.sim.cosmic_model import POSITIONS

    stops = ["source", "M101", "grating", "M111", "XS111", "M112", "sample", "detector"]
    return [
        {"start": [POSITIONS[a], 0.0, 0.0], "end": [POSITIONS[b], 0.0, 0.0],
         "color": [1.0, 0.0, 0.0, 0.5], "width": 0.02, "id": f"{a}_to_{b}"}
        for a, b in zip(stops, stops[1:], strict=False)
    ]


BEAMLINE_SCOPE_METADATA: dict[str, Any] = {"synoptic": {"beam_path": _beam_path()}}

# Synoptic placements for devices created elsewhere in mock.py.
# Legacy test devices are hidden; reused roster devices are visible.
LEGACY_SYNOPTIC: dict[str, dict[str, Any]] = {
    "motor": _synoptic(DeviceCategory.MOTOR, 39.3, -2.2, visible=False),
    "motor1": _synoptic(DeviceCategory.MOTOR, 39.5, -2.2, visible=False),
    "motor2": _synoptic(DeviceCategory.MOTOR, 39.7, -2.2, visible=False),
    "motor3": _synoptic(DeviceCategory.MOTOR, 39.9, -2.2, visible=False),
    "det": _synoptic(DeviceCategory.DETECTOR, 42.3, -2.2, visible=False),
    "det1": _synoptic(DeviceCategory.DETECTOR, 42.5, -2.2, visible=False),
    "det2": _synoptic(DeviceCategory.DETECTOR, 42.7, -2.2, visible=False),
    "noisy_det": _synoptic(DeviceCategory.DETECTOR, 42.9, -2.2, visible=False),
    "pressure": _synoptic(DeviceCategory.SENSOR, 39.8, -2.2, visible=False),
    "slit_gap": _synoptic(DeviceCategory.MOTOR, 37.3, -2.2, visible=False),
    "slit_center": _synoptic(DeviceCategory.MOTOR, 37.5, -2.2, visible=False),
    "sim_det": _synoptic(DeviceCategory.DETECTOR, 44.3, -2.2, visible=False),
    # Reused/visible existing devices
    "sample_x": _synoptic(DeviceCategory.MOTOR, 39.3, -0.7),
    "sample_y": _synoptic(DeviceCategory.MOTOR, 39.8, -0.7),
    "ring_current": _synoptic(DeviceCategory.SENSOR, 0.0, 0.7),
}


def _info(
    name: str,
    description: str,
    category: DeviceCategory,
    device_class: str,
    location: str,
    tags: list[str],
    ophyd_obj: Any,
    x: float,
    z: float = 0.0,
    metadata: dict[str, Any] | None = None,
    shape: str | None = None,
) -> DeviceInfo:
    md = dict(metadata or {})
    md["synoptic"] = _synoptic(category, x, z, shape=shape)
    info = DeviceInfo(
        name=name,
        description=description,
        category=category,
        device_class=device_class,
        connection_type=ConnectionType.SIMULATED,
        prefix=name,
        beamline="sim",
        location=location,
        tags=tags,
        metadata=md,
    )
    info._ophyd_device = ophyd_obj
    return info


@functools.lru_cache(maxsize=1)
def _default_ad_root() -> Path:
    """Fresh per-process temp dir for the sim area detector, created once.

    Cached so repeated ``MockBackend()`` construction (e.g. in tests)
    doesn't leak a new temp directory every time.
    """
    return Path(tempfile.mkdtemp(prefix="lightfall_area_det_"))


def _create_area_detector() -> Any | None:
    """ophyd-async sim detector writing real HDF5 files. None if unavailable.

    Write directory comes from the ``LIGHTFALL_SIM_AD_DIR`` env var
    (set by tests/deployments that want a known, cleaned-up location),
    defaulting to a cached per-process temp dir otherwise.
    """
    try:
        from ophyd_async.core import StaticPathProvider, UUIDFilenameProvider
        from ophyd_async.sim import PatternGenerator, SimBlobDetector

        env_dir = os.environ.get("LIGHTFALL_SIM_AD_DIR")
        root = Path(env_dir) if env_dir else _default_ad_root()
        root.mkdir(parents=True, exist_ok=True)
        provider = StaticPathProvider(UUIDFilenameProvider(), root)
        return SimBlobDetector(
            path_provider=provider,
            pattern_generator=PatternGenerator(),
            name="area_det",
        )
    except ImportError as e:
        logger.debug("ophyd-async sim area detector unavailable: {}", e)
        return None
    except Exception as e:
        logger.warning("Failed to create sim area detector: {}", e)
        return None


def create_roster(existing_ophyd: dict[str, Any]) -> list[DeviceInfo]:
    """Create the beamline roster devices: COSMIC-Scattering (7.0.1.1) on one
    beam model (see :mod:`lightfall.devices.sim.cosmic_roster`), plus the
    sample-environment controller and the HDF5 area detector.

    Args:
        existing_ophyd: Already-created ophyd objects by name; ``ring_current``
            (if present) feeds the source flux.

    Returns:
        DeviceInfo list (ophyd objects attached), beam order.
    """
    from lightfall.devices.sim import cosmic_model
    from lightfall.devices.sim.cosmic_roster import build

    infos: list[DeviceInfo] = []
    for spec in build(existing_ophyd):
        md: dict[str, Any] = dict(spec["extra"])
        if spec["units"]:
            md["units"] = spec["units"]
        if spec["precision"] is not None:
            md["precision"] = spec["precision"]
        obj = spec["obj"]
        infos.append(_info(
            spec["name"], spec["description"], spec["category"],
            f"{type(obj).__module__}.{type(obj).__qualname__}", spec["location"],
            spec["tags"], obj, x=spec["x"], z=spec["z"], metadata=md, shape=spec["shape"],
        ))

    temperature = SimTemperatureController(name="temperature")
    infos.append(_info(
        "temperature", "Sample temperature controller (slewing readback)",
        DeviceCategory.CONTROLLER,
        "lightfall.devices.sim.actuators.SimTemperatureController",
        "Sample Environment", ["controller", "temperature", "sample"],
        temperature, x=cosmic_model.POSITIONS["sample"], z=-2.1,
        metadata={"units": "K", "precision": 2},
    ))

    area_det = _create_area_detector()
    if area_det is not None:
        infos.append(_info(
            "area_det", "Area detector (writes HDF5 via ophyd-async sim)",
            DeviceCategory.DETECTOR,
            "ophyd_async.sim.SimBlobDetector", "Endstation",
            ["detector", "camera", "area", "hdf5"], area_det,
            x=cosmic_model.POSITIONS["detector"] + 0.4,
        ))

    return infos


def get_beamline_scope_metadata() -> dict[str, Any]:
    """Deep copy so callers can't mutate the module-level dict."""
    return copy.deepcopy(BEAMLINE_SCOPE_METADATA)

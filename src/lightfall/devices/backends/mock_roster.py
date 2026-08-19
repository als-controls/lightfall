"""Generic scattering-beamline roster for the mock backend.

Every device ships with ``metadata["synoptic"]`` so the synoptic panel
is populated out of the box. Coordinates: X = beam direction (m),
Y = lateral, Z = height. Simulation is intentionally minimal.
"""

from __future__ import annotations

import copy
import functools
import os
import random
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from lightfall.devices.model import ConnectionType, DeviceCategory, DeviceInfo
from lightfall.devices.sim.actuators import SimShutter, SimTemperatureController

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


BEAMLINE_SCOPE_METADATA: dict[str, Any] = {
    "synoptic": {
        "beam_path": [
            {"start": [0.0, 0.0, 0.0], "end": [5.0, 0.0, 0.0],
             "color": [1.0, 0.0, 0.0, 0.5], "width": 0.02, "id": "source_to_m1"},
            {"start": [5.0, 0.0, 0.0], "end": [22.0, 0.0, 0.0],
             "color": [1.0, 0.0, 0.0, 0.5], "width": 0.02, "id": "m1_to_sample"},
            {"start": [22.0, 0.0, 0.0], "end": [27.5, 0.0, 0.0],
             "color": [1.0, 0.0, 0.0, 0.5], "width": 0.02, "id": "sample_to_det"},
        ]
    }
}

# Synoptic placements for devices created elsewhere in mock.py.
# Legacy test devices are hidden; reused roster devices are visible.
LEGACY_SYNOPTIC: dict[str, dict[str, Any]] = {
    "motor": _synoptic(DeviceCategory.MOTOR, 22.0, -2.2, visible=False),
    "motor1": _synoptic(DeviceCategory.MOTOR, 22.2, -2.2, visible=False),
    "motor2": _synoptic(DeviceCategory.MOTOR, 22.4, -2.2, visible=False),
    "motor3": _synoptic(DeviceCategory.MOTOR, 22.6, -2.2, visible=False),
    "det": _synoptic(DeviceCategory.DETECTOR, 25.0, -2.2, visible=False),
    "det1": _synoptic(DeviceCategory.DETECTOR, 25.2, -2.2, visible=False),
    "det2": _synoptic(DeviceCategory.DETECTOR, 25.4, -2.2, visible=False),
    "noisy_det": _synoptic(DeviceCategory.DETECTOR, 25.6, -2.2, visible=False),
    "pressure": _synoptic(DeviceCategory.SENSOR, 22.5, -2.2, visible=False),
    "slit_gap": _synoptic(DeviceCategory.MOTOR, 20.0, -2.2, visible=False),
    "slit_center": _synoptic(DeviceCategory.MOTOR, 20.2, -2.2, visible=False),
    "sim_det": _synoptic(DeviceCategory.DETECTOR, 27.0, -2.2, visible=False),
    # Reused/visible existing devices
    "sample_x": _synoptic(DeviceCategory.MOTOR, 22.0, -0.7),
    "sample_y": _synoptic(DeviceCategory.MOTOR, 22.5, -0.7),
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


def _make_i0_func(shutters: list[SimShutter], gap_axes: list[Any]):
    """Transmitted intensity: dies when a shutter closes, scales with slit gaps."""

    def _i0() -> float:
        if not all(s.is_open for s in shutters):
            return random.uniform(0.0, 0.02)  # dark counts
        gap_factor = 1.0
        for axis in gap_axes:
            gap = axis.readback.get()
            gap_factor *= max(0.0, min(1.0, gap / 1.0))
        return 100.0 * gap_factor + random.uniform(-0.5, 0.5)

    return _i0


def create_roster(existing_ophyd: dict[str, Any]) -> list[DeviceInfo]:
    """Create the beamline roster devices.

    Args:
        existing_ophyd: Already-created ophyd objects by name; must
            contain ``sample_x`` and ``sample_y`` (reused, not recreated).

    Returns:
        DeviceInfo list (ophyd objects attached), beam order.
    """
    from ophyd.sim import SynAxis, SynGauss, SynSignal

    infos: list[DeviceInfo] = []

    def axis(name: str, value: float = 0.0, delay: float = 0.0) -> Any:
        return SynAxis(name=name, value=value, delay=delay, labels={"motors"})

    # --- Front end -------------------------------------------------
    fe_shutter = SimShutter(name="fe_shutter")
    infos.append(_info(
        "fe_shutter", "Front-end photon shutter", DeviceCategory.SHUTTER,
        "lightfall.devices.sim.actuators.SimShutter", "Front End",
        ["shutter", "front-end"], fe_shutter, x=1.5,
    ))

    gv_1 = SimShutter(name="gv_1", initial="open")
    infos.append(_info(
        "gv_1", "Gate valve 1", DeviceCategory.VALVE,
        "lightfall.devices.sim.actuators.SimShutter", "Front End",
        ["valve", "vacuum"], gv_1, x=2.5,
    ))

    ig_1 = SynSignal(name="ig_1", func=lambda: 2e-9 * (1 + random.uniform(-0.1, 0.1)))
    infos.append(_info(
        "ig_1", "Ion gauge 1", DeviceCategory.SENSOR, "ophyd.sim.SynSignal",
        "Front End", ["sensor", "vacuum", "pressure"], ig_1, x=3.0, z=0.7,
        metadata={"units": "Torr", "precision": 2},
    ))

    # --- Optics ----------------------------------------------------
    m1_pitch = axis("m1_pitch")
    infos.append(_info(
        "m1_pitch", "M1 mirror pitch", DeviceCategory.MOTOR,
        "ophyd.sim.SynAxis", "Optics", ["motor", "mirror", "optics"],
        m1_pitch, x=5.0, metadata={"units": "mrad", "precision": 4},
    ))
    m1_bend = axis("m1_bend")
    infos.append(_info(
        "m1_bend", "M1 mirror bender", DeviceCategory.MOTOR,
        "ophyd.sim.SynAxis", "Optics", ["motor", "mirror", "optics"],
        m1_bend, x=5.0, z=-0.7, metadata={"units": "um", "precision": 1},
    ))

    white_slits_hgap = axis("white_slits_hgap", value=1.0)
    white_slits_vgap = axis("white_slits_vgap", value=1.0)
    infos.append(_info(
        "white_slits_hgap", "White-beam slits horizontal gap",
        DeviceCategory.MOTOR, "ophyd.sim.SynAxis", "Optics",
        ["motor", "slit", "optics"], white_slits_hgap, x=7.0,
        metadata={"units": "mm", "precision": 3}, shape="double_rect_h",
    ))
    infos.append(_info(
        "white_slits_vgap", "White-beam slits vertical gap",
        DeviceCategory.MOTOR, "ophyd.sim.SynAxis", "Optics",
        ["motor", "slit", "optics"], white_slits_vgap, x=7.0, z=-0.7,
        metadata={"units": "mm", "precision": 3}, shape="double_rect_v",
    ))

    mono_energy = axis("mono_energy", value=800.0, delay=0.5)
    infos.append(_info(
        "mono_energy", "Monochromator energy", DeviceCategory.MOTOR,
        "ophyd.sim.SynAxis", "Optics", ["motor", "mono", "energy", "optics"],
        mono_energy, x=9.0, metadata={"units": "eV", "precision": 2},
    ))

    ps_shutter = SimShutter(name="ps_shutter")
    infos.append(_info(
        "ps_shutter", "Photon shutter (post-mono)", DeviceCategory.SHUTTER,
        "lightfall.devices.sim.actuators.SimShutter", "Optics",
        ["shutter"], ps_shutter, x=10.5,
    ))

    gv_2 = SimShutter(name="gv_2", initial="open")
    infos.append(_info(
        "gv_2", "Gate valve 2", DeviceCategory.VALVE,
        "lightfall.devices.sim.actuators.SimShutter", "Optics",
        ["valve", "vacuum"], gv_2, x=11.5,
    ))

    ig_2 = SynSignal(name="ig_2", func=lambda: 5e-10 * (1 + random.uniform(-0.1, 0.1)))
    infos.append(_info(
        "ig_2", "Ion gauge 2", DeviceCategory.SENSOR, "ophyd.sim.SynSignal",
        "Optics", ["sensor", "vacuum", "pressure"], ig_2, x=12.0, z=0.7,
        metadata={"units": "Torr", "precision": 2},
    ))

    mono_slits_hgap = axis("mono_slits_hgap", value=1.0)
    mono_slits_vgap = axis("mono_slits_vgap", value=1.0)
    infos.append(_info(
        "mono_slits_hgap", "Mono slits horizontal gap", DeviceCategory.MOTOR,
        "ophyd.sim.SynAxis", "Optics", ["motor", "slit", "optics"],
        mono_slits_hgap, x=13.0, metadata={"units": "mm", "precision": 3},
        shape="double_rect_h",
    ))
    infos.append(_info(
        "mono_slits_vgap", "Mono slits vertical gap", DeviceCategory.MOTOR,
        "ophyd.sim.SynAxis", "Optics", ["motor", "slit", "optics"],
        mono_slits_vgap, x=13.0, z=-0.7, metadata={"units": "mm", "precision": 3},
        shape="double_rect_v",
    ))

    # --- Diagnostics -----------------------------------------------
    bpm_x = SynSignal(name="bpm_x", func=lambda: random.gauss(0.0, 1.5))
    bpm_y = SynSignal(name="bpm_y", func=lambda: random.gauss(0.0, 1.5))
    infos.append(_info(
        "bpm_x", "Beam position monitor X", DeviceCategory.SENSOR,
        "ophyd.sim.SynSignal", "Diagnostics", ["sensor", "bpm", "beam"],
        bpm_x, x=15.0, z=0.7, metadata={"units": "um", "precision": 1},
    ))
    infos.append(_info(
        "bpm_y", "Beam position monitor Y", DeviceCategory.SENSOR,
        "ophyd.sim.SynSignal", "Diagnostics", ["sensor", "bpm", "beam"],
        bpm_y, x=15.6, z=0.7, metadata={"units": "um", "precision": 1},
    ))

    filter_wheel = axis("filter_wheel")
    infos.append(_info(
        "filter_wheel", "Attenuator filter wheel (positions 0-5)",
        DeviceCategory.MOTOR, "ophyd.sim.SynAxis", "Diagnostics",
        ["motor", "filter", "attenuator"], filter_wheel, x=17.0,
        metadata={"units": "position", "precision": 0,
                  "discrete_positions": [0, 1, 2, 3, 4, 5]},
    ))

    i0 = SynSignal(
        name="i0",
        func=_make_i0_func(
            [fe_shutter, ps_shutter],
            [white_slits_hgap, white_slits_vgap, mono_slits_hgap, mono_slits_vgap],
        ),
    )
    infos.append(_info(
        "i0", "Incident intensity diode (responds to shutters and slits)",
        DeviceCategory.DETECTOR, "ophyd.sim.SynSignal", "Diagnostics",
        ["detector", "diode", "i0", "normalization"], i0, x=18.5, z=0.7,
        metadata={"units": "counts", "precision": 2},
    ))

    # --- Endstation ------------------------------------------------
    es_defs = [
        ("es_slits_hgap", "Endstation slits horizontal gap", 1.0, 20.0, 0.0, "double_rect_h"),
        ("es_slits_vgap", "Endstation slits vertical gap", 1.0, 20.0, -0.7, "double_rect_v"),
        ("es_slits_hcen", "Endstation slits horizontal center", 0.0, 20.6, 0.0, "double_rect_h"),
        ("es_slits_vcen", "Endstation slits vertical center", 0.0, 20.6, -0.7, "double_rect_v"),
    ]
    for name, desc, value, x, z, shape in es_defs:
        obj = axis(name, value=value)
        infos.append(_info(
            name, desc, DeviceCategory.MOTOR, "ophyd.sim.SynAxis",
            "Endstation", ["motor", "slit", "endstation"], obj, x=x, z=z,
            metadata={"units": "mm", "precision": 3}, shape=shape,
        ))

    sample_z = axis("sample_z", delay=0.1)
    sample_theta = axis("sample_theta", delay=0.1)
    infos.append(_info(
        "sample_z", "Sample Z position", DeviceCategory.MOTOR,
        "ophyd.sim.SynAxis", "Sample Stage", ["motor", "sample", "position"],
        sample_z, x=23.0, z=-0.7, metadata={"units": "um", "precision": 1},
    ))
    infos.append(_info(
        "sample_theta", "Sample rotation", DeviceCategory.MOTOR,
        "ophyd.sim.SynAxis", "Sample Stage", ["motor", "sample", "rotation"],
        sample_theta, x=23.5, z=-0.7, metadata={"units": "deg", "precision": 2},
    ))

    temperature = SimTemperatureController(name="temperature")
    infos.append(_info(
        "temperature", "Sample temperature controller (slewing readback)",
        DeviceCategory.CONTROLLER,
        "lightfall.devices.sim.actuators.SimTemperatureController",
        "Sample Environment", ["controller", "temperature", "sample"],
        temperature, x=22.5, z=-1.4, metadata={"units": "K", "precision": 2},
    ))

    sample_x = existing_ophyd["sample_x"]
    point_det = SynGauss(
        "point_det", sample_x, "sample_x",
        center=0, Imax=100, sigma=5, noise="poisson", labels={"detectors"},
    )
    infos.append(_info(
        "point_det", "Point detector (Gaussian vs sample_x)",
        DeviceCategory.DETECTOR, "ophyd.sim.SynGauss", "Endstation",
        ["detector", "point", "endstation"], point_det, x=25.0, z=0.7,
        metadata={"units": "counts"},
    ))

    area_det = _create_area_detector()
    if area_det is not None:
        infos.append(_info(
            "area_det", "Area detector (writes HDF5 via ophyd-async sim)",
            DeviceCategory.DETECTOR,
            "ophyd_async.sim.SimBlobDetector", "Endstation",
            ["detector", "camera", "area", "hdf5"], area_det, x=27.0,
        ))

    return infos


def get_beamline_scope_metadata() -> dict[str, Any]:
    """Deep copy so callers can't mutate the module-level dict."""
    return copy.deepcopy(BEAMLINE_SCOPE_METADATA)

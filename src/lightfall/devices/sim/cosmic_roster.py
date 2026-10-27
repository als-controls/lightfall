"""COSMIC-Scattering (7.0.1.1) sim roster: ophyd sim devices on one beam model.

Every detector reading is computed by :mod:`lightfall.devices.sim.cosmic_model`
from the live state of every upstream device, so the devices behave as one
beamline: close the front-end shutter and every diode drops to dark current,
mis-pitch M112 and the spot walks off the pinhole, raise ``sample_lift`` and
``DetectorDiodeCurrent`` falls through a half-cut, rock
``sample_rotate_steppertheta`` at the half-cut and it peaks.

Names follow the real 7.0.1.1 devices where they are known (``M112``,
``DIAG111``, ``DIAG112_Diode``, ``PinholeX``, ``sample_lift``,
``sample_rotate_steppertheta``, ``DetectorDiodeCurrent`` ...), so the
endstation's skills (reflection alignment) run unchanged against the sim.

``LIGHTFALL_SIM_SEED`` randomises the hidden sample and mirror misalignments
(``cosmic_model.DEFAULT_TRUTH``) for a fresh alignment problem per session;
unset, the defaults are deterministic.
"""

from __future__ import annotations

import os
import random
from typing import Any

from lightfall.devices.sim import cosmic_model as model
from lightfall.devices.sim.actuators import SimShutter


def _truth() -> dict[str, float]:
    truth = dict(model.DEFAULT_TRUTH)
    seed = os.environ.get("LIGHTFALL_SIM_SEED", "").strip()
    if seed:
        rng = random.Random(seed)
        truth.update({
            "sample_surface_um": rng.uniform(900.0, 1600.0),
            "sample_tilt_deg": rng.uniform(-0.4, 0.4),
            "sample_center_um": rng.uniform(-300.0, 300.0),
            "M101_pitch_offset_urad": rng.uniform(-1.0, 1.0),
            "M111_pitch_offset_urad": rng.uniform(-0.5, 0.5),
            "M112_pitch_offset_urad": rng.uniform(-4.0, 4.0),
        })
    return truth


def build(existing_ophyd: dict[str, Any]) -> list[dict[str, Any]]:
    """Create the devices. Returns specs in beam order:
    ``{name, obj, description, category, location, tags, units, precision,
    x (m along the beam), z (synoptic row), shape}``; mock_roster turns
    them into DeviceInfo entries."""
    from ophyd.sim import SynAxis, SynSignal

    from lightfall.devices.model import DeviceCategory as C

    truth = _truth()
    devices: dict[str, Any] = {}
    specs: list[dict[str, Any]] = []

    def add(name, obj, desc, category, location, tags, *, at, z=0.0,
            units=None, precision=None, shape=None, extra=None):
        devices[name] = obj
        specs.append({
            "name": name, "obj": obj, "description": desc, "category": category,
            "location": location, "tags": tags, "units": units,
            "precision": precision, "x": model.POSITIONS[at], "z": z,
            "shape": shape, "extra": extra or {},
        })
        return obj

    def axis(name, value=0.0, delay=0.0, cls=SynAxis, egu=""):
        return cls(name=name, value=value, delay=delay, labels={"motors"}, egu=egu)

    # --- live state for the beam model -------------------------------
    def state() -> dict[str, Any]:
        s: dict[str, Any] = dict(truth)
        ring = existing_ophyd.get("ring_current")
        if ring is not None:
            try:
                s["ring_current"] = float(ring.get())
            except Exception:  # noqa: BLE001
                pass
        for name, dev in devices.items():
            if isinstance(dev, SimShutter):
                s[f"{name}_open"] = dev.is_open
                s[f"{name}_in"] = not dev.is_open  # paddles: "closed" = in the beam
            elif hasattr(dev, "readback"):
                s[name] = float(dev.readback.get())
        return s

    def diode(name: str):
        def read() -> float:
            value = model.diode_readings(state())[name]
            return max(0.0, random.gauss(value, 0.004 * value + 0.5))
        return read

    h0 = model.best_harmonic(745.0)

    # --- Source / front end --------------------------------------------
    add("epu_gap", axis("epu_gap", model.epu_gap_for(745.0, h0), delay=0.2, egu="mm"),
        "EPU gap", C.MOTOR, "Source", ["motor", "undulator", "source"],
        at="source", units="mm", precision=3)
    add("epu_harmonic", axis("epu_harmonic", float(h0)),
        "EPU harmonic in use (1, 3 or 5)", C.MOTOR, "Source", ["undulator", "source"],
        at="source", z=-0.7, precision=0, extra={"discrete_positions": [1, 3, 5]})
    add("fe_shutter", SimShutter(name="fe_shutter", initial="open"),
        "Front-end photon shutter", C.SHUTTER, "Front End", ["shutter", "front-end"],
        at="fe_shutter")
    add("gv_1", SimShutter(name="gv_1", initial="open"), "Gate valve 1", C.VALVE,
        "Front End", ["valve", "vacuum"], at="mask")
    add("ig_1", SynSignal(name="ig_1", func=lambda: 2e-9 * (1 + random.uniform(-0.1, 0.1))),
        "Ion gauge 1", C.SENSOR, "Front End", ["sensor", "vacuum", "pressure"],
        at="mask", z=0.7, units="Torr", precision=2)

    # --- Optics ------------------------------------------------------------
    add("M101_pitch", axis("M101_pitch", egu="urad"), "M101 cylindrical mirror pitch (steers horizontally)",
        C.MOTOR, "Optics", ["motor", "mirror", "optics"], at="M101",
        units="urad", precision=2)
    add("DIAG101", SimShutter(name="DIAG101", initial="open"),
        "DIAG101 paddle (closed = in the beam, blocks downstream)", C.SHUTTER,
        "Optics", ["diagnostic", "paddle"], at="DIAG101")
    add("DIAG101_Diode", SynSignal(name="DIAG101_Diode", func=diode("DIAG101_Diode")),
        "DIAG101 paddle photodiode (reads with the paddle in)", C.DETECTOR, "Optics",
        ["detector", "diode", "diagnostic"], at="DIAG101", z=0.7, units="nA", precision=1)

    class _BeamlineEnergy(SynAxis):
        """Moves the PGM and tracks the EPU (lowest harmonic that reaches)."""

        def set(self, value, **kwargs):
            e = float(value)
            h = model.best_harmonic(e)
            devices["epu_harmonic"].set(float(h))
            devices["epu_gap"].set(model.epu_gap_for(e, h))
            devices["mono_energy"].set(e)
            return super().set(value, **kwargs)

    add("mono_energy", axis("mono_energy", 745.0, delay=0.3, egu="eV"),
        "PGM energy (300 l/mm plane grating; EPU not moved)", C.MOTOR, "Optics",
        ["motor", "mono", "energy", "optics"], at="grating", units="eV", precision=2)
    add("beamline_energy", axis("beamline_energy", 745.0, delay=0.3, cls=_BeamlineEnergy, egu="eV"),
        "Beamline energy: PGM plus EPU gap/harmonic tracking", C.MOTOR, "Optics",
        ["motor", "energy", "optics"], at="grating", z=-0.7, units="eV", precision=2)
    add("DIAG102_hgap", axis("DIAG102_hgap", 10000.0, egu="um"), "DIAG102 4-jaw horizontal gap",
        C.MOTOR, "Optics", ["motor", "slit", "optics"], at="DIAG102", units="um",
        precision=0, shape="double_rect_h")
    add("DIAG102_vgap", axis("DIAG102_vgap", 10000.0, egu="um"), "DIAG102 4-jaw vertical gap",
        C.MOTOR, "Optics", ["motor", "slit", "optics"], at="DIAG102", z=-0.7, units="um",
        precision=0, shape="double_rect_v")
    add("beamline_shutter", SimShutter(name="beamline_shutter", initial="open"),
        "Beamline photon shutter (post-mono)", C.SHUTTER, "Optics", ["shutter"],
        at="beamline_shutter")
    add("gv_2", SimShutter(name="gv_2", initial="open"), "Gate valve 2", C.VALVE,
        "Optics", ["valve", "vacuum"], at="M111", z=0.7)
    add("ig_2", SynSignal(name="ig_2", func=lambda: 5e-10 * (1 + random.uniform(-0.1, 0.1))),
        "Ion gauge 2", C.SENSOR, "Optics", ["sensor", "vacuum", "pressure"],
        at="M111", z=1.4, units="Torr", precision=2)
    add("M111_pitch", axis("M111_pitch", egu="urad"), "M111 toroidal mirror pitch (steers vertically)",
        C.MOTOR, "Optics", ["motor", "mirror", "optics"], at="M111", units="urad", precision=2)
    add("DIAG111", SimShutter(name="DIAG111", initial="open"),
        "DIAG111 paddle (closed = in the beam, blocks downstream)", C.SHUTTER,
        "Optics", ["diagnostic", "paddle"], at="DIAG111")
    add("DIAG111_Diode", SynSignal(name="DIAG111_Diode", func=diode("DIAG111_Diode")),
        "DIAG111 paddle photodiode (reads with the paddle in)", C.DETECTOR, "Optics",
        ["detector", "diode", "diagnostic"], at="DIAG111", z=0.7, units="nA", precision=1)
    add("XS111_hgap", axis("XS111_hgap", 68.0, egu="um"), "XS-111 exit slit horizontal gap",
        C.MOTOR, "Optics", ["motor", "slit", "exit-slit"], at="XS111", units="um",
        precision=1, shape="double_rect_h")
    add("XS111_vgap", axis("XS111_vgap", 35.0, egu="um"), "XS-111 exit slit vertical gap",
        C.MOTOR, "Optics", ["motor", "slit", "exit-slit"], at="XS111", z=-0.7, units="um",
        precision=1, shape="double_rect_v")
    add("M112_pitch", axis("M112_pitch", egu="urad"), "M112 refocusing mirror pitch (steers the spot vertically)",
        C.MOTOR, "Optics", ["motor", "mirror", "optics"], at="M112", units="urad", precision=2)

    # --- Endstation ------------------------------------------------------
    add("DIAG112_Diode", SynSignal(name="DIAG112_Diode", func=diode("DIAG112_Diode")),
        "DIAG112 I0 mesh diode (incident intensity)", C.DETECTOR, "Endstation",
        ["detector", "diode", "i0", "normalization"], at="DIAG112", z=0.7,
        units="nA", precision=2)
    add("PinholeX", axis("PinholeX", egu="um"), "Pinhole horizontal position (50 um pinhole)",
        C.MOTOR, "Endstation", ["motor", "pinhole"], at="pinhole", units="um", precision=1)
    add("PinholeY", axis("PinholeY", egu="um"), "Pinhole vertical position", C.MOTOR, "Endstation",
        ["motor", "pinhole"], at="pinhole", z=-0.7, units="um", precision=1)

    translate = add("sample_translate", axis("sample_translate", delay=0.05, egu="um"),
                    "Sample translate (across the beam)", C.MOTOR, "Sample Stage",
                    ["motor", "sample", "position"], at="sample", z=-1.4,
                    units="um", precision=1)

    class _CoupledLift(SynAxis):
        """Lifting drags sample_translate along by LIFT_TRANSLATE_COUPLING per
        micron, like the real stage: plain lift scans let translate drift."""

        def set(self, value, **kwargs):
            delta = float(value) - float(self.readback.get())
            translate.set(float(translate.readback.get()) + model.LIFT_TRANSLATE_COUPLING * delta)
            return super().set(value, **kwargs)

    add("sample_lift", axis("sample_lift", delay=0.05, cls=_CoupledLift, egu="um"),
        "Sample lift (coupled: drags sample_translate)", C.MOTOR, "Sample Stage",
        ["motor", "sample", "position"], at="sample", units="um", precision=1)
    add("sample_rotate_steppertheta", axis("sample_rotate_steppertheta", delay=0.05, egu="deg"),
        "Sample theta (stepper)", C.MOTOR, "Sample Stage", ["motor", "sample", "rotation"],
        at="sample", z=-0.7, units="deg", precision=3)
    detector = add("DetectorDiodeCurrent", SynSignal(name="DetectorDiodeCurrent",
                                                     func=diode("DetectorDiodeCurrent")),
                   "Direct-beam detector diode behind the sample", C.DETECTOR, "Endstation",
                   ["detector", "diode", "endstation"], at="detector", z=0.7,
                   units="nA", precision=1)
    # Hook for evaluation scoring (lightfall.eval): the live model state,
    # hidden truth included. Not used by anything at run time.
    detector.beam_state = state
    return specs

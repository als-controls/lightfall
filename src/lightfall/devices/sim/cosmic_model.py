"""Low-fidelity beam model of COSMIC-Scattering (ALS 7.0.1.1).

Pure functions over a plain state dict, so the physics is testable without
ophyd. ``lightfall.devices.backends.mock_roster`` wires real ophyd sim
devices to it: every detector reading is computed from the current state of
every upstream device, so the simulated devices behave as one beamline.

Geometry (distances from the source, metres) comes from the OASYS/SHADOW
model "7.0.1 COSMIC Imaging and Scattering" (A. Wojdyla, 2023): element
order, spacings, grazing angles, the 300 l/mm plane-grating monochromator,
the mask and the XS-111 exit slit sizes. The scattering branch is
M111 -> XS-111 -> M112. Everything else (EPU tuning curve, efficiencies,
beam sizes, sample) is a plausible stand-in chosen so that a healthy
``DetectorDiodeCurrent`` reads ~15000 nA, matching the reflection-alignment
skill's expectations. Not a raytrace: no energy resolution, no aberrations.
"""

from __future__ import annotations

import math
from typing import Any

#: Element positions along the beam (m from the source), OASYS model.
POSITIONS: dict[str, float] = {
    "source": 0.0,
    "fe_shutter": 13.5,
    "mask": 14.85,
    "M101": 15.0,
    "DIAG101": 15.5,
    "M102": 17.2,
    "grating": 17.5,
    "zero_order_mask": 18.5,
    "DIAG102": 19.3,
    "beamline_shutter": 20.5,
    "M111": 24.3,
    "DIAG111": 25.5,
    "XS111": 29.3,
    "M112": 34.3,
    "DIAG112": 37.8,
    "pinhole": 38.8,
    "sample": 39.3,
    "detector": 39.6,
}

#: Grazing incidence angles (deg), OASYS model.
GRAZING_DEG = {"M101": 1.25, "M102": 1.4783, "M111": 1.5, "M112": 1.5}
GRATING_LINES_PER_MM = 300.0

# --- Source: EPU ---------------------------------------------------------
#: Fundamental at the reference gap is 149 eV (the OASYS sources are the
#: 3rd and 5th harmonics of 149 eV: 450 and 745 eV).
EPU_REF_GAP_MM = 30.0
EPU_REF_E1_EV = 149.0
EPU_E1_SLOPE_EV_PER_MM = 4.0
EPU_GAP_LIMITS_MM = (14.0, 100.0)
HARMONIC_STRENGTH = {1: 1.0, 3: 0.6, 5: 0.35}
HARMONIC_REL_WIDTH = 0.02  # sigma / harmonic energy
SOURCE_FLUX = 2.4e13  # ph/s at 500 mA, harmonic peak, before optics (calibrated: ~15000 nA aligned)
RING_NOMINAL_MA = 500.0

# --- Optics efficiencies (lumped) -----------------------------------------
MIRROR_REFLECTIVITY = 0.85
GRATING_EFFICIENCY = 0.12

# --- Beam sizes (1-sigma, micrometres) at each aperture --------------------
BEAM_SIGMA_UM = {
    "mask": (1200.0, 2500.0),
    "DIAG102": (1500.0, 1000.0),
    "XS111": (30.0, 15.0),
    "pinhole": (10.0, 10.0),
    "sample": (12.0, 12.0),
}
MASK_SIZE_UM = (5000.0, 15000.0)  # OASYS: 5 mm x 15 mm
PINHOLE_DIAMETER_UM = 50.0

# --- Sample (reflection geometry) ------------------------------------------
SAMPLE_LENGTH_UM = 5000.0  # along the beam
SAMPLE_WIDTH_UM = 5000.0  # across the beam (sample_translate)
LIFT_TRANSLATE_COUPLING = 0.15  # um of translate drift per um of lift moved

# --- Detection ------------------------------------------------------------
ELECTRON = 1.602176634e-19
SI_PAIR_EV = 3.65
DIODE_DARK_NA = 2.0
I0_MESH_FRACTION = 0.02  # DIAG112 is a thin mesh: reads 2%, passes the rest

#: Hidden "truth" of the simulated sample and optics. ``state`` keys of the
#: same names override these (the roster can randomise them per session).
DEFAULT_TRUTH: dict[str, float] = {
    "sample_surface_um": 1250.0,  # sample_lift where the surface is on the beam axis
    "sample_tilt_deg": 0.15,  # sample_rotate_steppertheta where the surface is parallel
    "sample_center_um": 0.0,  # sample_translate that centres the sample on the beam
    "M101_pitch_offset_urad": 0.0,
    "M111_pitch_offset_urad": 0.0,
    "M112_pitch_offset_urad": 2.0,  # small error: pinhole needs a touch of alignment
}


def epu_fundamental_ev(gap_mm: float) -> float:
    return EPU_REF_E1_EV + EPU_E1_SLOPE_EV_PER_MM * (gap_mm - EPU_REF_GAP_MM)


def epu_gap_for(energy_ev: float, harmonic: int) -> float:
    """EPU gap that puts ``harmonic`` on ``energy_ev``."""
    return EPU_REF_GAP_MM + (energy_ev / harmonic - EPU_REF_E1_EV) / EPU_E1_SLOPE_EV_PER_MM


def best_harmonic(energy_ev: float) -> int:
    """Lowest odd harmonic whose gap stays inside the EPU's limits."""
    for h in (1, 3, 5):
        if EPU_GAP_LIMITS_MM[0] <= epu_gap_for(energy_ev, h) <= EPU_GAP_LIMITS_MM[1]:
            return h
    return 5


def source_spectrum(energy_ev: float, gap_mm: float, harmonic: int) -> float:
    """Relative undulator intensity at ``energy_ev`` (0..1)."""
    e_h = harmonic * epu_fundamental_ev(gap_mm)
    if e_h <= 0:
        return 0.0
    sigma = HARMONIC_REL_WIDTH * e_h
    return HARMONIC_STRENGTH.get(int(harmonic), 0.0) * math.exp(
        -0.5 * ((energy_ev - e_h) / sigma) ** 2
    )


def slit_transmission(gap_um: float, offset_um: float, sigma_um: float) -> float:
    """Fraction of a Gaussian beam (centre ``offset_um`` from the slit centre)
    passing a slit of full width ``gap_um``."""
    if gap_um <= 0:
        return 0.0
    s = math.sqrt(2.0) * max(sigma_um, 1e-9)
    half = gap_um / 2.0
    return max(0.0, 0.5 * (math.erf((half - offset_um) / s) + math.erf((half + offset_um) / s)))


def pinhole_transmission(dx_um: float, dy_um: float, sigma_um: float,
                         diameter_um: float = PINHOLE_DIAMETER_UM) -> float:
    """Approximate fraction of a round Gaussian beam through a round pinhole
    whose centre is (dx, dy) from the beam centre."""
    r = diameter_um / 2.0
    centred = 1.0 - math.exp(-(r * r) / (2.0 * sigma_um * sigma_um))
    spread = sigma_um * sigma_um + (r * r) / 4.0
    return centred * math.exp(-(dx_um * dx_um + dy_um * dy_um) / (2.0 * spread))


def _deflection_um(pitch_error_urad: float, mirror: str, at: str) -> float:
    """Beam displacement at ``at`` from a mirror pitch error (2x angle x lever arm)."""
    lever_m = POSITIONS[at] - POSITIONS[mirror]
    return 2.0 * pitch_error_urad * lever_m if lever_m > 0 else 0.0


def _get(state: dict[str, Any], key: str, default: float = 0.0) -> float:
    if key in state:
        return float(state[key])
    return float(DEFAULT_TRUTH.get(key, default))


def beam_offsets(state: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """(x, y) beam-centre offsets in um at the slits, pinhole and sample.
    M101 steers horizontally; M111 and M112 steer vertically."""
    m101 = _get(state, "M101_pitch") - _get(state, "M101_pitch_offset_urad")
    m111 = _get(state, "M111_pitch") - _get(state, "M111_pitch_offset_urad")
    m112 = _get(state, "M112_pitch") - _get(state, "M112_pitch_offset_urad")
    out = {}
    for at in ("DIAG102", "XS111", "pinhole", "sample"):
        x = _deflection_um(m101, "M101", at)
        y = _deflection_um(m111, "M111", at)
        if at in ("pinhole", "sample"):
            # M112 refocuses the exit slit onto the sample: upstream offsets
            # are demagnified, its own pitch error steers the spot.
            x *= 0.25
            y = 0.25 * y + _deflection_um(m112, "M112", at)
        out[at] = (x, y)
    return out


def flux_profile(state: dict[str, Any]) -> dict[str, float]:
    """Photon flux (ph/s) arriving at each diagnostic point, given the
    state of every device. Paddles and shutters block what is downstream."""
    energy = _get(state, "mono_energy", 745.0)
    ring = _get(state, "ring_current", RING_NOMINAL_MA)
    flux = SOURCE_FLUX * (ring / RING_NOMINAL_MA) * source_spectrum(
        energy, _get(state, "epu_gap", epu_gap_for(745.0, 3)), int(_get(state, "epu_harmonic", 3))
    )
    profile: dict[str, float] = {}
    off = beam_offsets(state)

    def blocked(key: str) -> bool:
        return not bool(state.get(key, True))

    if blocked("fe_shutter_open"):
        flux = 0.0
    sx, sy = BEAM_SIGMA_UM["mask"]
    flux *= slit_transmission(MASK_SIZE_UM[0], 0.0, sx) * slit_transmission(MASK_SIZE_UM[1], 0.0, sy)
    flux *= MIRROR_REFLECTIVITY  # M101
    profile["DIAG101"] = flux
    if bool(state.get("DIAG101_in", False)):
        flux = 0.0
    flux *= MIRROR_REFLECTIVITY * GRATING_EFFICIENCY  # M102 + grating
    sx, sy = BEAM_SIGMA_UM["DIAG102"]
    ox, oy = off["DIAG102"]
    flux *= slit_transmission(_get(state, "DIAG102_hgap", 10000.0), ox, sx)
    flux *= slit_transmission(_get(state, "DIAG102_vgap", 10000.0), oy, sy)
    if blocked("beamline_shutter_open"):
        flux = 0.0
    flux *= MIRROR_REFLECTIVITY  # M111
    profile["DIAG111"] = flux
    if bool(state.get("DIAG111_in", False)):
        flux = 0.0
    sx, sy = BEAM_SIGMA_UM["XS111"]
    ox, oy = off["XS111"]
    flux *= slit_transmission(_get(state, "XS111_hgap", 68.0), ox, sx)
    flux *= slit_transmission(_get(state, "XS111_vgap", 35.0), oy, sy)
    flux *= MIRROR_REFLECTIVITY  # M112
    profile["DIAG112"] = flux
    flux *= 1.0 - I0_MESH_FRACTION
    px, py = off["pinhole"]
    flux *= pinhole_transmission(
        _get(state, "PinholeX") - px, _get(state, "PinholeY") - py, BEAM_SIGMA_UM["pinhole"][0]
    )
    profile["sample"] = flux
    profile["detector"] = flux * sample_transmission(state, off["sample"][1])
    return profile


def sample_transmission(state: dict[str, Any], beam_y_um: float = 0.0) -> float:
    """Fraction of the beam that passes the sample to the detector diode.

    Reflection geometry, direct-beam detector: raising ``sample_lift`` pushes
    the sample up into the beam (a falling edge, the half-cut). A tilted
    sample sticks up into the beam by (L/2)·sin|θ−θ0| more, so at the
    half-cut a theta scan peaks where the surface is parallel to the beam.
    If ``sample_translate`` has drifted so the beam misses the sample
    across, nothing is blocked."""
    lift = _get(state, "sample_lift")
    theta = _get(state, "sample_rotate_steppertheta")
    translate = _get(state, "sample_translate")
    if abs(translate - _get(state, "sample_center_um")) > SAMPLE_WIDTH_UM / 2.0:
        return 1.0
    tilt = math.radians(abs(theta - _get(state, "sample_tilt_deg")))
    top = (lift - _get(state, "sample_surface_um")) + (SAMPLE_LENGTH_UM / 2.0) * math.sin(tilt)
    sigma = BEAM_SIGMA_UM["sample"][1]
    return 0.5 * math.erfc((top - beam_y_um) / (math.sqrt(2.0) * sigma))


def photocurrent_na(flux_ph_s: float, energy_ev: float) -> float:
    """Silicon photodiode current (nA) for ``flux_ph_s`` at ``energy_ev``."""
    return flux_ph_s * (energy_ev / SI_PAIR_EV) * ELECTRON * 1e9


def diode_readings(state: dict[str, Any]) -> dict[str, float]:
    """Noise-free diode currents (nA): DIAG101 / DIAG111 read only with their
    paddle in; DIAG112 is the I0 mesh; DetectorDiodeCurrent sees the beam
    after the sample."""
    energy = _get(state, "mono_energy", 745.0)
    prof = flux_profile(state)
    return {
        "DIAG101_Diode": DIODE_DARK_NA + (photocurrent_na(prof["DIAG101"], energy)
                                          if state.get("DIAG101_in") else 0.0),
        "DIAG111_Diode": DIODE_DARK_NA + (photocurrent_na(prof["DIAG111"], energy)
                                          if state.get("DIAG111_in") else 0.0),
        "DIAG112_Diode": DIODE_DARK_NA + I0_MESH_FRACTION * photocurrent_na(prof["DIAG112"], energy),
        "DetectorDiodeCurrent": DIODE_DARK_NA + photocurrent_na(prof["detector"], energy),
    }

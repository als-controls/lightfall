"""Physics of the COSMIC-Scattering sim beam model."""

import pytest

from lightfall.devices.sim import cosmic_model as m


def _base(**kw):
    h = m.best_harmonic(745.0)
    off = m.beam_offsets({})
    s = {"mono_energy": 745.0, "epu_gap": m.epu_gap_for(745.0, h), "epu_harmonic": h,
         "PinholeX": off["pinhole"][0], "PinholeY": off["pinhole"][1]}
    s.update(kw)
    return s


def det(**kw):
    return m.diode_readings(_base(**kw))["DetectorDiodeCurrent"]


def test_aligned_beamline_reads_about_15000_nA():
    assert det() == pytest.approx(15000, rel=0.05)


def test_epu_tracking_and_harmonics():
    assert m.epu_fundamental_ev(m.EPU_REF_GAP_MM) == m.EPU_REF_E1_EV
    for e in (300.0, 450.0, 745.0, 1200.0):
        h = m.best_harmonic(e)
        assert h * m.epu_fundamental_ev(m.epu_gap_for(e, h)) == pytest.approx(e)
    assert det(mono_energy=790.0) < 0.1 * det()  # mono walked off the EPU harmonic


def test_slit_transmission_limits():
    assert m.slit_transmission(1e9, 0.0, 10.0) == pytest.approx(1.0)
    assert m.slit_transmission(0.0, 0.0, 10.0) == 0.0
    assert m.slit_transmission(20.0, 0.0, 10.0) < m.slit_transmission(40.0, 0.0, 10.0)
    assert m.slit_transmission(40.0, 30.0, 10.0) < m.slit_transmission(40.0, 0.0, 10.0)


def test_mirror_pitch_moves_the_spot_off_the_pinhole():
    assert det(M112_pitch=5.0) < 0.2 * det()
    assert m.beam_offsets({"M112_pitch": 1.0})["sample"][1] != 0.0


def test_exit_slit_closing_cuts_flux():
    assert det(XS111_vgap=5.0) < 0.5 * det()


def test_lift_halfcut_is_a_falling_edge():
    lifts = [1150, 1200, 1225, 1250, 1300]
    values = [det(sample_lift=x) for x in lifts]
    assert values == sorted(values, reverse=True)
    assert values[0] > 0.9 * det() and values[-1] < 0.02 * det()


def test_theta_rocking_curve_peaks_at_the_sample_tilt():
    truth = m.DEFAULT_TRUTH
    at_halfcut = truth["sample_surface_um"] - 20.0
    tilt = truth["sample_tilt_deg"]
    peak = det(sample_lift=at_halfcut, sample_rotate_steppertheta=tilt)
    for dt in (-0.2, -0.1, 0.1, 0.2):
        assert det(sample_lift=at_halfcut, sample_rotate_steppertheta=tilt + dt) < peak


def test_translate_off_the_sample_means_nothing_blocks():
    high = m.DEFAULT_TRUTH["sample_surface_um"] + 200
    assert det(sample_lift=high) < 100
    assert det(sample_lift=high, sample_translate=m.SAMPLE_WIDTH_UM) == pytest.approx(det(), rel=0.01)


def test_shutters_and_paddles_block():
    assert det(fe_shutter_open=False) == pytest.approx(m.DIODE_DARK_NA)
    assert det(beamline_shutter_open=False) == pytest.approx(m.DIODE_DARK_NA)
    r = m.diode_readings(_base(DIAG101_in=True))
    assert r["DIAG101_Diode"] > 1000 and r["DetectorDiodeCurrent"] == pytest.approx(m.DIODE_DARK_NA)


def test_seeded_truth_is_reproducible(monkeypatch):
    from lightfall.devices.sim import cosmic_roster

    monkeypatch.setenv("LIGHTFALL_SIM_SEED", "7")
    assert cosmic_roster._truth() == cosmic_roster._truth()
    assert cosmic_roster._truth() != m.DEFAULT_TRUTH
    monkeypatch.delenv("LIGHTFALL_SIM_SEED")
    assert cosmic_roster._truth() == m.DEFAULT_TRUTH

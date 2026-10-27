"""Integrity tests for the mock beamline roster."""
from ophyd.sim import SynAxis

from lightfall.devices.backends.mock_roster import (
    BEAMLINE_SCOPE_METADATA,
    LEGACY_SYNOPTIC,
    create_roster,
)
from lightfall.devices.model import DeviceCategory
from lightfall.ui.panels.synoptic.models import BeamPathSegment, DeviceSynopticData


def _roster():
    existing = {
        "sample_x": SynAxis(name="sample_x", value=0.0),
        "sample_y": SynAxis(name="sample_y", value=0.0),
    }
    return create_roster(existing)


def test_all_roster_devices_have_valid_synoptic_data():
    for info in _roster():
        assert "synoptic" in info.metadata, info.name
        data = DeviceSynopticData.from_dict(info.metadata["synoptic"])
        assert data.visible, info.name


def test_roster_names_unique_and_ophyd_attached():
    infos = _roster()
    names = [i.name for i in infos]
    assert len(names) == len(set(names))
    for info in infos:
        assert info._ophyd_device is not None, info.name
        assert info._ophyd_device.name == info.name


def test_roster_covers_cosmic_scattering():
    names = {i.name for i in _roster()}
    expected = {
        "epu_gap", "epu_harmonic", "fe_shutter", "gv_1", "ig_1",
        "M101_pitch", "DIAG101", "DIAG101_Diode", "mono_energy", "beamline_energy",
        "DIAG102_hgap", "DIAG102_vgap", "beamline_shutter", "gv_2", "ig_2",
        "M111_pitch", "DIAG111", "DIAG111_Diode", "XS111_hgap", "XS111_vgap",
        "M112_pitch", "DIAG112_Diode", "PinholeX", "PinholeY",
        "sample_lift", "sample_translate", "sample_rotate_steppertheta",
        "DetectorDiodeCurrent", "temperature",
    }
    assert expected <= names


def test_categories_are_semantic():
    by_name = {i.name: i for i in _roster()}
    assert by_name["fe_shutter"].category == DeviceCategory.SHUTTER
    assert by_name["gv_1"].category == DeviceCategory.VALVE
    assert by_name["ig_1"].category == DeviceCategory.SENSOR
    assert by_name["DetectorDiodeCurrent"].category == DeviceCategory.DETECTOR
    assert by_name["temperature"].category == DeviceCategory.CONTROLLER


def test_positions_ordered_along_beam():
    by_name = {i.name: i for i in _roster()}
    order = ["epu_gap", "fe_shutter", "M101_pitch", "mono_energy", "M111_pitch",
             "XS111_hgap", "M112_pitch", "DIAG112_Diode", "PinholeX", "sample_lift",
             "DetectorDiodeCurrent"]
    xs = [by_name[n].metadata["synoptic"]["position"][0] for n in order]
    assert xs == sorted(xs)


def _read(dev):
    dev.trigger()
    return next(iter(dev.read().values()))["value"]


def test_diodes_respond_to_the_whole_beamline():
    by_name = {i.name: i._ophyd_device for i in _roster()}
    det = by_name["DetectorDiodeCurrent"]
    assert _read(det) > 5000  # beam on out of the box
    by_name["fe_shutter"].state.put("closed")
    assert _read(det) < 10  # front end closed: dark current only
    assert _read(by_name["DIAG112_Diode"]) < 10
    by_name["fe_shutter"].state.put("open")
    by_name["DIAG111"].state.put("closed")  # paddle in
    assert _read(by_name["DIAG111_Diode"]) > 1000
    assert _read(det) < 10


def test_beam_path_scope_metadata_deserializes():
    segments = BEAMLINE_SCOPE_METADATA["synoptic"]["beam_path"]
    assert len(segments) == 7
    for seg in segments:
        BeamPathSegment.from_dict(seg)


def test_legacy_synoptic_hides_legacy_devices():
    for name in ("motor", "det", "noisy_det", "sim_det", "pressure"):
        assert name in LEGACY_SYNOPTIC
        assert LEGACY_SYNOPTIC[name]["visible"] is False
    for name in ("sample_x", "sample_y", "ring_current"):
        assert LEGACY_SYNOPTIC[name]["visible"] is True


def test_slit_devices_use_double_rect_shapes():
    by_name = {i.name: i for i in _roster()}
    for name in ("DIAG102_hgap", "XS111_hgap"):
        assert by_name[name].metadata["synoptic"]["primitive_shape"] == "double_rect_h"
    for name in ("DIAG102_vgap", "XS111_vgap"):
        assert by_name[name].metadata["synoptic"]["primitive_shape"] == "double_rect_v"

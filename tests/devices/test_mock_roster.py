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


def test_roster_covers_expected_sections():
    names = {i.name for i in _roster()}
    expected = {
        "fe_shutter", "ps_shutter", "gv_1", "gv_2", "ig_1", "ig_2",
        "m1_pitch", "m1_bend", "mono_energy",
        "white_slits_hgap", "white_slits_vgap",
        "mono_slits_hgap", "mono_slits_vgap",
        "es_slits_hgap", "es_slits_vgap", "es_slits_hcen", "es_slits_vcen",
        "bpm_x", "bpm_y", "i0", "filter_wheel",
        "sample_z", "sample_theta", "temperature", "point_det",
    }
    assert expected <= names


def test_categories_are_semantic():
    by_name = {i.name: i for i in _roster()}
    assert by_name["fe_shutter"].category == DeviceCategory.SHUTTER
    assert by_name["gv_1"].category == DeviceCategory.VALVE
    assert by_name["ig_1"].category == DeviceCategory.SENSOR
    assert by_name["i0"].category == DeviceCategory.DETECTOR
    assert by_name["temperature"].category == DeviceCategory.CONTROLLER


def test_positions_ordered_along_beam():
    by_name = {i.name: i for i in _roster()}
    order = ["fe_shutter", "m1_pitch", "mono_energy", "i0", "point_det"]
    xs = [by_name[n].metadata["synoptic"]["position"][0] for n in order]
    assert xs == sorted(xs)


def test_i0_responds_to_shutters():
    by_name = {i.name: i for i in _roster()}
    i0 = by_name["i0"]._ophyd_device
    fe = by_name["fe_shutter"]._ophyd_device
    ps = by_name["ps_shutter"]._ophyd_device

    i0.trigger()
    assert i0.get() < 1.0  # shutters closed -> essentially no beam

    fe.state.put("open")
    ps.state.put("open")
    i0.trigger()
    assert i0.get() > 10.0  # beam on


def test_beam_path_scope_metadata_deserializes():
    segments = BEAMLINE_SCOPE_METADATA["synoptic"]["beam_path"]
    assert len(segments) == 3
    for seg in segments:
        BeamPathSegment.from_dict(seg)


def test_legacy_synoptic_hides_legacy_devices():
    for name in ("motor", "det", "noisy_det", "sim_det", "pressure"):
        assert name in LEGACY_SYNOPTIC
        assert LEGACY_SYNOPTIC[name]["visible"] is False
    for name in ("sample_x", "sample_y", "ring_current"):
        assert LEGACY_SYNOPTIC[name]["visible"] is True

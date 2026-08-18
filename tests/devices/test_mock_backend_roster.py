"""Tests for MockBackend with the beamline roster integrated."""
from collections import Counter

from lightfall.devices.backends.mock import MockBackend
from lightfall.devices.model import DeviceCategory, DeviceInfo
from lightfall.devices.sim.actuators import SimTemperatureController
from lightfall.ui.panels.synoptic.models import DeviceSynopticData


def test_all_devices_have_synoptic_metadata():
    backend = MockBackend()
    infos = backend.load_metadata()
    assert len(infos) >= 40
    for info in infos:
        assert "synoptic" in info.metadata, info.name
        DeviceSynopticData.from_dict(info.metadata["synoptic"])


def test_roster_devices_present_and_instantiable():
    backend = MockBackend()
    infos = {i.name: i for i in backend.load_metadata()}
    for name in ("fe_shutter", "mono_energy", "i0", "point_det", "sample_theta"):
        assert name in infos
        assert backend.instantiate(infos[name]) is not None


def test_temperature_is_now_a_controller():
    backend = MockBackend()
    infos = {i.name: i for i in backend.load_metadata()}
    obj = backend.instantiate(infos["temperature"])
    assert isinstance(obj, SimTemperatureController)
    reading = obj.read()
    assert any(k.startswith("temperature") for k in reading)


def test_legacy_devices_hidden_but_present():
    backend = MockBackend()
    infos = {i.name: i for i in backend.load_metadata()}
    for name in ("motor", "det", "noisy_det", "sim_det"):
        assert name in infos
        assert infos[name].metadata["synoptic"]["visible"] is False
    assert infos["sample_x"].metadata["synoptic"]["visible"] is True


def test_device_names_are_globally_unique():
    backend = MockBackend()
    infos = backend.load_metadata()
    counts = Counter(info.name for info in infos)
    duplicates = {name: n for name, n in counts.items() if n > 1}
    assert not duplicates, f"duplicate device names: {duplicates}"


def test_add_device_before_load_does_not_starve_instantiate():
    """Regression: add_device() before load_metadata() must not prevent instantiate()."""
    backend = MockBackend()
    backend.add_device(DeviceInfo(name="early_bird", category=DeviceCategory.CONTROLLER))
    infos = {i.name: i for i in backend.load_metadata()}
    assert "motor" in infos
    assert backend.instantiate(infos["motor"]) is not None


def test_repeated_create_calls_do_not_duplicate():
    """Regression: calling _create_simulated_devices() twice must not duplicate devices."""
    backend = MockBackend()
    n1 = len(backend.load_metadata())
    backend._create_simulated_devices()
    n2 = len(backend.load_metadata())
    assert n1 == n2

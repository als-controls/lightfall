"""RemoteControlService device verbs against ophyd.sim devices."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from ophyd.sim import SynAxis, SynGauss

from lightfall.devices.model import DeviceCategory, DeviceInfo
from lightfall.ipc.service import IPCService
from lightfall.remote.service import RemoteControlService


class _FakeCatalog:
    def __init__(self, devices):
        # devices: list[tuple[DeviceInfo, ophyd_obj]]
        self._infos = {info.name: info for info, _ in devices}
        self._ophyd = {info.name: obj for info, obj in devices}

    def list_devices(self, category=None, beamline=None, active_only=True):
        return list(self._infos.values())

    def get_device_by_name(self, name):
        return self._infos.get(name)

    def get_ophyd_device(self, name):
        return self._ophyd.get(name)


def _make_ipc():
    ipc = IPCService.__new__(IPCService)
    ipc._topic_prefix = "als.test"
    ipc._subscriptions = {}
    ipc._action_catalog = {}
    ipc._event_catalog = {}
    ipc._trusted_actions = {}
    ipc._session_channels = {}
    ipc._trust = None
    ipc._instance_id = "t"
    ipc._display_name = None
    ipc._loop = None
    ipc._nc = None
    ipc._connected = False
    ipc._connected_lock = threading.Lock()
    sent = []
    ipc.publish = lambda subject, data: sent.append((subject, data))  # type: ignore[method-assign]
    return ipc, sent


class _FakeEngine:
    def __init__(self):
        self.is_idle = True

    def get_current_procedure(self):
        return None

    def get_queue_items(self):
        return []

    class _Sig:
        def connect(self, *_):
            pass

    sigOutput = _Sig()
    sigFinish = _Sig()
    sigAbort = _Sig()
    sigException = _Sig()
    sigStateChanged = _Sig()


@pytest.fixture
def dev_svc(qapp):
    motor = SynAxis(name="sim_motor")
    det = SynGauss("sim_det", motor, "sim_motor", center=0, Imax=1, sigma=1)
    devices = [
        (
            DeviceInfo(
                name="sim_motor",
                category=DeviceCategory.MOTOR,
                device_class="ophyd.sim.SynAxis",
                beamline="7.0.1.1",
                tags=["sample"],
            ),
            motor,
        ),
        (
            DeviceInfo(
                name="sim_det",
                category=DeviceCategory.DETECTOR,
                device_class="ophyd.sim.SynGauss",
                beamline="7.0.1.1",
            ),
            det,
        ),
    ]
    ipc, sent = _make_ipc()
    service = RemoteControlService(ipc, engine=_FakeEngine(), catalog=_FakeCatalog(devices))
    service.start()
    yield SimpleNamespace(ipc=ipc, sent=sent, service=service, motor=motor)
    service.stop()


def _invoke(svc, suffix, data, reply="_INBOX.d"):
    svc.ipc._trusted_actions[suffix].callback(suffix, data, reply)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        replies = [d for s, d in svc.sent if s == reply]
        if replies:
            return replies[-1]
        time.sleep(0.01)
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()
    raise AssertionError(f"No reply for {suffix}")


class TestSearch:
    def test_empty_filter_lists_all_names(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.search", {})
        assert sorted(reply["devices"]) == ["sim_det", "sim_motor"]
        assert reply["contract_version"] == 1

    def test_filter_by_category(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.search", {"category": "motor"})
        assert reply["devices"] == ["sim_motor"]

    def test_filter_by_name(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.search", {"name": "sim_det"})
        assert reply["devices"] == ["sim_det"]

    def test_filter_by_tag(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.search", {"tags": "sample"})
        assert reply["devices"] == ["sim_motor"]

    def test_no_match_empty(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.search", {"name": "nope"})
        assert reply["devices"] == []


class TestInfo:
    def test_info_fields(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.info", {"device": "sim_motor"})
        assert reply == {
            "name": "sim_motor",
            "category": "motor",
            "device_class": "ophyd.sim.SynAxis",
            "contract_version": 1,
        }

    def test_unknown_device(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.info", {"device": "nope"})
        assert reply["code"] == "unknown"

    def test_missing_device_field(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.info", {})
        assert reply["code"] == "bad_request"


class TestComponents:
    def test_motor_components(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.components", {"device": "sim_motor"})
        comps = {c["name"]: c for c in reply["components"]}
        assert "readback" in comps
        assert "setpoint" in comps
        assert comps["setpoint"]["writable"] is True
        assert isinstance(comps["readback"]["type"], str)

    def test_unknown_device(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.components", {"device": "nope"})
        assert reply["code"] == "unknown"


class TestGet:
    def test_get_default_readback(self, dev_svc):
        dev_svc.motor.set(3.5).wait(timeout=5)
        reply = _invoke(dev_svc, "commands.device.get", {"device": "sim_motor"})
        assert reply["value"] == pytest.approx(3.5)
        assert isinstance(reply["timestamp"], float)

    def test_get_named_signal(self, dev_svc):
        reply = _invoke(
            dev_svc, "commands.device.get", {"device": "sim_motor", "signal": "velocity"}
        )
        assert "value" in reply

    def test_get_unknown_signal(self, dev_svc):
        reply = _invoke(
            dev_svc, "commands.device.get", {"device": "sim_motor", "signal": "warp_drive"}
        )
        assert reply["code"] == "unknown"

    def test_get_unknown_device(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.get", {"device": "nope"})
        assert reply["code"] == "unknown"


class TestPut:
    def test_put_wait_true_replies_on_completion(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.put", {"device": "sim_motor", "value": 1.25})
        assert reply["status"] == "ok"
        assert reply["value"] == pytest.approx(1.25)
        assert dev_svc.motor.readback.get() == pytest.approx(1.25)

    def test_put_wait_false_replies_accepted(self, dev_svc):
        reply = _invoke(
            dev_svc,
            "commands.device.put",
            {"device": "sim_motor", "value": 2.0, "wait": False},
        )
        assert reply["status"] == "accepted"

    def test_put_busy_engine_rejected(self, dev_svc):
        dev_svc.service._engine.is_idle = False
        reply = _invoke(dev_svc, "commands.device.put", {"device": "sim_motor", "value": 9})
        assert reply["code"] == "busy"

    def test_put_missing_value_bad_request(self, dev_svc):
        reply = _invoke(dev_svc, "commands.device.put", {"device": "sim_motor"})
        assert reply["code"] == "bad_request"

    def test_put_behavior_queue_unsupported(self, dev_svc):
        reply = _invoke(
            dev_svc,
            "commands.device.put",
            {"device": "sim_motor", "value": 1, "behavior": "queue"},
        )
        assert reply["code"] == "bad_request"

    def test_put_timeout(self, dev_svc):
        import ophyd.sim

        slow = ophyd.sim.SynAxis(name="slow_motor", delay=5.0)
        dev_svc.service._catalog._infos["slow_motor"] = __import__(
            "lightfall.devices.model", fromlist=["DeviceInfo"]
        ).DeviceInfo(name="slow_motor", device_class="ophyd.sim.SynAxis")
        dev_svc.service._catalog._ophyd["slow_motor"] = slow
        reply = _invoke(
            dev_svc,
            "commands.device.put",
            {"device": "slow_motor", "value": 1.0, "timeout_s": 0.2},
        )
        assert reply["code"] == "timeout"

    def test_put_readonly_signal_rejected(self, dev_svc):
        import ophyd.sim

        ro_sig = ophyd.sim.SynSignalRO(name="readonly_sig")
        dev_svc.service._catalog._infos["ro_device"] = __import__(
            "lightfall.devices.model", fromlist=["DeviceInfo"]
        ).DeviceInfo(name="ro_device", device_class="ophyd.sim.SynSignalRO")
        dev_svc.service._catalog._ophyd["ro_device"] = ro_sig
        reply = _invoke(
            dev_svc,
            "commands.device.put",
            {"device": "ro_device", "value": 5},
        )
        assert reply["code"] == "limits"

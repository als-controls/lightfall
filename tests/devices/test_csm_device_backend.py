"""CSMDeviceBackend: CSM instantiables -> Lightfall devices -> ophyd objects."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap

import httpx
import ophyd
import pytest

from lightfall.auth.service_key_auth import StaticApiKeyAuth
from lightfall.devices.backends.csm import CSMDeviceBackend

BASE_URL = "https://csm.test"

ACR_ENTRY = {
    "id": 101,
    "name": "bl701_m1",
    "instantiators": [
        {
            "system": "python",
            "target": "ophyd.EpicsMotor",
            "args": ["{{prefix}}"],
            "kwargs": {"name": "{{name}}"},
        }
    ],
    "fields": {"prefix": "BL701:ACR1:m1"},
    "documentation": None,
    "active": True,
    "origin": "manifest",
    "is_synced": True,
    "ioc_id": 34,
    "ioc_name": "acr9000-7011",
    "beamline_name": "7.0.1.1",
    "ioc_runtime_status": "running",
}


def _backend(**kw) -> CSMDeviceBackend:
    kw.setdefault("beamline_id", 12)
    return CSMDeviceBackend(BASE_URL, StaticApiKeyAuth("session-key"), name="csm-7011", **kw)


def test_base_params_from_row_config():
    b = _backend(ioc_id=34, name_filter="m.*", regex=True, include_inactive=True)
    assert b.base_params == {
        "beamline_id": 12,
        "ioc_id": 34,
        "name": "m.*",
        "regex": "true",
        "include_inactive": "true",
    }


def test_minimal_base_params():
    assert _backend().base_params == {"beamline_id": 12, "include_inactive": "false"}


def test_not_editable_and_named():
    b = _backend()
    assert b.is_editable is False
    assert b.name == "csm-7011"


def test_load_metadata_sends_filters_and_token(httpx_mock):
    def _callback(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Apikey session-key"
        assert request.url.params["beamline_id"] == "12"
        assert request.url.params["include_inactive"] == "false"
        return httpx.Response(200, json=[ACR_ENTRY])

    httpx_mock.add_callback(_callback, is_reusable=True)
    infos = _backend().load_metadata()
    assert [i.name for i in infos] == ["bl701_m1"]
    assert infos[0].prefix == "BL701:ACR1:m1"
    assert infos[0].device_class == "ophyd.EpicsMotor"


def test_instantiate_renders_happi_templates(httpx_mock, monkeypatch):
    """happi resolves {{prefix}}/{{name}} and constructs the target class."""
    calls = []

    class _RecordingMotor:
        def __init__(self, prefix, *, name):
            calls.append((prefix, name))
            self.prefix, self.name = prefix, name

    # The suite runs ophyd on its dummy control layer, where EpicsMotor can't
    # be constructed; the real class is covered by the subprocess test below.
    monkeypatch.setattr(ophyd, "EpicsMotor", _RecordingMotor)
    httpx_mock.add_response(json=[ACR_ENTRY], is_reusable=True)
    backend = _backend()
    (info,) = backend.load_metadata()
    device = backend.instantiate(info)
    assert isinstance(device, _RecordingMotor)
    assert calls == [("BL701:ACR1:m1", "bl701_m1")]


def test_401_propagates_from_load_metadata(httpx_mock):
    httpx_mock.add_response(status_code=401)
    with pytest.raises(PermissionError, match="minted at login"):
        _backend().load_metadata()


@pytest.mark.timeout(60)
def test_real_epicsmotor_instantiates_without_network():
    """End to end on the caproto layer, isolated in a subprocess.

    Proves the ACR9000 instantiable becomes an unconnected ophyd.EpicsMotor
    with no IOC reachable, without polluting this process's control layer.
    """
    script = textwrap.dedent(
        f"""
        import json, os, httpx
        from lightfall.devices.backends.csm import CSMDeviceBackend
        entry = json.loads({json.dumps(json.dumps(ACR_ENTRY))})
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[entry]))
        b = CSMDeviceBackend("{BASE_URL}", None, beamline_id=12,
                             name="csm-7011", transport=transport)
        (info,) = b.load_metadata()
        dev = b.instantiate(info)
        print(type(dev).__module__, type(dev).__name__, dev.prefix, dev.name, dev.connected)
        os._exit(0)
        """
    )
    env = {
        **os.environ,
        "OPHYD_CONTROL_LAYER": "caproto",
        "EPICS_CA_AUTO_ADDR_LIST": "NO",
        "EPICS_CA_ADDR_LIST": "127.0.0.1",
    }
    out = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-5:] == [
        "ophyd.epics_motor",
        "EpicsMotor",
        "BL701:ACR1:m1",
        "bl701_m1",
        "False",
    ]


def test_synoptic_move_through_real_update_device_sends_one_put(httpx_mock):
    """The synoptic view's real save path: metadata['synoptic'] change -> one PUT /display."""
    import json as _json

    httpx_mock.add_response(
        url=re.compile(r".*/api/instantiables\?.*"), json=[ACR_ENTRY], is_reusable=True
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=bl701_m1&include_inactive=true",
        json=ACR_ENTRY,
        is_reusable=True,
    )
    puts = []

    def _put(request):
        puts.append(_json.loads(request.content))
        return httpx.Response(200, json=ACR_ENTRY)

    httpx_mock.add_callback(
        _put, method="PUT", url=f"{BASE_URL}/api/instantiables/101/display", is_reusable=True
    )
    backend = _backend()
    assert backend.connect()
    (info,) = backend.list_devices()
    info.metadata["synoptic"] = {"x": 10, "y": 20}
    assert backend.update_device(info) is True
    assert puts == [{"display": {"synoptic": {"x": 10, "y": 20}}}]


def test_prefix_edit_refused_and_sends_nothing(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/api/instantiables\?.*"), json=[ACR_ENTRY], is_reusable=True
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=bl701_m1&include_inactive=true",
        json=ACR_ENTRY,
        is_reusable=True,
    )
    backend = _backend()
    assert backend.connect()
    (info,) = backend.list_devices()
    info.prefix = "OTHER:m1"
    assert backend.update_device(info) is False  # PermissionError logged, no PUT/PATCH sent


def test_remove_device_sends_nothing(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/api/instantiables\?.*"), json=[ACR_ENTRY], is_reusable=True
    )
    backend = _backend()
    assert backend.connect()
    (info,) = backend.list_devices()
    assert backend.remove_device(info.id) is False


def test_scope_metadata_first_save_creates(httpx_mock):
    import json as _json

    httpx_mock.add_response(url=re.compile(r".*/api/instantiables\?.*"), json=[], is_reusable=True)
    name = CSMDeviceBackend._scope_item_name("7.0.1.1")
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name={name}&include_inactive=true",
        status_code=404,
        is_reusable=True,
    )
    posts = []

    def _post(request):
        posts.append(_json.loads(request.content))
        return httpx.Response(201, json={**ACR_ENTRY, "name": name, "ioc_id": None})

    httpx_mock.add_callback(_post, method="POST", url=f"{BASE_URL}/api/instantiables")
    backend = _backend()
    assert backend.connect()
    assert backend.update_scope_metadata("7.0.1.1", {"beam_path": [1, 2]}) is True
    assert posts[0]["display"] == {
        "lightfall_scope": "7.0.1.1",
        "scope_metadata": {"beam_path": [1, 2]},
    }


def test_scope_metadata_second_save_updates(httpx_mock):
    import json as _json

    name = CSMDeviceBackend._scope_item_name("7.0.1.1")
    existing = {
        **ACR_ENTRY,
        "id": 555,
        "name": name,
        "ioc_id": None,
        "instantiators": [],
        "fields": {},
        "display": {"lightfall_scope": "7.0.1.1", "scope_metadata": {"beam_path": [1]}},
    }
    httpx_mock.add_response(url=re.compile(r".*/api/instantiables\?.*"), json=[], is_reusable=True)
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name={name}&include_inactive=true",
        json=existing,
        is_reusable=True,
    )
    puts = []

    def _put(request):
        puts.append(_json.loads(request.content))
        return httpx.Response(200, json=existing)

    httpx_mock.add_callback(_put, method="PUT", url=f"{BASE_URL}/api/instantiables/555/display")
    backend = _backend()
    assert backend.connect()
    assert backend.get_scope_metadata("7.0.1.1") == {"beam_path": [1]}
    assert backend.update_scope_metadata("7.0.1.1", {"beam_path": [1, 2]}) is True
    assert puts == [{"display": {"scope_metadata": {"beam_path": [1, 2]}}}]

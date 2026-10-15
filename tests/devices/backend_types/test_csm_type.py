"""CSM backend type: lookups, config, build, and the settings editor."""
from __future__ import annotations

import httpx
import pytest

from lightfall.devices.backend_types import csm as csm_type
from lightfall.devices.backend_types.base import BuildContext
from lightfall.devices.backend_types.csm import CSMBackendType, fetch_beamlines, fetch_iocs
from lightfall.devices.backends.csm import CSMDeviceBackend

URL = "https://csm.test"

SAVED = {
    "url": URL,
    "beamline_id": 12,
    "beamline_name": "7.0.1.1",
    "ioc_id": 34,
    "ioc_name": "acr9000-7011",
    "name_filter": "",
    "regex": False,
    "include_inactive": False,
}


@pytest.fixture(autouse=True)
def _logged_out(monkeypatch):
    monkeypatch.setattr(csm_type, "session_token", lambda: None)


def test_fetch_beamlines(httpx_mock):
    def _cb(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/admin/beamlines"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=[{"id": 2, "name": "7.0.1.1"}, {"id": 1, "name": "6.0.1"}])

    httpx_mock.add_callback(_cb)
    assert fetch_beamlines(URL, "tok") == [(1, "6.0.1"), (2, "7.0.1.1")]


def test_fetch_iocs_distinct_sorted(httpx_mock):
    def _cb(request: httpx.Request) -> httpx.Response:
        assert request.url.params["beamline_id"] == "12"
        return httpx.Response(200, json=[
            {"ioc_id": 34, "ioc_name": "acr9000-7011"},
            {"ioc_id": 34, "ioc_name": "acr9000-7011"},
            {"ioc_id": 9, "ioc_name": "adsim"},
            {"ioc_id": None, "ioc_name": None},
        ])

    httpx_mock.add_callback(_cb)
    assert fetch_iocs(URL, "tok", 12) == [(34, "acr9000-7011"), (9, "adsim")]


def test_type_basics():
    t = CSMBackendType()
    assert t.type_id == "csm"
    assert t.requires_auth is True
    cfg = t.default_config()
    assert cfg["url"] == "https://csm.als.lbl.gov"
    assert cfg["beamline_id"] is None
    assert t.validate(cfg) == ["Beamline is required"]
    assert t.validate({**cfg, "url": "", "beamline_id": 1}) == ["CSM URL is required"]
    assert t.summary(SAVED) == "csm.test · 7.0.1.1 / acr9000-7011"
    assert t.summary({**SAVED, "ioc_id": None, "ioc_name": None}) == "csm.test · 7.0.1.1"


def test_build():
    backend = CSMBackendType().build(SAVED, "csm-7011", BuildContext("background", 4.0))
    assert isinstance(backend, CSMDeviceBackend)
    assert backend.name == "csm-7011"
    assert backend.base_params == {"beamline_id": 12, "ioc_id": 34, "include_inactive": "false"}


def test_registered():
    from lightfall.devices.backend_types.registry import BackendTypeRegistry

    BackendTypeRegistry.reset()
    try:
        assert BackendTypeRegistry.get_instance().get("csm") is not None
    finally:
        BackendTypeRegistry.reset()


def test_editor_offline_keeps_saved_selection(qtbot):
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    assert editor.get_config() == SAVED
    assert "Log in" in editor._status.text()
    assert not editor._beamline.isEnabled()


def test_editor_populates_and_keeps_selection(qtbot):
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    editor._on_beamlines([(1, "6.0.1"), (12, "7.0.1.1")])
    assert editor._beamline.isEnabled()
    assert editor.get_config()["beamline_id"] == 12
    editor._on_iocs([(9, "adsim"), (34, "acr9000-7011")])
    cfg = editor.get_config()
    assert (cfg["ioc_id"], cfg["ioc_name"]) == (34, "acr9000-7011")


def test_editor_saved_beamline_missing_from_list_is_kept(qtbot):
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    editor._on_beamlines([(1, "6.0.1")])
    assert editor.get_config()["beamline_id"] == 12


def test_editor_ioc_all_is_none(qtbot):
    editor = CSMBackendType().create_editor({**SAVED, "ioc_id": None, "ioc_name": None})
    qtbot.addWidget(editor)
    cfg = editor.get_config()
    assert cfg["ioc_id"] is None and cfg["ioc_name"] is None


def test_editor_fetch_error_shows_message(qtbot):
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    editor._on_fetch_error(PermissionError("CSM rejected the Lightfall token (401)"))
    assert "401" in editor._status.text()
    assert editor.get_config() == SAVED


def test_callback_after_editor_deleted_is_ignored(qtbot):
    from shiboken6 import delete

    editor = CSMBackendType().create_editor(SAVED)
    on_beamlines = editor._on_beamlines
    delete(editor)
    on_beamlines([(12, "7.0.1.1")])  # must not raise or crash


def test_editor_emits_changed_on_filter_edit(qtbot):
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    with qtbot.waitSignal(editor.changed, timeout=1000):
        editor._name_filter.setText("m1")

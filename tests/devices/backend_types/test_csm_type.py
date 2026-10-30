"""CSM backend type: lookups, config, build, and the settings editor."""

from __future__ import annotations

import httpx
import pytest

from lightfall.auth.service_key_auth import ServiceKeyAuth, StaticApiKeyAuth
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
    monkeypatch.setattr(csm_type, "csm_api_key", lambda url: None)


def test_fetch_beamlines(httpx_mock):
    def _cb(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/admin/beamlines"
        assert request.headers["Authorization"] == "Apikey tok"
        return httpx.Response(200, json=[{"id": 2, "name": "7.0.1.1"}, {"id": 1, "name": "6.0.1"}])

    httpx_mock.add_callback(_cb)
    assert fetch_beamlines(URL, StaticApiKeyAuth("tok")) == [(1, "6.0.1"), (2, "7.0.1.1")]


def test_fetch_iocs_distinct_sorted(httpx_mock):
    def _cb(request: httpx.Request) -> httpx.Response:
        assert request.url.params["beamline_id"] == "12"
        return httpx.Response(
            200,
            json=[
                {"ioc_id": 34, "ioc_name": "acr9000-7011"},
                {"ioc_id": 34, "ioc_name": "acr9000-7011"},
                {"ioc_id": 9, "ioc_name": "adsim"},
                {"ioc_id": None, "ioc_name": None},
            ],
        )

    httpx_mock.add_callback(_cb)
    assert fetch_iocs(URL, StaticApiKeyAuth("tok"), 12) == [(34, "acr9000-7011"), (9, "adsim")]


def test_type_basics():
    t = CSMBackendType()
    assert t.type_id == "csm"
    assert t.requires_auth is True
    cfg = t.default_config()
    assert cfg["url"] == "https://bcgmgmt.als.lbl.gov"
    assert cfg["beamline_id"] is None
    assert "bind it on the Scopes page" in t.validate(cfg)[0]
    assert t.validate({**cfg, "url": "", "beamline_id": 1}) == ["CSM URL is required"]
    assert t.summary(SAVED) == "csm.test · 7.0.1.1 / acr9000-7011"
    assert t.summary({**SAVED, "ioc_id": None, "ioc_name": None}) == "csm.test · 7.0.1.1"


def test_build():
    backend = CSMBackendType().build(SAVED, "csm-7011", BuildContext("background", 4.0))
    assert isinstance(backend, CSMDeviceBackend)
    assert backend.name == "csm-7011"
    assert backend.base_params == {"beamline_id": 12, "ioc_id": 34, "include_inactive": "false"}
    assert isinstance(backend._auth, ServiceKeyAuth)
    assert backend._auth._service == "csm:https://csm.test"


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


def _bound(monkeypatch, *names):
    monkeypatch.setattr("lightfall.scopes.site.binding_names", lambda: list(names))


def test_beamline_is_the_consoles_and_not_editable(qtbot, monkeypatch):
    _bound(monkeypatch, "7.0.1.1", "7.0.1")
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    editor._on_beamlines([(1, "6.0.1"), (12, "7.0.1.1")])
    assert not editor._beamline.isEnabled()
    assert editor.get_config()["beamline_id"] == 12
    editor._on_iocs([(9, "adsim"), (34, "acr9000-7011")])
    cfg = editor.get_config()
    assert (cfg["ioc_id"], cfg["ioc_name"]) == (34, "acr9000-7011")


def test_new_row_picks_the_branch_then_its_beamline(qtbot, monkeypatch):
    _bound(monkeypatch, "7.0.1.1", "7.0.1")
    fresh = CSMBackendType().default_config()
    editor = CSMBackendType().create_editor(fresh)
    qtbot.addWidget(editor)
    editor._on_beamlines([(11, "7.0.1"), (13, "8.3.2")])          # CSM has no 7.0.1.1
    assert (editor.get_config()["beamline_id"], editor.get_config()["beamline_name"]) == (
        11, "7.0.1")


def test_row_for_another_beamline_follows_the_console(qtbot, monkeypatch):
    _bound(monkeypatch, "8.3.2")
    editor = CSMBackendType().create_editor(SAVED)                 # saved for 7.0.1.1
    qtbot.addWidget(editor)
    editor._on_iocs([(34, "acr9000-7011")])
    editor._on_beamlines([(12, "7.0.1.1"), (13, "8.3.2")])
    cfg = editor.get_config()
    assert cfg["beamline_id"] == 13
    assert (cfg["ioc_id"], cfg["ioc_name"]) == (None, None)       # the old IOC is not 8.3.2's


def test_no_match_keeps_the_saved_beamline_and_says_why(qtbot, monkeypatch):
    # Opening an inherited row must not edit it.
    _bound(monkeypatch, "12.0.2.1", "12.0.2")
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    editor._on_beamlines([(1, "6.0.1")])
    assert editor.get_config() == SAVED
    assert "12.0.2.1" in editor._status.text()


def test_unbound_console_keeps_the_saved_beamline(qtbot, monkeypatch):
    _bound(monkeypatch)
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    editor._on_beamlines([(12, "7.0.1.1"), (13, "8.3.2")])
    assert editor.get_config() == SAVED
    assert "Bind this console" in editor._status.text()


def test_match_csm_beamline_is_loose_and_ordered():
    from lightfall.devices.backend_types.csm import match_csm_beamline

    items = [(11, "7.0.1"), (12, "7011")]
    assert match_csm_beamline(items, ["7.0.1.1", "7.0.1"]) == (12, "7011")
    assert match_csm_beamline(items, ["7.0.1.2", "7.0.1"]) == (11, "7.0.1")
    assert match_csm_beamline(items, ["12.0.2.1"]) is None


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


def test_token_never_passed_as_worker_arg(qtbot, monkeypatch):
    """QThreadFuture logs repr(args) on failure; the bearer must not be in them."""
    import lightfall.utils.threads as threads

    recorded = []

    class _Recorder:
        def __init__(self, fn, *args, **kwargs):
            recorded.append((fn, args, kwargs))

        def start(self):
            pass

    monkeypatch.setattr(threads, "QThreadFuture", _Recorder)
    monkeypatch.setattr(csm_type, "csm_api_key", lambda url: "secret-tok")
    monkeypatch.setattr(
        "lightfall.auth.session.SessionManager.get_api_key", lambda self, service: "secret-tok"
    )
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    assert recorded, "refresh() should start a fetch when logged in"
    for _fn, args, kwargs in recorded:
        assert "secret-tok" not in repr(args)
        assert "secret-tok" not in repr(kwargs)
        assert kwargs.get("log_exceptions") is False


def test_editor_logged_in_without_key_explains_relogin(qtbot, monkeypatch):
    monkeypatch.setattr(csm_type, "session_authenticated", lambda: True)
    editor = CSMBackendType().create_editor(SAVED)
    qtbot.addWidget(editor)
    assert "log out and back in" in editor._status.text()
    assert not editor._beamline.isEnabled()


def test_lookups_go_through_the_configured_proxy(monkeypatch):
    """bcgmgmt is only reachable through the user's SOCKS proxy off-site."""
    seen = {}

    def _fake_get(url, **kwargs):
        seen.update(kwargs)
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(csm_type, "csm_proxy", lambda url: "socks5://localhost:1080")
    monkeypatch.setattr(csm_type.httpx, "get", _fake_get)
    fetch_beamlines(URL, StaticApiKeyAuth("tok"))
    assert seen["proxy"] == "socks5://localhost:1080"


def test_build_passes_proxy_to_backend(monkeypatch):
    monkeypatch.setattr(csm_type, "csm_proxy", lambda url: "socks5://localhost:1080")
    backend = CSMBackendType().build(SAVED, "csm-7011", BuildContext("background", 4.0))
    assert backend._proxy == "socks5://localhost:1080"

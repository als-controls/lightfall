"""fetch_bootstrap / probe_health against a stub server; apply_bootstrap and the config layer."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from lightfall.setup import bootstrap as bs
from lightfall.setup.bootstrap import (
    Bootstrap,
    BootstrapError,
    NetworkError,
    apply_bootstrap,
    bootstrap_path,
    fetch_bootstrap,
    load_bootstrap_layer,
    probe_health,
)

FULL = {
    "schema": 1,
    "facility": {"id": "als", "name": "Advanced Light Source"},
    "auth": {"provider": "keycloak", "server_url": "https://kc.example", "realm": "als",
             "client_id": "lightfall"},
    "services": {"tiled_url": "https://tiled.example", "logbook_url": "http://lb.example:8000",
                 "ipc_nats_url": "nats://nats.example:4222"},
    "network": {"proxy_domains": ["*.lbl.gov"]},
    "agent": {"backend": "agy", "agy_gcp_project": "proj", "agy_gcp_location": "global"},
    "future_field": {"x": 1},
}


@pytest.fixture
def stub_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    routes: dict[str, tuple[int, bytes, float]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            status, body, delay = routes.get(self.path, (404, b"{}", 0.0))
            if delay:
                time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def route(path, payload, status=200, delay=0.0):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        routes[path] = (status, body, delay)

    yield f"http://127.0.0.1:{server.server_address[1]}", route
    server.shutdown()
    server.server_close()


def test_fetch_bootstrap_reads_the_well_known_document(stub_server):
    url, route = stub_server
    route("/.well-known/lightfall", FULL)
    b = fetch_bootstrap(url + "/", proxy=None)
    assert b.server_url == url
    assert b.facility_name == "Advanced Light Source"
    assert b.proxy_domains == ["*.lbl.gov"]
    assert b.summary() == ["sign-on", "Tiled", "logbook", "NATS", "assistant (Gemini)"]


@pytest.mark.parametrize("schema", [2, "1", True, None])
def test_schema_must_be_integer_one(stub_server, schema):
    url, route = stub_server
    route("/.well-known/lightfall", {"schema": schema})
    with pytest.raises(BootstrapError, match="schema"):
        fetch_bootstrap(url, proxy=None)


@pytest.mark.parametrize(("payload", "status", "match"), [
    (b"not json", 200, "not valid JSON"),
    (b"[1, 2]", 200, "not a JSON object"),
    (b"{}", 404, "HTTP 404"),
])
def test_bad_answers_raise_bootstrap_error(stub_server, payload, status, match):
    url, route = stub_server
    route("/.well-known/lightfall", payload, status=status)
    with pytest.raises(BootstrapError, match=match):
        fetch_bootstrap(url, proxy=None)


def test_fetch_times_out(stub_server):
    url, route = stub_server
    route("/.well-known/lightfall", FULL, delay=1.0)
    with pytest.raises(BootstrapError, match="no answer within 0.2 s"):
        fetch_bootstrap(url, proxy=None, timeout=0.2)


def test_probe_health(stub_server):
    url, route = stub_server
    route("/health", {"status": "ok"})
    probe_health(url, proxy=None)
    route("/health", {"status": "down"}, status=503)
    with pytest.raises(NetworkError, match="HTTP 503"):
        probe_health(url, proxy=None)


def test_probe_health_unreachable():
    with pytest.raises(NetworkError, match="could not connect|no answer"):
        probe_health("http://127.0.0.1:9", proxy=None, timeout=2.0)


def test_fetch_routes_through_the_configured_proxy(monkeypatch):
    from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

    seen: dict = {}

    def fake_proxy(url):
        seen["asked"] = url
        return "socks5://localhost:1080"

    class FakeClient:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, headers=None):
            seen["get"] = url
            return httpx.Response(200, json={"schema": 1}, request=httpx.Request("GET", url))

    monkeypatch.setattr(ProxySettingsProvider, "should_use_proxy_for_url",
                        staticmethod(fake_proxy))
    monkeypatch.setattr(httpx, "Client", FakeClient)
    fetch_bootstrap("http://bcglucidlogbook.dhcp.lbl.gov:8000")
    assert seen["asked"] == "http://bcglucidlogbook.dhcp.lbl.gov:8000"
    assert seen["kwargs"]["proxy"] == "socks5://localhost:1080"
    assert seen["kwargs"]["timeout"] == 10.0
    assert seen["get"] == "http://bcglucidlogbook.dhcp.lbl.gov:8000/.well-known/lightfall"


def test_explicit_proxy_bypasses_settings(monkeypatch):
    from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

    def boom(url):
        raise AssertionError("settings must not be consulted")

    monkeypatch.setattr(ProxySettingsProvider, "should_use_proxy_for_url", staticmethod(boom))
    assert bs.resolve_proxy("http://x.lbl.gov", "socks5://h:1") == "socks5://h:1"
    assert bs.resolve_proxy("http://x.lbl.gov", None) is None


def test_apply_bootstrap_maps_into_a_layer_at_priority_15(cfg):
    path = apply_bootstrap(Bootstrap("http://srv:8000", FULL), cfg)
    assert path == bootstrap_path(cfg) and path.exists()
    assert path == cfg.get_user_config_path().parent / "bootstrap.yaml"
    layer = cfg.layers.get_layer("bootstrap")
    assert layer.priority == 15 and layer.mutable is False
    names = [lyr.name for lyr in cfg.layers.layers()]
    assert names.index("global") < names.index("bootstrap") < names.index("user")
    provider = cfg.model.auth.provider
    assert (provider.type, provider.server_url, provider.realm, provider.client_id) == (
        "keycloak", "https://kc.example", "als", "lightfall")
    assert cfg.get("preferences.tiled_url") == "https://tiled.example"
    assert cfg.get("preferences.tiled_enabled") is True
    assert cfg.get("preferences.logbook_url") == "http://lb.example:8000"
    assert cfg.get("preferences.ipc_nats_url") == "nats://nats.example:4222"
    assert cfg.get("preferences.agent_backend") == "agy"
    assert cfg.get("preferences.agy_gcp_project") == "proj"
    assert cfg.get("preferences.agy_gcp_location") == "global"
    assert cfg.get("preferences.proxy_auto_detect_domains") == ["*.lbl.gov"]
    assert cfg.get("setup.facility.name") == "Advanced Light Source"
    assert cfg.get("setup.facility.server_url") == "http://srv:8000"
    assert cfg.get("future_field") is None


def test_user_yaml_overrides_the_bootstrap(cfg):
    cfg.set("preferences.tiled_url", "https://mine", persist=True)
    apply_bootstrap(Bootstrap("http://s", {"schema": 1,
                                           "services": {"tiled_url": "https://facility"}}), cfg)
    assert cfg.get("preferences.tiled_url") == "https://mine"


@pytest.mark.skipif(os.name != "nt", reason="PROGRAMDATA is the Windows global config dir")
def test_bootstrap_overrides_programdata(config_dirs):
    from lightfall.config import ConfigManager

    _, programdata = config_dirs
    (programdata / "ncs").mkdir(parents=True)
    (programdata / "ncs" / "application.yaml").write_text(
        "preferences:\n  tiled_url: https://global\n", encoding="utf-8")
    cm = ConfigManager()
    assert cm.get("preferences.tiled_url") == "https://global"
    apply_bootstrap(Bootstrap("http://s", {"schema": 1,
                                           "services": {"tiled_url": "https://facility"}}), cm)
    assert cm.get("preferences.tiled_url") == "https://facility"


def test_invalid_bootstrap_values_never_break_the_config_model(cfg):
    bad = {
        "schema": 1,
        "auth": {"provider": "oidc", "realm": 7, "server_url": ""},
        "services": {"tiled_url": ["x"], "Bad-Key": "v", "claude_api_key": "sk", 3: "x"},
        "agent": {"backend": "gpt", "agy_gcp_project": None},
        "network": {"proxy_domains": "*.LBL.gov"},
        "facility": "not a mapping",
    }
    apply_bootstrap(Bootstrap("http://s", bad), cfg)
    assert cfg.model.auth.provider.type == "local"
    assert cfg.validation_errors == []
    assert cfg.get("preferences.tiled_url") is None
    assert cfg.get("preferences.claude_api_key") is None
    assert cfg.get("preferences.agent_backend") is None
    assert cfg.get("preferences.agy_gcp_project") is None
    assert cfg.get("preferences.proxy_auto_detect_domains") == ["*.lbl.gov"]


def test_bootstrap_layer_reloads_at_startup(cfg, config_dirs):
    from lightfall.config import ConfigManager

    apply_bootstrap(Bootstrap("http://srv:8000", FULL), cfg)
    fresh = ConfigManager()
    assert fresh.layers.get_layer("bootstrap") is None
    assert load_bootstrap_layer(fresh) is True
    assert fresh.model.auth.provider.type == "keycloak"
    assert fresh.get("preferences.agent_backend") == "agy"


@pytest.mark.parametrize("text", ["- a\n- b\n", "auth: [unclosed\n", ""])
def test_unusable_bootstrap_file_is_ignored_at_startup(cfg, text):
    path = bootstrap_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert load_bootstrap_layer(cfg) is False
    assert cfg.layers.get_layer("bootstrap") is None
    assert cfg.model.auth.provider.type == "local"


def test_hand_edited_provider_type_is_dropped_on_load(cfg):
    path = bootstrap_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("auth:\n  provider:\n    type: oidc\n    realm: als\n", encoding="utf-8")
    assert load_bootstrap_layer(cfg) is True
    assert cfg.model.auth.provider.type == "local"
    assert cfg.model.auth.provider.realm == "als"
    assert cfg.validation_errors == []


def test_setup_services_loads_the_bootstrap_layer_first(monkeypatch):
    from lightfall import main as main_mod

    class _Stop(Exception):
        pass

    seen = []

    def fake_load(config):
        seen.append(config)
        raise _Stop

    monkeypatch.setattr(bs, "load_bootstrap_layer", fake_load)
    with pytest.raises(_Stop):
        main_mod._setup_services(object(), "CFG")
    assert seen == ["CFG"]

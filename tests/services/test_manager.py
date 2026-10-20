"""Tests for lightfall.services.manager.ServiceManager (local broker + local services)."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import pytest

import lightfall.services.manager as mgr_mod
from lightfall.services.manager import ServiceManager
from lightfall_utils.ipc.services.descriptor import ServiceDescriptor

ECHO_CMD = ("-m", "lightfall_utils.ipc.services.echo")
SCOPE = "{prefix}.host.{hostname}.{client_instance}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _patch_prefs(monkeypatch, values: dict):
    prefs = MagicMock()
    prefs.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    import lightfall.ui.preferences.manager as pm

    monkeypatch.setattr(pm.PreferencesManager, "get_instance", lambda: prefs)
    return prefs


def _echo_desc(**kw) -> ServiceDescriptor:
    return ServiceDescriptor(
        name="echo", scope=SCOPE, contract="1.0", launch="local", command=ECHO_CMD, **kw
    )


def _wait_up(qtbot, client, timeout=20000):
    qtbot.waitUntil(lambda: client.health == "up", timeout=timeout)


def _probe_ok(host, port, timeout=1.0):
    return {"server_id": "x"}


@pytest.fixture
def manager(qapp, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    port = _free_port()
    _patch_prefs(monkeypatch, {"services_local_port": port, "ipc_topic_prefix": "t9test"})
    m = ServiceManager()
    m.port_for_test = port
    yield m
    m.stop()


def test_register_and_lazy_start_local_echo(qtbot, manager, tmp_path):
    manager.register(_echo_desc())
    manager.start()
    # Local services start lazily: nothing before ensure_started.
    assert manager.client("echo") is None
    assert manager.statuses()[0]["health"] == "unknown"
    manager.ensure_started("echo")
    client = manager.client("echo")
    assert client is not None
    _wait_up(qtbot, client)
    reply = client.call("echo", {"x": 1}).result(timeout=5)
    assert reply["status"] == "ok" and reply["echo"] == {"x": 1}
    st = manager.statuses()[0]
    assert st["name"] == "echo" and st["health"] == "up"
    assert st["scope"].startswith("t9test.host.")
    assert st["version"]
    assert st["last_error"] == ""
    assert (tmp_path / "logs" / "services" / "echo.log").exists()
    # A second ensure_started is a no-op.
    manager.ensure_started("echo")
    assert manager.client("echo") is client


def test_reuses_existing_local_broker(qapp, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_prefs(monkeypatch, {"services_local_port": 4299})
    monkeypatch.setattr(mgr_mod, "probe_nats", _probe_ok)
    fake_server_cls = MagicMock()
    monkeypatch.setattr(mgr_mod, "LocalNatsServer", fake_server_cls)
    fake_client_cls = MagicMock()
    monkeypatch.setattr(mgr_mod, "QtServiceClient", fake_client_cls)
    m = ServiceManager()
    m.register(_echo_desc())
    m.ensure_started("echo")
    fake_server_cls.assert_not_called()
    fake_client_cls.assert_called_once()
    args, kwargs = fake_client_cls.call_args
    assert args[1] == "nats://127.0.0.1:4299"
    assert kwargs["tls"] is None
    assert kwargs["app_name"] == "lightfall"
    m.stop()
    fake_server_cls.return_value.stop.assert_not_called()


def test_local_command_and_scope(qapp, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_prefs(monkeypatch, {"services_local_port": 4299, "my_python": "C:/py/python.exe"})
    monkeypatch.setattr(mgr_mod, "probe_nats", _probe_ok)
    fake_client_cls = MagicMock()
    monkeypatch.setattr(mgr_mod, "QtServiceClient", fake_client_cls)
    m = ServiceManager()
    m.register(_echo_desc(interpreter_pref="my_python"))
    m.ensure_started("echo")
    kwargs = fake_client_cls.call_args.kwargs
    scope = kwargs["scope"]
    # An empty ipc_topic_prefix falls back to "lightfall".
    assert scope.startswith("lightfall.host.") and scope.endswith(m.client_instance)
    launcher = kwargs["launcher"]("tok123")
    assert launcher.session_token == "tok123"
    assert launcher._command == [
        "C:/py/python.exe", *ECHO_CMD, "--scope", scope,
        "--nats-url", "nats://127.0.0.1:4299", "--token-stdin",
    ]
    assert launcher._log_path == tmp_path / "logs" / "services" / "echo.log"
    m.stop()


def test_port_taken_by_non_nats(qapp, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(8)
    port = holder.getsockname()[1]
    _patch_prefs(monkeypatch, {"services_local_port": port})
    m = ServiceManager()
    try:
        m.register(_echo_desc())
        m.register(ServiceDescriptor(
            name="other", scope=SCOPE, contract="1.0", launch="local", command=ECHO_CMD))
        m.ensure_started("echo")
        for st in m.statuses():
            assert st["health"] == "down"
            assert st["last_error"] == f"local broker port {port} unavailable"
        assert m.client("echo") is None
    finally:
        m.stop()
        holder.close()


def test_stop_shuts_children_and_owned_broker(qtbot, manager):
    manager.register(_echo_desc())
    manager.ensure_started("echo")
    client = manager.client("echo")
    _wait_up(qtbot, client)
    launcher = client.launcher
    proc = launcher._proc
    server = manager._local_server
    assert server is not None and server.is_running()
    manager.stop()
    assert launcher.state == "stopped"
    assert proc.returncode is not None
    assert not server.is_running()
    assert manager._local_server is None
    assert mgr_mod.probe_nats("127.0.0.1", manager.port_for_test, timeout=0.5) is None
    assert manager.client("echo") is None


def test_stop_with_client_false_leaves_child(qapp, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_prefs(monkeypatch, {"services_local_port": 4299})
    monkeypatch.setattr(mgr_mod, "probe_nats", _probe_ok)
    fake_client = MagicMock()
    fake_launcher = MagicMock()
    fake_client._launcher = fake_launcher

    def _stop(*a, **k):
        # What QtServiceClient.stop would shut down.
        assert fake_client._launcher is None

    fake_client.stop.side_effect = _stop
    monkeypatch.setattr(mgr_mod, "QtServiceClient", MagicMock(return_value=fake_client))
    m = ServiceManager()
    m.register(_echo_desc(stop_with_client=False))
    m.ensure_started("echo")
    m.stop()
    fake_client.stop.assert_called_once()
    fake_launcher.stop.assert_not_called()


def test_external_service_uses_main_ipc_url_with_tls(qapp, monkeypatch):
    _patch_prefs(monkeypatch, {"ipc_nats_url": "nats://broker.example:4222",
                               "ipc_topic_prefix": "als.7011"})
    fake_client_cls = MagicMock()
    monkeypatch.setattr(mgr_mod, "QtServiceClient", fake_client_cls)
    m = ServiceManager()
    m.register(ServiceDescriptor(name="ext", scope="{prefix}", contract="1.0", launch="external"))
    m.start()
    args, kwargs = fake_client_cls.call_args
    assert args[1] == "nats://broker.example:4222"
    assert kwargs["scope"] == "als.7011"
    assert kwargs["tls"] is not None
    assert kwargs.get("launcher") is None
    fake_client_cls.return_value.start.assert_called_once()
    m.stop()
    fake_client_cls.return_value.stop.assert_called_once()


def test_external_local_url_has_no_tls(qapp, monkeypatch):
    _patch_prefs(monkeypatch, {"ipc_use_local_nats": True, "ipc_local_nats_port": 4300})
    fake_client_cls = MagicMock()
    monkeypatch.setattr(mgr_mod, "QtServiceClient", fake_client_cls)
    m = ServiceManager()
    m.start()
    m.register(ServiceDescriptor(name="ext", scope="{prefix}", contract="1.0", launch="external"))
    args, kwargs = fake_client_cls.call_args
    assert args[1] == "nats://127.0.0.1:4300"
    assert kwargs["tls"] is None
    m.stop()


def test_unknown_service_raises(qapp, monkeypatch):
    _patch_prefs(monkeypatch, {})
    m = ServiceManager()
    with pytest.raises(KeyError):
        m.ensure_started("nope")
    assert m.client("nope") is None


def test_register_emits_services_changed(qapp, qtbot, monkeypatch):
    _patch_prefs(monkeypatch, {})
    m = ServiceManager()
    with qtbot.waitSignal(m.services_changed, timeout=1000):
        m.register(_echo_desc())

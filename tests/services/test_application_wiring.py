"""LFApplication wiring for ServiceManager: registered, started after IPC, stopped first."""

from __future__ import annotations

from unittest.mock import MagicMock

from lightfall.core.application import LFApplication
from lightfall.core.services import ServiceRegistry
from lightfall.services.manager import ServiceManager
from lightfall_utils.ipc import IPCService


def test_register_core_services_includes_manager(qapp, monkeypatch):
    sentinel = MagicMock()
    monkeypatch.setattr(ServiceManager, "get_instance", classmethod(lambda cls: sentinel))
    app = LFApplication.__new__(LFApplication)
    app._services = ServiceRegistry()
    app._register_core_services()
    assert app._services.get(ServiceManager) is sentinel


def test_start_ipc_starts_manager_last():
    app = LFApplication.__new__(LFApplication)
    order: list[str] = []
    ipc = MagicMock(spec=IPCService)
    ipc.start.side_effect = lambda: order.append("ipc")
    manager = MagicMock()
    manager.start.side_effect = lambda: order.append("services")
    app._services = MagicMock()
    app._services.get.side_effect = lambda t, *d: manager if t is ServiceManager else ipc
    for name in dir(LFApplication):
        if name.startswith("_wire_"):
            setattr(app, name, lambda *a, **k: order.append("wire"))
    ipc.nats_url = "nats://site:4222"
    app._start_ipc()
    assert order[0] == "ipc" and order[-1] == "services"
    manager.set_main_ipc_url.assert_called_once_with("nats://site:4222")


def test_shutdown_stops_manager_before_ipc():
    app = LFApplication.__new__(LFApplication)
    app._state = None
    app._set_state = MagicMock()
    order: list[str] = []
    ipc = MagicMock()
    ipc.stop.side_effect = lambda: order.append("ipc")
    manager = MagicMock()
    manager.stop.side_effect = lambda: order.append("services")
    app._services = MagicMock()
    app._services.get.side_effect = lambda t, *d: (
        manager if t is ServiceManager else ipc if t is IPCService else None
    )
    app._local_nats = None
    app._shutdown()
    assert order == ["services", "ipc"]

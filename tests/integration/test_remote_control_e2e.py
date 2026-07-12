"""End-to-end: LocalNatsServer + real IPCService + RemoteControlService +
real RunEngine + ophyd.sim devices + headless reference client.

Skips when no nats-server binary is resolvable (install extra `local-nats`).
Runs WITHOUT the integration marker gate — this is the primary contract test.
Tiled persistence is exercised only under LIGHTFALL_INTEGRATION=1.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import threading
import time
from types import SimpleNamespace

import bluesky.plan_stubs as bps
import bluesky.preprocessors as bpp
import pytest

from lightfall.ipc.local_server import LocalNatsServer, resolve_nats_binary
from tests.integration.remote_client import RemoteError

pytestmark = pytest.mark.skipif(
    resolve_nats_binary() is None, reason="nats-server binary not available"
)


@bpp.run_decorator(md={"plan_name": "e2e_sleep"})
def e2e_sleep(seconds: float = 2.0):
    """Trivial plan used only to exercise the remote-control contract.

    Emits real start/stop docs (via run_decorator) without needing any
    device params, so it works with the default plan registry's
    device-requiring plans (count/scan_1d/etc all need detectors/motors).
    """
    yield from bps.sleep(seconds)


def _ensure_e2e_plans_registered() -> None:
    """Register test-only plans into the real (singleton) plan registry.

    Idempotent: safe to call from every test/fixture invocation across the
    module since PlanRegistry is a process-wide singleton.
    """
    from lightfall.acquire.plans.registry import get_registry

    registry = get_registry()
    if "e2e_sleep" not in registry:
        registry.register("e2e_sleep", e2e_sleep, category="test")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def nats_url():
    port = _free_port()
    server = LocalNatsServer(port=port)
    server.start(timeout_s=10.0)
    yield f"nats://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def lf(qapp, nats_url, monkeypatch):
    """A live server-side stack: IPCService + trust + RemoteControlService."""
    from ophyd.sim import SynAxis

    from lightfall.devices.model import DeviceCategory, DeviceInfo
    from lightfall.ipc.service import IPCService
    from lightfall.ipc.trust import TrustManager, TrustState
    from lightfall.utils.threads import initialize_main_thread_invoker

    try:
        initialize_main_thread_invoker()
    except RuntimeError:
        pass  # already initialized by an earlier test in this process

    _ensure_e2e_plans_registered()

    prefix = "als.e2etest"
    ipc = IPCService(nats_url=nats_url, topic_prefix=prefix)
    trust = TrustManager()
    trust.approve("e2e-client")  # pre-approve: no TrustDialog in headless tests
    ipc.set_trust_manager(trust)
    ipc.register_meta_endpoints()

    # auth.request handler mirroring application._handle_ipc_auth_request for
    # the approved path (session=None acceptable: no tiled key in this test).
    def handle_auth(subject, data, reply):
        app_name = data.get("app_name", "unknown")
        if ipc.evaluate_trust(app_name) == TrustState.APPROVED:
            resp = {"status": "approved", "contract_version": 1}
            resp["session_token"] = ipc.mint_session_channel(app_name)
            ipc.reply(reply, resp)
        else:
            ipc.reply(reply, {"status": "denied", "contract_version": 1})

    ipc.register_action("auth.request", handle_auth, main_thread=False)

    # Real engine + sim device
    from lightfall.acquire.engine import get_engine, reset_engine
    from lightfall.remote.service import RemoteControlService

    reset_engine()
    engine = get_engine("bluesky")

    motor = SynAxis(name="e2e_motor", delay=0.2)

    class _Catalog:
        def list_devices(self, **kw):
            return [self._info]

        _info = DeviceInfo(
            name="e2e_motor", category=DeviceCategory.MOTOR, device_class="ophyd.sim.SynAxis"
        )

        def get_device_by_name(self, name):
            return self._info if name == "e2e_motor" else None

        def get_ophyd_device(self, name):
            return motor if name == "e2e_motor" else None

    remote = RemoteControlService(ipc, engine=engine, catalog=_Catalog())
    remote.start()
    ipc.start()

    # Wait for NATS connection
    deadline = time.monotonic() + 10
    while not ipc.is_connected and time.monotonic() < deadline:
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()
        time.sleep(0.05)
    assert ipc.is_connected

    yield SimpleNamespace(
        ipc=ipc, trust=trust, engine=engine, motor=motor, prefix=prefix, remote=remote
    )

    remote.stop()
    ipc.stop()
    reset_engine()


class _ClientRunner:
    """Drives the async reference client from sync test code on a thread."""

    def __init__(self, nats_url, prefix):
        from tests.integration.remote_client import LightfallRemoteClient

        self.client = LightfallRemoteClient(nats_url, prefix, "e2e-client")
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro, timeout=30.0):
        """Submit *coro* to the client's loop and wait for it, pumping Qt.

        Engine document signals (``sigOutput``/``sigFinish``/...) are queued
        cross-thread onto the Qt main thread, and this test thread IS the Qt
        main thread. A plain blocking ``future.result(timeout)`` would starve
        that queue for the whole call, so server-side handlers that wait on
        those signals (e.g. plan.run waiting for the start doc) would never
        see them fire until some *later* call happened to pump events — by
        which point a fast plan may already have finished, corrupting
        run/item correlation. Pump events while waiting instead.
        """
        from PySide6.QtCore import QCoreApplication

        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        deadline = time.monotonic() + timeout
        while not future.done():
            QCoreApplication.processEvents()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        return future.result(timeout=0.1)

    def close(self):
        try:
            self.run(self.client.close(), timeout=5)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=5)


@pytest.fixture
def client(lf, nats_url):
    runner = _ClientRunner(nats_url, lf.prefix)
    runner.run(runner.client.connect())
    yield runner
    runner.close()


def _pump_qt_until(predicate, timeout=15.0):
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_full_contract_flow(lf, client):
    # 1. Handshake -> token + channel
    auth = client.run(client.client.authenticate())
    assert auth["status"] == "approved"
    assert auth["contract_version"] == 1
    assert client.client.session_token

    # 2. Bare-subject call is rejected
    bare = client.run(client.client.call_bare("commands.engine.status", {}))
    assert bare["status"] == "error" and bare["code"] == "denied"

    # 3. Device search / components / info / get round-trip
    reply = client.run(client.client.call("commands.device.search", {}))
    assert reply["devices"] == ["e2e_motor"]
    reply = client.run(client.client.call("commands.device.info", {"device": "e2e_motor"}))
    assert reply["device_class"] == "ophyd.sim.SynAxis"
    reply = client.run(client.client.call("commands.device.components", {"device": "e2e_motor"}))
    assert any(c["name"] == "readback" for c in reply["components"])
    reply = client.run(client.client.call("commands.device.get", {"device": "e2e_motor"}))
    assert "value" in reply and "timestamp" in reply

    # BlueskyEngine boots its RunEngine lazily on the worker thread; is_idle
    # is False until that boot completes, so device.put/plan.run would look
    # "busy" during warm-up. Wait it out (generous timeout per brief note 2).
    assert _pump_qt_until(lambda: lf.engine.is_idle, timeout=30), "engine never warmed up"

    # 4. put wait=true completes against the slow (delay=0.2) sim positioner
    t0 = time.monotonic()
    reply = client.run(
        client.client.call("commands.device.put", {"device": "e2e_motor", "value": 2.5}, timeout=10)
    )
    assert reply["status"] == "ok"
    assert time.monotonic() - t0 >= 0.15  # actually waited for completion
    assert lf.motor.readback.get() == pytest.approx(2.5)

    # 5. plan.run of a sim plan -> run_uid; runs.new/complete observed
    events: list[tuple[str, dict]] = []
    client.run(client.client.subscribe_event("runs.new", lambda d: events.append(("new", d))))
    client.run(
        client.client.subscribe_event("runs.complete", lambda d: events.append(("complete", d)))
    )

    plans = client.run(client.client.call("commands.plan.list", {}))["plans"]
    assert plans, "plan registry is empty — default registry did not load"
    assert any(p["name"] == "e2e_sleep" for p in plans)

    reply = client.run(
        client.client.call(
            "commands.plan.run", {"plan_name": "e2e_sleep", "params": {"seconds": 0.5}}, timeout=15
        )
    )
    assert reply["status"] == "submitted"
    assert reply["item_id"]

    assert _pump_qt_until(lambda: any(e[0] == "complete" for e in events), timeout=30)
    new_evt = next(d for k, d in events if k == "new")
    assert new_evt["item_id"] == reply["item_id"]
    assert new_evt["run_uid"]
    if reply["run_uid"] is not None:
        assert reply["run_uid"] == new_evt["run_uid"]

    # 6. Logout kills the channel; re-handshake restores it
    lf.trust.clear()
    lf.ipc.teardown_session_channels()
    with pytest.raises(Exception):  # noqa: B017 — timeout or denied, either is fine here
        client.run(client.client.call("commands.engine.status", {}, timeout=2))
    lf.trust.approve("e2e-client")
    auth2 = client.run(client.client.authenticate())
    assert auth2["status"] == "approved"
    assert auth2["session_token"] != auth["session_token"]
    reply = client.run(client.client.call("commands.engine.status", {}))
    assert reply["state"] in ("idle", "running")


def test_busy_rejection_while_plan_runs(lf, client):
    auth = client.run(client.client.authenticate())
    assert auth["status"] == "approved"

    plans = client.run(client.client.call("commands.plan.list", {}))["plans"]
    assert any(p["name"] == "e2e_sleep" for p in plans)

    assert _pump_qt_until(lambda: lf.engine.is_idle, timeout=30), "engine never warmed up"

    # Submit a plan that runs long enough to observe the engine mid-flight.
    reply = client.run(
        client.client.call(
            "commands.plan.run", {"plan_name": "e2e_sleep", "params": {"seconds": 3.0}}, timeout=15
        )
    )
    assert reply["status"] == "submitted"

    # Poll engine.status until the engine reports "running".
    def _is_running() -> bool:
        status = client.run(client.client.call("commands.engine.status", {}))
        return status["state"] == "running"

    assert _pump_qt_until(_is_running, timeout=15), "engine never reported running"

    # plan.run (default behavior="reject") is rejected while busy.
    with pytest.raises(RemoteError) as exc_info:
        client.run(
            client.client.call("commands.plan.run", {"plan_name": "e2e_sleep", "params": {}})
        )
    assert exc_info.value.code == "busy"

    # device.put is rejected while the engine is not idle.
    with pytest.raises(RemoteError) as exc_info:
        client.run(
            client.client.call("commands.device.put", {"device": "e2e_motor", "value": 1.0})
        )
    assert exc_info.value.code == "busy"

    # Let the long plan finish so it doesn't leak into the next test.
    def _is_idle_again() -> bool:
        status = client.run(client.client.call("commands.engine.status", {}))
        return status["state"] == "idle"

    assert _pump_qt_until(_is_idle_again, timeout=15), "engine never returned to idle"


def _tiled_deps_available() -> bool:
    try:
        from bluesky_tiled_plugins import TiledWriter  # noqa: F401
        from tiled.catalog import in_memory  # noqa: F401
        from tiled.client import Context, from_context  # noqa: F401
        from tiled.server.app import build_app  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("LIGHTFALL_INTEGRATION"),
    reason="set LIGHTFALL_INTEGRATION=1 to run the Tiled-persistence e2e",
)
@pytest.mark.skipif(not _tiled_deps_available(), reason="tiled server deps not importable")
def test_run_lands_in_tiled(lf, client):
    """Spec §7: a plan submitted via the remote contract lands in Tiled."""
    from bluesky_tiled_plugins import TiledWriter
    from tiled.catalog import in_memory as tiled_in_memory
    from tiled.client import Context, from_context
    from tiled.server.app import build_app

    # In-memory writable Tiled catalog + client (tsuchinoko e2e pattern).
    tmpdir = tempfile.mkdtemp(prefix="lightfall_test_tiled_")
    catalog = tiled_in_memory(writable_storage=tmpdir)
    app = build_app(catalog)
    context = Context.from_app(app)
    tiled_client = from_context(context)
    try:
        auth = client.run(client.client.authenticate())
        assert auth["status"] == "approved"

        # RunEngine boots lazily on the worker thread — wait for it, then
        # subscribe a TiledWriter directly to the real RE document stream.
        assert _pump_qt_until(lambda: lf.engine.RE is not None, timeout=30)
        assert _pump_qt_until(lambda: lf.engine.is_idle, timeout=30)
        token = lf.engine.RE.subscribe(TiledWriter(tiled_client))
        try:
            reply = client.run(
                client.client.call(
                    "commands.plan.run",
                    {"plan_name": "e2e_sleep", "params": {"seconds": 0.2}},
                    timeout=15,
                )
            )
            assert reply["status"] == "submitted"
            run_uid = reply["run_uid"]
            assert run_uid, "plan.run did not return a run_uid within the start-doc wait"

            # Wait for the run to complete and the stop doc to be written.
            assert _pump_qt_until(lambda: lf.engine.is_idle, timeout=30)

            def _landed() -> bool:
                return run_uid in tiled_client

            assert _pump_qt_until(_landed, timeout=30), "run_uid never appeared in Tiled"
            node = tiled_client[run_uid]
            assert node.metadata["start"]["uid"] == run_uid
        finally:
            lf.engine.RE.unsubscribe(token)
    finally:
        context.close()

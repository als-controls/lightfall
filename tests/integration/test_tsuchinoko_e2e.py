"""Cross-repo end-to-end integration test: Lightfall adaptive plan + Tsuchinoko.

Spawns a real nats-server, creates an in-memory Tiled catalog, runs
Tsuchinoko Core with RandomInProcess, and drives Lightfall's
``adaptive_experiment`` plan through a bluesky RunEngine. Validates that
NATS messages flow correctly and Tiled receives the expected schema.

Requires the integration venv with both lightfall and tsuchinoko installed.
Skips gracefully if nats-server binary or tsuchinoko are unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any

import numpy as np
import pytest

# Guard: skip collection entirely when the integration venv is not active.
# The normal Lightfall venv has a broken tiled[server] install, which causes
# ImportError on collection. This guard makes `pytest --collect-only` safe.
try:
    from tiled.catalog import in_memory as tiled_in_memory
    from tiled.client import Context, from_context
    from tiled.server.app import build_app
    from bluesky_tiled_plugins import TiledWriter
except ImportError as _exc:
    pytest.skip(
        f"Integration deps not available ({_exc}); use .venv-integration",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Skip early if tsuchinoko is not installed
# ---------------------------------------------------------------------------
try:
    from tsuchinoko.adaptive.random_in_process import RandomInProcess
    from tsuchinoko.core import Core, CoreState
    from tsuchinoko.execution.lightfall import LightfallEngine
    from tsuchinoko.nats.client import NATSClient
    from tsuchinoko.nats.config import NATSConfig
    from tsuchinoko.tiled.reader import TiledReader
    from tsuchinoko.tiled.writer import TiledPublisher
except ImportError:
    pytest.skip("tsuchinoko not installed", allow_module_level=True)

import nats as nats_lib

# Bluesky / ophyd
from bluesky import RunEngine
from ophyd.sim import SynAxis, SynSignal

# Lightfall (under test)
# Ensure QApplication exists before any PySide6 QObject/Signal usage
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication(sys.argv)

from lightfall.acquire.plans.adaptive import adaptive_experiment, _state
from lightfall.visualization.widgets.adaptive.heatmap import (
    AdaptiveHeatmapVisualization,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NATS_SERVER_BIN = (
    r"C:\Users\rp\AppData\Local\Microsoft\WinGet\Packages"
    r"\NATSAuthors.NATSServer_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\nats-server-v2.10.25-windows-amd64\nats-server.exe"
).replace("\\", "/")


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def nats_server():
    """Start a nats-server on a random port. Session-scoped."""
    if not os.path.isfile(NATS_SERVER_BIN):
        pytest.skip(f"nats-server not found at {NATS_SERVER_BIN}")

    port = _free_port()
    proc = subprocess.Popen(
        [NATS_SERVER_BIN, "-p", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for the server to be ready
    url = f"nats://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("nats-server did not start within 10s")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture()
def tiled_env():
    """Writable in-memory Tiled catalog + client (function-scoped)."""
    tmpdir = tempfile.mkdtemp(prefix="lightfall_test_tiled_")
    catalog = tiled_in_memory(writable_storage=tmpdir)
    app = build_app(catalog)
    context = Context.from_app(app)
    client = from_context(context)
    yield client
    context.close()


class _FakeSubscription:
    """Returned by FakeIPCService.subscribe(); supports .unsubscribe()."""

    def __init__(self, unsub_callback):
        self._unsub = unsub_callback

    def unsubscribe(self):
        self._unsub()


class FakeIPCService:
    """Lightweight test double bridging sync calls to a real NATS connection.

    Runs an asyncio event loop in a background daemon thread. Exposes the
    same subscribe/publish interface that NATSPlanBridge expects from
    lightfall.ipc.service.IPCService.
    """

    def __init__(self, nats_url: str) -> None:
        self._nats_url = nats_url
        self._nc: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._subs: list[Any] = []

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("FakeIPCService loop did not start")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect())
        self._ready.set()
        self._loop.run_forever()

    async def _connect(self) -> None:
        self._nc = await nats_lib.connect(self._nats_url)

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            if self._thread:
                self._thread.join(timeout=5)

    async def _shutdown(self) -> None:
        for sub in self._subs:
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        if self._nc:
            await self._nc.drain()
        self._loop.stop()

    # -- IPCService interface ------------------------------------------------

    def subscribe(self, subject: str, callback, main_thread: bool = False):
        """Subscribe to a NATS subject. callback(subject, data_dict, reply)."""
        fut = asyncio.run_coroutine_threadsafe(
            self._subscribe_async(subject, callback), self._loop
        )
        return fut.result(timeout=5)

    async def _subscribe_async(self, subject, callback):
        async def handler(msg):
            data = json.loads(msg.data)
            reply = msg.reply or ""
            # Call the callback in a thread to avoid blocking the event loop
            callback(msg.subject, data, reply)

        sub = await self._nc.subscribe(subject, cb=handler)
        self._subs.append(sub)

        def unsub():
            asyncio.run_coroutine_threadsafe(sub.unsubscribe(), self._loop)

        return _FakeSubscription(unsub)

    def publish(self, subject: str, payload: dict) -> None:
        """Publish JSON to NATS. Fire-and-forget."""
        asyncio.run_coroutine_threadsafe(
            self._publish_async(subject, payload), self._loop
        )

    async def _publish_async(self, subject, payload):
        if self._nc:
            await self._nc.publish(subject, json.dumps(payload).encode())
            await self._nc.flush()


@pytest.fixture()
def fake_ipc(nats_server):
    """Create a FakeIPCService and monkey-patch get_ipc_service."""
    import lightfall.ipc.service as ipc_mod

    svc = FakeIPCService(nats_server)
    svc.start()

    original = ipc_mod.get_ipc_service
    ipc_mod.get_ipc_service = lambda: svc

    yield svc

    ipc_mod.get_ipc_service = original
    svc.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeTiledReader:
    """Minimal TiledReader that returns synthetic measurements for each target.

    The real TiledReader reads from the bluesky primary stream in Tiled.
    In this test the RunEngine writes events to Tiled via TiledWriter, but
    the Tiled primary stream layout may differ from what TiledReader expects.
    This fake just produces measurements matching the targets that were sent,
    so the Core's adaptive loop can complete.
    """

    def __init__(self):
        self._targets: list[list[float]] = []
        self._consumed = 0

    def inject_targets(self, targets: list[list[float]]) -> None:
        self._targets.extend(targets)

    def read_new(self) -> list[tuple]:
        new = self._targets[self._consumed:]
        self._consumed = len(self._targets)
        results = []
        for t in new:
            x, y = t[0], t[1]
            value = float(np.sin(x / 30.0) * np.cos(y / 30.0))
            results.append((tuple(t), value, 0.1, {}))
        return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
class TestAdaptiveLoopE2E:
    """End-to-end: Tsuchinoko Core + Lightfall adaptive_experiment plan."""

    def test_adaptive_loop_e2e(self, nats_server, tiled_env, fake_ipc):
        """Full adaptive loop: targets flow via NATS, results land in Tiled."""
        client = tiled_env
        run_uid = f"test-run-{uuid.uuid4().hex[:8]}"
        lightfall_prefix = f"test.lightfall.{uuid.uuid4().hex[:8]}"
        n_iterations = 5

        # -- Sim devices ----------------------------------------------------
        motor_x = SynAxis(name="motor_x")
        motor_y = SynAxis(name="motor_y")
        det = SynSignal(name="det", func=lambda: np.random.random())

        # -- RunEngine ------------------------------------------------------
        # context_managers=[] avoids signal.signal() in non-main thread
        RE = RunEngine({}, context_managers=[])

        # -- Fake Tiled reader for Tsuchinoko's LightfallEngine ----------------
        fake_reader = _FakeTiledReader()

        # -- LightfallEngine (Tsuchinoko's execution engine) --------------------
        lightfall_engine = LightfallEngine(
            nats_client=None,  # will be set after Core starts
            tiled_reader=fake_reader,
            lightfall_prefix=lightfall_prefix,
        )

        # -- Tsuchinoko Core -----------------------------------------------
        adaptive_engine = RandomInProcess(
            dimensionality=2,
            parameter_bounds=[(0, 100), (0, 100)],
        )
        nats_config = NATSConfig(url=nats_server, lightfall_prefix="")

        # TiledPublisher needs the run to exist in Tiled first
        run_container = client.create_container(key=run_uid)
        tiled_publisher = TiledPublisher(
            tiled_client=client,
            run_uid=run_uid,
            dimensionality=2,
        )

        core = Core(
            execution_engine=lightfall_engine,
            adaptive_engine=adaptive_engine,
            compute_metrics=False,
            nats_config=nats_config,
            tiled_publisher=tiled_publisher,
        )
        core.exit_at = [n_iterations]

        # -- Start Core in a thread; wait for NATS to connect ---------------
        core_thread = threading.Thread(target=core.main, daemon=True)
        core_thread.start()
        # Wait for NATS client to be available
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if core._nats_client is not None and core._nats_client.is_connected:
                break
            time.sleep(0.1)
        else:
            pytest.fail("Core NATS client did not connect within 10s")

        # Inject the NATSClient into LightfallEngine before experiment starts
        lightfall_engine._nats_client = core._nats_client

        # -- Wire NATS: subscribe to {prefix}.adaptive.measured on behalf
        #    of Tsuchinoko. The plan publishes here after each measurement.
        measured_count = {"n": 0}

        def _on_measured(subject, data, reply):
            """NATS callback: Lightfall signals a measurement is done."""
            measured_count["n"] += data.get("n_new_points", 1)
            lightfall_engine.signal_measurements_ready()

        measured_sub = fake_ipc.subscribe(
            f"{lightfall_prefix}.adaptive.measured",
            callback=_on_measured,
            main_thread=False,
        )

        # -- Wire: intercept update_targets to inject into fake reader
        original_update_targets = lightfall_engine.update_targets

        def _patched_update_targets(targets):
            """Intercept targets to feed the fake reader."""
            fake_reader.inject_targets([list(t) for t in targets])
            original_update_targets(targets)

        lightfall_engine.update_targets = _patched_update_targets

        # -- Write TiledPublisher config ------------------------------------
        tiled_publisher.write_config(adaptive_engine)

        # -- Start the Lightfall plan FIRST so it subscribes to tsuchinoko.targets
        #    before Core starts publishing targets. RunEngine needs
        #    context_managers=[] to avoid signal.signal() in non-main thread.
        plan_error = {"exc": None}

        def _run_plan():
            try:
                RE(
                    adaptive_experiment(
                        [det],
                        [motor_x, motor_y],
                        experiment_id="test-e2e-123",
                        lightfall_prefix=lightfall_prefix,
                        timeout=20.0,
                        poll_interval=0.05,
                    )
                )
            except Exception as e:
                plan_error["exc"] = e

        plan_thread = threading.Thread(target=_run_plan, daemon=True)
        plan_thread.start()

        # Let the plan subscribe to NATS before we trigger the Core
        time.sleep(1.0)

        # -- NOW trigger the experiment loop --------------------------------
        core.state = CoreState.Starting

        # -- Wait for Core to finish (exit_at=[5]) --------------------------
        core_thread.join(timeout=30)

        # Stop the plan once the Core is done
        _state.stop_requested = True
        plan_thread.join(timeout=10)

        # -- Cleanup --------------------------------------------------------
        measured_sub.unsubscribe()

        # -- Assertions -----------------------------------------------------
        # Plan should not have raised
        if plan_error["exc"] is not None:
            raise AssertionError(
                f"Plan raised: {plan_error['exc']}"
            ) from plan_error["exc"]

        # Core should have exited cleanly
        assert not core_thread.is_alive(), "Core thread did not exit"
        assert not plan_thread.is_alive(), "Plan thread did not exit"

        # Tiled: adaptive container exists
        run_node = client[run_uid]
        assert "adaptive" in run_node.keys(), (
            f"Missing 'adaptive' container. Keys: {list(run_node.keys())}"
        )

        adaptive_node = run_node["adaptive"]
        assert adaptive_node.metadata.get("adaptive_engine") == "tsuchinoko"

        # Tiled: config has evaluation grids
        config_node = adaptive_node["config"]
        config_keys = list(config_node.keys())
        assert "evaluation_grid_x" in config_keys
        assert "evaluation_grid_y" in config_keys

        # Tiled: at least 2 iteration containers
        iter_keys = sorted(
            k for k in adaptive_node.keys() if k.startswith("iter_")
        )
        assert len(iter_keys) >= 2, (
            f"Expected >= 2 iterations, got {len(iter_keys)}: {iter_keys}"
        )

        # First iteration has targets
        iter_000 = adaptive_node[iter_keys[0]]
        iter_000_keys = list(iter_000.keys())
        assert "targets" in iter_000_keys, (
            f"iter_000 missing 'targets'. Keys: {iter_000_keys}"
        )

        # Viz: AdaptiveHeatmapVisualization.can_handle should return 90
        score = AdaptiveHeatmapVisualization.can_handle(run_node)
        assert score == 90, f"can_handle returned {score}, expected 90"


@pytest.mark.timeout(30)
class TestTiledSchemaCanHandle:
    """Verify viz widget compatibility with Tsuchinoko's Tiled schema."""

    def test_can_handle_returns_90_for_2d(self, tiled_env):
        """A run with adaptive container + 2D grid config scores 90.

        ``can_handle`` reads the evaluation grids from descriptor metadata
        (``configuration.tsuchinoko.data``), the layout that
        ``TiledPublisher._build_grid_config`` actually emits — not from child
        ``config`` / ``iter_NNN`` containers.
        """
        client = tiled_env
        run = client.create_container(key="viz-test-run")
        adaptive = run.create_container(key="adaptive")
        adaptive.update_metadata(
            {
                "adaptive_engine": "tsuchinoko",
                "configuration": {
                    "tsuchinoko": {
                        "data": {
                            "evaluation_grid_x": np.linspace(0, 100, 50).tolist(),
                            "evaluation_grid_y": np.linspace(0, 100, 50).tolist(),
                        }
                    }
                },
            }
        )

        score = AdaptiveHeatmapVisualization.can_handle(
            client["viz-test-run"]
        )
        assert score == 90

    def test_can_handle_returns_0_for_3d(self, tiled_env):
        """A 3D run (has grid_z) should return 0 from the 2D heatmap widget."""
        client = tiled_env
        run = client.create_container(key="viz-test-3d")
        adaptive = run.create_container(key="adaptive")
        adaptive.update_metadata(
            {
                "adaptive_engine": "tsuchinoko",
                "configuration": {
                    "tsuchinoko": {
                        "data": {
                            "evaluation_grid_x": np.linspace(0, 100, 50).tolist(),
                            "evaluation_grid_y": np.linspace(0, 100, 50).tolist(),
                            "evaluation_grid_z": np.linspace(0, 100, 50).tolist(),
                        }
                    }
                },
            }
        )

        score = AdaptiveHeatmapVisualization.can_handle(
            client["viz-test-3d"]
        )
        assert score == 0

    def test_can_handle_returns_0_without_engine_tag(self, tiled_env):
        """A run without adaptive_engine metadata should return 0."""
        client = tiled_env
        run = client.create_container(key="viz-test-no-engine")
        adaptive = run.create_container(key="adaptive")
        # Grids present, but no adaptive_engine tag -> not ours.
        adaptive.update_metadata(
            {
                "configuration": {
                    "tsuchinoko": {
                        "data": {
                            "evaluation_grid_x": np.linspace(0, 100, 50).tolist(),
                            "evaluation_grid_y": np.linspace(0, 100, 50).tolist(),
                        }
                    }
                },
            }
        )

        score = AdaptiveHeatmapVisualization.can_handle(
            client["viz-test-no-engine"]
        )
        assert score == 0


@pytest.mark.timeout(10)
class TestNATSSubjectsMatch:
    """Verify that Lightfall and Tsuchinoko agree on NATS subject names."""

    def test_targets_subject(self):
        """LightfallEngine publishes to 'tsuchinoko.targets'; plan subscribes same."""
        import inspect
        src = inspect.getsource(LightfallEngine.update_targets)
        assert '"tsuchinoko.targets"' in src

        # adaptive_experiment subscribes to "tsuchinoko.targets"
        from lightfall.acquire.plans.adaptive import adaptive_experiment
        src_plan = inspect.getsource(adaptive_experiment.__wrapped__
                                      if hasattr(adaptive_experiment, '__wrapped__')
                                      else adaptive_experiment)
        assert '"tsuchinoko.targets"' in src_plan

    def test_measured_subject_uses_prefix(self):
        """Plan publishes to '{lightfall_prefix}.adaptive.measured'."""
        import inspect
        from lightfall.acquire.plans.adaptive import adaptive_experiment
        src = inspect.getsource(adaptive_experiment.__wrapped__
                                 if hasattr(adaptive_experiment, '__wrapped__')
                                 else adaptive_experiment)
        assert "adaptive.measured" in src

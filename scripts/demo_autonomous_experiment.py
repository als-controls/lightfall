"""End-to-end smoke test for the autonomous-experiment agent integration.

This is a MANUAL verification script. It is not run by CI.

Prerequisites:
- Both LUCID and Tsuchinoko venvs installed.
- nats-server binary on PATH (or NATS_SERVER_BIN env var pointing at it).
- A Tiled server reachable at $LUCID_TILED_URL (or use the default LUCID setting).
- gpcam importable in LUCID's environment if the agent should load the
  experiment-designer skill (this script does NOT exercise the agent -- it
  drives the wire surface directly).

Flow:
1. Optionally start a local nats-server (NATS_TEST_AUTOSTART=1).
2. Launch `tsuchinoko run --nats nats://localhost:4222` in a subprocess.
3. Stand up a headless IPCService against the same broker.
4. Walk the demo wire flow: discover -> configure -> run adaptive plan ->
   tail the adaptive Tiled stream.
5. Assert at least three iter_NNN containers landed in the run's
   adaptive stream.

Run (from the LUCID repo root, with .venv-integration active or .venv if
your default venv has bluesky + tiled + tsuchinoko installed):

    .venv/Scripts/python.exe scripts/demo_autonomous_experiment.py

Several integration seams are marked TODO -- fill in once Phase A is
deployed and the demo can actually be exercised against a real
Tsuchinoko instance.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# 1. nats-server (optional autostart)
# ---------------------------------------------------------------------------
NATS_AUTOSTART = os.environ.get("NATS_TEST_AUTOSTART") == "1"
nats_proc: subprocess.Popen | None = None
if NATS_AUTOSTART:
    bin_path = os.environ.get("NATS_SERVER_BIN") or shutil.which("nats-server")
    if not bin_path:
        print("ERROR: NATS_SERVER_BIN not set and nats-server not on PATH.")
        sys.exit(2)
    print(f"[1/5] Starting nats-server: {bin_path}")
    nats_proc = subprocess.Popen([bin_path, "-p", "4222"])
    time.sleep(1.0)
else:
    print("[1/5] Skipping nats-server autostart (set NATS_TEST_AUTOSTART=1 to enable).")

# ---------------------------------------------------------------------------
# 2. Launch Tsuchinoko
# ---------------------------------------------------------------------------
print("[2/5] Launching tsuchinoko...")
tsu_proc = subprocess.Popen(
    ["tsuchinoko", "run", "--nats", "nats://localhost:4222"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
print("       Waiting 3s for Tsuchinoko to subscribe...")
time.sleep(3.0)

try:
    # -----------------------------------------------------------------------
    # 3. Stand up a headless IPCService
    # -----------------------------------------------------------------------
    # NOTE: In a normal LUCID GUI session the singleton is constructed and
    # started during app boot. For a headless smoke test we instantiate
    # directly. If IPCService gains a `from_config()` / `start_headless()`
    # convenience later, prefer that.
    from lightfall.ipc.service import IPCService
    ipc = IPCService(
        nats_url="nats://localhost:4222",
        topic_prefix="lightfall-demo",
        # TODO: wire any other constructor args the current IPCService
        # signature requires (auth token, TLS config, etc.). Check
        # `IPCService.__init__` and pass the smoke-test equivalents.
    )
    ipc.start()
    # Give it a moment to actually connect
    time.sleep(1.0)
    print("[3/5] IPC up. Discovering Tsuchinoko...")

    # Discover
    discover = ipc.request("_tsuchinoko.discover", {}, timeout_ms=2000)
    assert discover and discover.get("app_name") == "tsuchinoko", (
        f"Unexpected discover reply: {discover!r}"
    )
    print(f"       Tsuchinoko instance: {discover.get('instance_id', '?')[:8]}")

    # -----------------------------------------------------------------------
    # 4. Configure + start
    # -----------------------------------------------------------------------
    print("[4/5] Configuring Tsuchinoko (bounds-only minimal payload)...")
    cfg = ipc.request(
        "tsuchinoko.experiment.configure",
        {
            "parameter_bounds": [[-1.0, 1.0], [-1.0, 1.0]],
            "kernel": "matern_3_2",
            "acquisition_function": "variance",
            "initial_points": 5,
        },
        timeout_ms=5000,
    )
    assert cfg and cfg.get("status") == "ok", f"configure failed: {cfg!r}"

    # Build synthetic devices and drive the plan
    print("       Building synthetic devices + RunEngine...")
    from bluesky import RunEngine
    from ophyd.sim import SynAxis, SynSignal
    import numpy as np

    m1 = SynAxis(name="m1")
    m2 = SynAxis(name="m2")

    def _det_func():
        x = m1.read()["m1"]["value"]
        y = m2.read()["m2"]["value"]
        return float(np.exp(-(x * x + y * y)))

    det = SynSignal(name="det", func=_det_func)

    from lightfall.acquire.plans.adaptive import adaptive_experiment
    RE = RunEngine({})
    print("       Running adaptive_experiment plan (timeout=30s)...")
    RE(adaptive_experiment(detectors=[det], motors=[m1, m2], timeout=30.0))

    # -----------------------------------------------------------------------
    # 5. Tail the adaptive stream
    # -----------------------------------------------------------------------
    print("[5/5] Checking Tiled adaptive stream...")
    # TODO: source the Tiled URL the same way LUCID does at runtime. Two
    # options to wire here:
    #   a) Pull from lightfall settings (lightfall.ui.preferences.tiled_settings or
    #      similar) and use tiled.client.from_uri with whatever auth LUCID
    #      currently sends.
    #   b) Accept an env var like LUCID_TILED_URL and skip auth (only
    #      acceptable against a dev catalog).
    tiled_url = os.environ.get("LUCID_TILED_URL")
    if not tiled_url:
        print("       NOTE: $LUCID_TILED_URL not set -- skipping adaptive-stream check.")
        print("       Demo wire flow completed successfully through configure + plan run.")
    else:
        from tiled.client import from_uri
        client = from_uri(tiled_url)
        last = next(iter(client.values()))
        adaptive = last["adaptive"]
        iter_keys = [k for k in adaptive if str(k).startswith("iter_")]
        assert len(iter_keys) >= 3, (
            f"adaptive stream has only {len(iter_keys)} iter_ containers"
        )
        print(
            f"       OK -- adaptive stream has {len(iter_keys)} iter_ containers"
        )

    print("DEMO COMPLETED.")

finally:
    print("Tearing down...")
    tsu_proc.terminate()
    try:
        tsu_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        tsu_proc.kill()
    if nats_proc is not None:
        nats_proc.terminate()
        try:
            nats_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            nats_proc.kill()

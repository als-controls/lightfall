"""Phase-2c streaming-viz build spike (Task 1).

Pins three version-/code-sensitive unknowns against a REAL local streaming
Tiled 0.2.9 server, so Tasks 2-6 build against confirmed forms:

  PROBE A -- Subscription surface + message->source routing.
    Does Tiled 0.2.9 offer a CONTAINER/run-level subscription that forwards a
    run's child array-data/table-data, or must a panel subscribe PER-CHILD?
    Creates a run-like container with a child array + a child table, subscribes
    at the container AND per-child, appends to each, and records exactly which
    messages arrive on which subscription and how each identifies its source.

  PROBE B -- Does the existing patched TiledWriter persist the fly-scan as a
    streamable 2-D (ny, nx) ARRAY node? Drives the real Phase-2b fly-scan plan
    through Lightfall's BlueskyEngine with TiledService pointed at the local
    streaming server, at nx=10 (the plan-plugin default) and nx=32, then
    inspects the persisted node path / structure family / shape for the
    detector key. This decides whether Task 5 needs a new writer.

Run (Lightfall 3.14 venv):
    QT_QPA_PLATFORM=offscreen \
    C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python scripts/spike_stream_viz.py

The script spawns the streaming server itself (python -m tiled serve config) and
tears it down on exit. It is a SPIKE -- additive, reads lightfall source only.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# --------------------------------------------------------------------------
# Scratchpad paths (server config + storage live here; not committed)
# --------------------------------------------------------------------------
SCRATCH = Path(
    r"C:\Users\rp\AppData\Local\Temp\claude\C--Users-rp-workspace"
    r"\a95c2b18-d2c2-4c3c-9b2b-765d9622b5ac\scratchpad"
)
CONFIG = SCRATCH / "config_viz_streaming.yml"
SERVER_LOG = SCRATCH / "run" / "server_viz_stream.log"
API_KEY = "vizspikekey0123456789"
PYTHON = sys.executable  # the venv python running this script


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url: str, timeout: float = 30.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def start_server(port: int) -> subprocess.Popen:
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(SERVER_LOG, "w")
    # NOTE: `tiled.exe` console-script shim fails under Git Bash; use -m tiled.
    proc = subprocess.Popen(
        [PYTHON, "-m", "tiled", "serve", "config", str(CONFIG),
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=logf, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    ok = _wait_http(f"{base}/api/v1/?api_key={API_KEY}", timeout=40.0)
    if not ok:
        proc.terminate()
        raise RuntimeError(f"server did not come up; see {SERVER_LOG}")
    print(f"[server] up at {base} (pid={proc.pid})", flush=True)
    return proc


# ==========================================================================
# PROBE A -- container vs per-child subscription + routing
# ==========================================================================
def probe_a(base_url: str) -> dict:
    print("\n" + "=" * 70, flush=True)
    print("PROBE A: container vs per-child subscription + message routing", flush=True)
    print("=" * 70, flush=True)

    from tiled.client import from_uri
    import pyarrow as pa

    client = from_uri(base_url, api_key=API_KEY)

    # Clean any prior run container
    RUN = "run0"
    try:
        if RUN in client:
            del client[RUN]
    except Exception:
        pass

    # A run-like container holding a child array (the "map") + a child table.
    run = client.create_container(key=RUN)
    print(f"[A] created run container key={RUN!r} path_parts={run.path_parts}", flush=True)

    # --- Subscribe at the CONTAINER level (run) BEFORE children exist --------
    container_msgs: list = []
    clock = threading.Lock()

    def on_container(update):
        with clock:
            container_msgs.append(update)
        utype = getattr(update, "type", "?")
        key = getattr(update, "key", None)
        fam = getattr(update, "structure_family", None)
        print(f"  [CONTAINER-SUB] type={utype} key={key!r} structure_family={fam}", flush=True)

    csub = run.subscribe()  # -> tiled.client.stream.ContainerSubscription
    print(f"[A] run.subscribe() -> {type(csub).__name__} segments={csub.segments}", flush=True)
    # ContainerSubscription exposes child_created / child_metadata_updated registries.
    csub.child_created.add_callback(on_container)
    if hasattr(csub, "child_metadata_updated"):
        csub.child_metadata_updated.add_callback(on_container)
    csub.start_in_thread(start=1)
    time.sleep(0.8)

    # --- Create child array (the map) + child table -------------------------
    NX = 8
    line0 = np.arange(NX, dtype="float64")
    map_arr = run.write_array(line0.reshape(1, NX), key="stxm_map")
    print(f"[A] wrote child array 'stxm_map' shape={map_arr.shape}", flush=True)

    tbl_schema = pa.schema([("seq_num", pa.int64()), ("SampleY", pa.float64())])
    tbl = run.create_appendable_table(schema=tbl_schema, key="meta")
    print("[A] created child appendable table 'meta'", flush=True)
    time.sleep(1.0)  # let container-child-created messages land

    # --- Now subscribe PER-CHILD to the array and the table -----------------
    array_msgs: list = []
    table_msgs: list = []

    def on_array(update):
        with clock:
            array_msgs.append(update)
        utype = getattr(update, "type", "?")
        cls = type(update).__name__
        try:
            arr = update.data()  # inline decode -- no refetch for array-data
            arr_repr = np.asarray(arr).ravel()
        except Exception as e:
            arr_repr = f"<data() failed: {e!r}>"
        print(f"  [ARRAY-SUB ('stxm_map')] type={utype} cls={cls} "
              f"offset={getattr(update,'offset',None)} shape={getattr(update,'shape',None)} "
              f"data={arr_repr}", flush=True)

    def on_table(update):
        with clock:
            table_msgs.append(update)
        utype = getattr(update, "type", "?")
        cls = type(update).__name__
        try:
            df = update.data()
            df_repr = df.to_pydict() if hasattr(df, "to_pydict") else df
        except Exception as e:
            df_repr = f"<data() failed: {e!r}>"
        print(f"  [TABLE-SUB ('meta')] type={utype} cls={cls} "
              f"partition={getattr(update,'partition',None)} append={getattr(update,'append',None)} "
              f"data={df_repr}", flush=True)

    asub = map_arr.subscribe()      # -> ArraySubscription
    tsub = tbl.subscribe()          # -> TableSubscription
    print(f"[A] map_arr.subscribe() -> {type(asub).__name__} segments={asub.segments}", flush=True)
    print(f"[A] tbl.subscribe() -> {type(tsub).__name__} segments={tsub.segments}", flush=True)
    asub.new_data.add_callback(on_array)
    tsub.new_data.add_callback(on_table)
    asub.start_in_thread(start=1)
    tsub.start_in_thread(start=1)
    time.sleep(0.8)

    # --- Append to BOTH children; observe which sub gets what ----------------
    print("[A] appending 3 rows to the array, 3 rows to the table ...", flush=True)
    for row in range(1, 4):
        map_arr.patch((np.arange(NX, dtype="float64") + row * 100).reshape(1, NX),
                      offset=(row, 0), extend=True)
        tbl.append_partition(0, pa.table({"seq_num": [row], "SampleY": [float(row)]}))
        time.sleep(0.5)
    time.sleep(1.5)

    for s in (csub, asub, tsub):
        try:
            s.disconnect()
        except Exception as e:
            print(f"  [A] disconnect warn: {e!r}", flush=True)

    # --- Summarise ----------------------------------------------------------
    print("\n[A] SUMMARY", flush=True)
    print(f"  CONTAINER subscription messages: {len(container_msgs)}", flush=True)
    for m in container_msgs:
        print(f"    type={getattr(m,'type','?')} key={getattr(m,'key',None)!r} "
              f"family={getattr(m,'structure_family',None)}", flush=True)
    c_types = sorted({getattr(m, "type", "?") for m in container_msgs})
    a_types = sorted({getattr(m, "type", "?") for m in array_msgs})
    t_types = sorted({getattr(m, "type", "?") for m in table_msgs})
    print(f"  CONTAINER got types={c_types}  (array-data here? "
          f"{'array-data' in c_types}; table-data here? {'table-data' in c_types})", flush=True)
    print(f"  ARRAY-child sub got {len(array_msgs)} msgs types={a_types}", flush=True)
    print(f"  TABLE-child sub got {len(table_msgs)} msgs types={t_types}", flush=True)

    return {
        "container_child_created_keys": [getattr(m, "key", None) for m in container_msgs
                                         if getattr(m, "type", "") == "container-child-created"],
        "container_types": c_types,
        "array_types": a_types,
        "table_types": t_types,
        "array_data_on_container": "array-data" in c_types,
        "table_data_on_container": "table-data" in c_types,
        "n_array_msgs": len(array_msgs),
        "n_table_msgs": len(table_msgs),
    }


# ==========================================================================
# PROBE B -- fly-scan persistence through BlueskyEngine + TiledService
# ==========================================================================
def probe_b(base_url: str, nx: int, ny: int = 4) -> dict:
    print("\n" + "=" * 70, flush=True)
    print(f"PROBE B: fly-scan persistence (nx={nx}, ny={ny})", flush=True)
    print("=" * 70, flush=True)

    import asyncio

    from PySide6.QtWidgets import QApplication

    from lightfall.acquire.engine import get_engine
    from lightfall.services.tiled_service import TiledService, TiledAuthMode

    from lightfall_pystxmcontrol import config as stxm_config
    from lightfall_pystxmcontrol.devices import PystxmAxis
    from lightfall_pystxmcontrol.flyer import PystxmLineFlyer
    from lightfall_pystxmcontrol.plans import stxm_fly_raster

    app = QApplication.instance() or QApplication([])

    # --- Point TiledService at the local streaming server -------------------
    TiledService.reset()
    service = TiledService.get_instance()
    service.configure(url=base_url, api_key=API_KEY, enabled=True,
                      auth_mode=TiledAuthMode.API_KEY)
    connected = service.connect()
    print(f"[B] TiledService.connect() -> {connected} state={service.state}", flush=True)
    if not connected:
        return {"error": "TiledService failed to connect", "nx": nx}

    # --- Bring up the engine; subscribe the patched TiledWriter -------------
    engine = get_engine("bluesky")
    re = None
    for _ in range(200):
        re = engine.RE
        if re is not None:
            break
        app.processEvents()
        time.sleep(0.05)
    assert re is not None, "BlueskyEngine RE never became available"

    # TiledService.connect() already subscribed the patched ThreadedTiledWriter
    # to the engine (see _subscribe_writer). Confirm it's wired.
    print(f"[B] writer wired? {service._writer is not None} "
          f"(subscription_token={service._subscription_token})", flush=True)

    flyer = PystxmLineFlyer(stxm_config.DEFAULT_COUNTER,
                            stxm_config.DEFAULT_AXES["SampleX"], name="Counter1")
    yax = PystxmAxis(stxm_config.DEFAULT_AXES["SampleY"], name="SampleY")

    async def _connect_all():
        await flyer.connect(mock=False)
        await yax.connect(mock=False)

    asyncio.run(_connect_all())

    docs: list = []
    re.subscribe(lambda n, d: docs.append((n, d)))

    start_uid_box: dict = {}
    re.subscribe(lambda n, d: start_uid_box.update({"uid": d["uid"]}) if n == "start" else None)

    engine(stxm_fly_raster(flyer, yax, y_start=-5, y_stop=5, ny=ny,
                           x_start=-5, x_stop=5, nx=nx, dwell=1.0))

    deadline = time.time() + 90
    while time.time() < deadline:
        names = [n for n, _ in docs]
        if "stop" in names and engine.is_idle:
            break
        app.processEvents()
        time.sleep(0.05)

    names = [n for n, _ in docs]
    assert "stop" in names and engine.is_idle, f"fly raster did not finish: names={names}"
    uid = start_uid_box.get("uid")
    print(f"[B] run finished. docs={names[:1]}..{names[-1:]} uid={uid}", flush=True)

    # --- Flush the threaded writer so all docs reached Tiled ----------------
    if service._writer is not None:
        try:
            service._writer.flush(timeout=20.0)
        except Exception as e:
            print(f"[B] writer flush warn: {e!r}", flush=True)
    # Give Tiled a moment to settle the appendable table / arrays.
    time.sleep(2.0)

    # --- Inspect what landed in Tiled ---------------------------------------
    client = service.client
    findings: dict = {"nx": nx, "ny": ny, "uid": uid}
    try:
        run_client = client[uid]
    except Exception as e:
        print(f"[B] could not open run {uid}: {e!r}", flush=True)
        findings["error"] = f"open run failed: {e!r}"
        return findings

    try:
        run_children = list(run_client.keys())
    except Exception as e:
        run_children = f"<keys() failed: {e!r}>"
    print(f"[B] run children: {run_children}", flush=True)
    findings["run_children"] = run_children

    try:
        primary = run_client["primary"]
    except Exception as e:
        print(f"[B] no primary stream: {e!r}", flush=True)
        findings["error"] = f"no primary: {e!r}"
        return findings

    # primary may be a composite whose `internal` table failed to create
    # (then `structure.columns` is None and keys() raises). Don't let the
    # introspection crash abort the whole spike -- record it and move on.
    try:
        primary_children = list(primary.keys())
    except Exception as e:
        primary_children = f"<primary.keys() failed: {e!r}>"
        print(f"[B] primary.keys() failed (likely table never created): {e!r}", flush=True)
    print(f"[B] primary stream children: {primary_children}", flush=True)
    findings["primary_children"] = primary_children

    # Walk every node under the run and record path / structure family / shape.
    def describe_node(node, path):
        try:
            fam = node.structure_family
        except Exception:
            fam = "?"
        shape = None
        try:
            st = node.structure()
            shape = getattr(st, "shape", None)
        except Exception:
            shape = getattr(node, "shape", None)
        return {"path": path, "family": str(fam), "shape": tuple(shape) if shape else None}

    node_report: list = []

    def walk(node, prefix):
        try:
            keys = list(node.keys())
        except Exception:
            keys = []
        if not keys:
            node_report.append(describe_node(node, prefix))
            return
        for k in keys:
            child_path = f"{prefix}/{k}"
            try:
                child = node[k]
            except Exception as e:
                node_report.append({"path": child_path, "family": "?", "error": repr(e)})
                continue
            cfam = getattr(child, "structure_family", "?")
            node_report.append(describe_node(child, child_path))
            if str(cfam) in ("container", "StructureFamily.container", "composite"):
                walk(child, child_path)

    walk(primary, "primary")

    print("[B] NODE REPORT (path | family | shape):", flush=True)
    for r in node_report:
        print(f"    {r.get('path')} | {r.get('family')} | shape={r.get('shape')}"
              f"{' | ' + r['error'] if 'error' in r else ''}", flush=True)
    findings["node_report"] = node_report

    # Specifically: where did the detector key 'Counter1' (dtype=array, shape=[nx]) land?
    det_array = None
    det_table_col = None
    for r in node_report:
        p = r.get("path", "")
        if p.endswith("/Counter1") and r.get("family") in ("array", "StructureFamily.array"):
            det_array = r
        if p.endswith("/internal") and r.get("family") in ("table", "StructureFamily.table"):
            # check columns
            det_table_col = p

    # Read the events table to see if Counter1 is a per-row list column.
    try:
        from lightfall.utils.tiled_helpers import read_events
        ev = read_events(primary)
        if ev is not None:
            cols = list(ev.keys()) if hasattr(ev, "keys") else None
            findings["events_columns"] = cols
            print(f"[B] events table columns: {cols}", flush=True)
            if cols and "Counter1" in cols:
                c = np.asarray(ev["Counter1"])
                findings["events_Counter1_shape"] = tuple(c.shape)
                print(f"[B] events['Counter1'] shape (table facet) = {c.shape}", flush=True)
    except Exception as e:
        print(f"[B] read_events failed: {e!r}", flush=True)

    if det_array is not None:
        findings["detector_verdict"] = "ARRAY"
        findings["detector_node"] = det_array
        print(f"[B] VERDICT nx={nx}: Counter1 persisted as ARRAY node "
              f"path={det_array['path']} shape={det_array['shape']}", flush=True)
    else:
        findings["detector_verdict"] = "TABLE_COLUMN"
        print(f"[B] VERDICT nx={nx}: Counter1 is NOT a standalone array node "
              f"-- it is a per-row column in the internal table", flush=True)

    return findings


def main() -> int:
    print("=== Phase-2c streaming-viz spike ===", flush=True)
    import tiled
    print(f"tiled {tiled.__version__}; python {sys.version.split()[0]}", flush=True)

    port = _free_port()
    proc = start_server(port)
    base_url = f"http://127.0.0.1:{port}"
    results: dict = {}
    try:
        results["probe_a"] = probe_a(base_url)
        # nx=10 is the plan-plugin default; nx=32 crosses max_array_size=16.
        results["probe_b_nx10"] = probe_b(base_url, nx=10, ny=4)
        results["probe_b_nx32"] = probe_b(base_url, nx=32, ny=4)
    finally:
        print("\n[server] terminating", flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print("\n" + "=" * 70, flush=True)
    print("SPIKE RESULT SUMMARY", flush=True)
    print("=" * 70, flush=True)
    a = results.get("probe_a", {})
    print(f"[A] container-child-created keys observed: {a.get('container_child_created_keys')}", flush=True)
    print(f"[A] array-data delivered on CONTAINER sub? {a.get('array_data_on_container')}", flush=True)
    print(f"[A] table-data delivered on CONTAINER sub? {a.get('table_data_on_container')}", flush=True)
    print(f"[A] per-child ARRAY sub msgs={a.get('n_array_msgs')} types={a.get('array_types')}", flush=True)
    print(f"[A] per-child TABLE sub msgs={a.get('n_table_msgs')} types={a.get('table_types')}", flush=True)
    for tag in ("probe_b_nx10", "probe_b_nx32"):
        b = results.get(tag, {})
        print(f"[B/{tag}] verdict={b.get('detector_verdict')} "
              f"node={b.get('detector_node')} "
              f"events_cols={b.get('events_columns')} "
              f"events_Counter1_shape={b.get('events_Counter1_shape')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

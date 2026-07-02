# Phase-2c streaming-viz build spike — findings (Task 1)

**Date:** 2026-06-25
**Branch:** `feature/streaming-viz-updates` (lightfall repo)
**Spike script:** `scripts/spike_stream_viz.py` (spawns its own streaming Tiled
0.2.9 server, runs two probes, tears the server down).
**Env:** Lightfall 3.14 venv; `tiled 0.2.9`; Windows 11. Probes run against a
REAL `python -m tiled serve config` server with `streaming_cache: {uri: memory}`.

> **Verdicts up front**
> 1. **No container-level data fan-out.** A `ContainerSubscription` delivers
>    ONLY `container-child-created` / `container-child-metadata-updated`. The
>    actual `array-data` / `table-data` push arrives ONLY on a **per-child**
>    `ArraySubscription` / `TableSubscription`. The panel must subscribe per
>    child node; routing is by *which subscription fired* (update messages carry
>    no path).
> 2. **Fly-scan persistence is nx-dependent.** At the plan-plugin **default
>    `nx=10`** the detector key lands as a per-row **list column** in the stream
>    `internal` table (NOT a streamable array — and it 500s on a SQLite catalog).
>    At **`nx > 16`** (tested `nx=32`) it lands as a standalone streamable 2-D
>    **array** node `primary/Counter1`, structure family `array`, shape
>    `(ny, nx)`. The boundary is upstream's `max_array_size=16`.

---

## PROBE A — subscription surface + message→source routing

### What was proven (empirically, against the live server)

Created a run-like container `run0` with a child **array** (`stxm_map`) and a
child **appendable table** (`meta`). Subscribed at the container AND per-child,
appended to both, recorded what each subscription received:

```
[A] run.subscribe()      -> ContainerSubscription segments=['', 'run0']
[A] map_arr.subscribe()  -> ArraySubscription     segments=['', 'run0', 'stxm_map']
[A] tbl.subscribe()      -> TableSubscription      segments=['', 'run0', 'meta']

CONTAINER got types=['container-child-created']  (array-data here? False; table-data here? False)
  container-child-created key='stxm_map' family=StructureFamily.array
  container-child-created key='meta'     family=StructureFamily.table
ARRAY-child sub  got 4 msgs  types=['array-data']   (1 write + 3 patches)
TABLE-child sub  got 3 msgs  types=['table-data']   (3 appends)
```

### Source-of-truth (code, Tiled 0.2.9)

- There is **one** WS endpoint: `/api/v1/stream/single/{path}` (`server/router.py:748`).
  No recursive/run-level data endpoint exists.
- `ContainerSubscription.process` (`client/stream.py:571`) routes only
  `container-child-created` → `child_created` and
  `container-child-metadata-updated` → `child_metadata_updated`; anything else
  **raises**. It exposes NO `new_data`.
- The server emits `container-child-created` against the **parent** node id when
  a child node is created (`catalog/adapter.py:793`, `incr_seq(self.node.id)`),
  whereas `array-data` (`adapter.py:1287`) and `table-data` (`adapter.py:1409`)
  are emitted against the **child's own** node id — so they only reach a
  subscription opened on that child.
- Update messages (`ArrayData`, `TableData` in `stream_messages.py`) carry **no
  path/key** — only `ChildCreated` carries `key` + `structure_family`. Therefore
  a per-child subscription identifies its source implicitly (you know which node
  you subscribed to: `sub.segments`).

### Copy-pasteable subscription API (per-child — the data path)

```python
from tiled.client.stream import ArraySubscription, TableSubscription  # types only

# ARRAY child (the STXM map, or any 2-D array viz):
arr_client = run_client["primary"]["Counter1"]   # an ArrayClient
asub = arr_client.subscribe()                     # -> ArraySubscription; segments = path_parts
asub.new_data.add_callback(on_array_update)       # callback runs on a bg ThreadPool thread
asub.start_in_thread(start=1)                      # start=1 replays from seq 1 (catch the first write)
# ...
asub.disconnect()                                  # joins the daemon thread

# TABLE child (e.g. the stream `internal` table for table/plot/scatter viz):
tbl_client = run_client["primary"]["internal"]     # a DataFrameClient
tsub = tbl_client.subscribe()                      # -> TableSubscription
tsub.new_data.add_callback(on_table_update)
tsub.start_in_thread(start=1)
```

**Inline payload decode (no HTTP refetch):**

```python
def on_array_update(update):                # update is LiveArrayData (array-data)
    arr = update.data()                     # np.ndarray, decoded from update.payload locally
    offset = update.offset                  # e.g. (row, 0) — the patched line's offset, or None for the first write
    shape  = update.shape                   # the PATCHED line's shape (e.g. (1, nx)), NOT the full array

def on_table_update(update):                # update is LiveTableData (table-data)
    df = update.data()                      # pandas/arrow table of the appended rows
    partition = update.partition            # 0
    append = update.append                  # True
```

`LiveArrayData.data()` (`client/stream.py:733`) deserializes `self.payload`
bytes locally → no refetch. (The catalog write/patch path only ever emits
`array-data` with an inline payload, never `array-ref`; confirmed by the prior
spike: 0 refetch messages.)

### Container subscription — what it's good for

`container-child-created` is the signal that a **new child node appeared** (with
`update.key` + `update.structure_family`). For the panel this is how to discover
streams/columns that show up mid-run (e.g. a stream that is only created after
the first descriptor). It is NOT a data feed. `LiveChildCreated.child()`
(`client/stream.py:709`) builds a client for the new child, on which you then
open an `ArraySubscription`/`TableSubscription`.

### Panel routing pattern (for Tasks 2-4)

Because data messages carry no path, the bridge must keep a **1:1 map of
subscription → target viz/field**. Open one `ArraySubscription` (or
`TableSubscription`) per child the active viz reads; the callback closure
already knows the source. Optionally also open a `ContainerSubscription` on the
run/stream to catch late-appearing children and open further per-child subs.

---

## PROBE B — does the patched TiledWriter persist a streamable 2-D map?

Drove the **real** Phase-2b plan `stxm_fly_raster` through Lightfall's
`BlueskyEngine` (`get_engine("bluesky")`) with `TiledService.configure(...).
connect()` pointed at the local streaming server (which auto-subscribes the
patched `ThreadedTiledWriter` — confirmed `writer wired? True`). Ran twice:
`nx=10` (the `StxmFlyRasterPlanPlugin` default) and `nx=32`.

### Result

```
[B/nx=10]  verdict = TABLE_COLUMN
           the `internal` table create 500'd: "Unsupported PyArrow type: list<item: double>"
           (server: tiled/adapters/sql.py:764 arrow_field_to_sqlite_type)
           -> Counter1 is a per-row list column; the run is left with primary
              present but no readable `internal` (composite columns = None).

[B/nx=32]  verdict = ARRAY
           node path     = primary/Counter1
           structure     = array  (StructureFamily.array)
           shape         = (4, 32) = (ny, nx)   <-- streamable 2-D map, grows by patch(extend=True)
           NODE REPORT (V3 composite — each data_key is a direct array child of the stream):
             primary/Counter1   array   (4, 32)
             primary/SampleX    array   (4, 32)
             primary/seq_num    array   (4,)
             primary/time       array   (4,)
             primary/SampleY    array   (4,)
             primary/ts_*       array   (4,)
```

### Why (root cause — upstream writer routing)

`bluesky_tiled_plugins...tiled_writer._RunWriter.descriptor` (line 875-881)
routes a data_key to the **zarr array path** only when:

```python
("external" not in val) and val.get("dtype") == "array" and (0 <= max_array_size < sum(val["shape"]))
```

- The flyer's `describe_collect` (`lightfall_pystxmcontrol/flyer.py:60`) declares
  `Counter1` and `SampleX` as `dtype:"array", shape:[nx]`.
- Upstream default `max_array_size = 16` (`MAX_ARRAY_SIZE`, line 59). Lightfall's
  `TiledService._subscribe_writer` builds `TiledWriter(self._client, batch_size=1)`
  and does **not** override `max_array_size` (`services/tiled_service.py:769`).
- So the test is `16 < nx`:
  - `nx=10` → `16 < 10` False → stays in the tabular `internal` table as a
    `list<double>` column (the **`tiled_writer_internal_array_shape` hazard**).
  - `nx=32` → `16 < 32` True → routed to `_int_array_keys`, written via
    `desc_node.write_array(...)` then grown by `arr_client.patch(..., offset=
    arr_client.shape[:1], extend=True)` (`tiled_writer_patch.py:240-257`) →
    one event per row → **2-D `(ny, nx)` array, appended per line** = the
    streamable STXM map.

> The `nx=10` SQLite 500 (`Unsupported PyArrow type: list<item: double>`) is
> partly a **local-backend artifact** — the production `als-tiled` runs
> **PostgreSQL**, whose SQL adapter may store list columns. But it does not
> change the architecture verdict: at `nx<=16` the detector is a per-row list
> column, never a standalone `array` node, so **it is not streamable via
> `array-data` and cannot drive a live STXM map.**

### Decision for Task 5 (the minimal fix)

To guarantee the fly-scan persists as a streamable 2-D `(ny, nx)` array
**regardless of `nx`**, force the detector key down the array path. Cleanest
minimal options (pick in Task 5):

1. **Set `max_array_size=0` when constructing the writer.** With `0 <= 0 < nx`
   true for any `nx>=1`, every `dtype:"array"` key routes to the zarr array path.
   But this is a *global* writer knob (affects all array-valued keys for every
   plan) — change it in `TiledService._subscribe_writer`
   (`TiledWriter(self._client, batch_size=1, max_array_size=0)`), and weigh the
   blast radius (small AreaDetector ROI time-series would also become arrays).
2. **Or** raise the per-key route locally: ensure the STXM line key always has
   `sum(shape) > max_array_size`. Not controllable from the plugin without
   touching the writer, so option 1 is the lever.

No new writer class is needed — the existing patched `TiledWriter` already
produces the correct growing 2-D array once the key is routed to the array path.
The **node path the STXM viz subscribes to is `<run>/primary/Counter1`** (or
whatever the flyer's detector key is named; the Phase-2b backend names the flyer
`STXMLineFlyer`, the smoke/plan path names it `Counter1`). Confirm the live key
name in Task 5/6.

---

## Integration surface for Tasks 2-4 (exact symbols + line regions)

All in the lightfall repo on `feature/streaming-viz-updates`.

### `src/lightfall/visualization/base_visualization.py`
- `BaseVisualization(QWidget)` ABC. Abstract methods: `can_handle` (47-50,
  staticmethod), `set_run` (52-54), `get_streams` (56-58), `set_stream` (60-62),
  `get_fields` (64-66), `set_field` (68-70), **`refresh` (72-74, abstract)**.
- State init `__init__` (41-45): `_run`, `_stream_name`, `_field_name`.
- **Task 2 add** `def on_stream_update(self, update) -> None:` with default body
  `self.refresh()`. (Per spec: GUI-thread, every existing viz becomes
  push-driven with no per-viz change; STXM overrides it.)

### `src/lightfall/ui/panels/visualization_panel.py`
- **State** (`__init__` 87-98): `_entry`, `_current_widget`, `_current_proxy`,
  `_refresh_timer`, `_follow_live`, `_live_run_uid`, **`_is_live`** (95),
  `_sync_retries`, `_follow_action`.
- **2-second timer (REMOVE in Task 3):**
  - `_start_refresh` (498-505) — builds `QTimer`, `start(2000)`, connects
    `_on_refresh_tick`.
  - `_stop_refresh` (507-512).
  - `_on_refresh_tick` (514-534) — calls `_current_widget.refresh()` AND does
    **stop-doc detection** (526-534: `_entry.refresh()` then checks
    `metadata.get("stop")`, sets `_is_live=False`, `_stop_refresh()`).
  - `_update_refresh` (477-482) — `if self._is_live and self.is_active:
    _start_refresh() else _stop_refresh()`.
- **Activation path:**
  - `_on_activated` (484-492) — calls `_sync_to_live_run()`, a catch-up
    `_current_widget.refresh()`, `_update_refresh()`. (Task 3: replace
    `_update_refresh` with `StreamBridge` (re)connect; keep the catch-up
    `refresh()`.)
  - `_on_deactivated` (494-496) — `_stop_refresh()` (Task 3: bridge.disconnect()).
  - `_activate_widget` (290-334) — sets `_is_live = entry.metadata.get("stop")
    is None` (331) then `_update_refresh()` (332). The initial `refresh()`
    happens implicitly via `set_stream`→`set_field`. (Task 3: after building the
    widget, point/connect a `StreamBridge` at the active node and wire its
    GUI-thread signal to `widget.on_stream_update`.)
  - `_sync_to_live_run` (457-473) and `_schedule_sync_retry` (449-455),
    `_resolve_entry` (429-447) — live-follow; unchanged by the timer removal.
- **Engine doc-stream subscription (where stop-doc detection MUST MOVE):**
  - `_connect_engine` (578-588) — `get_engine().sigOutput.connect(
    self._on_engine_document)`; stores `self._engine`.
  - **`_on_engine_document` (590-614)** — `@Slot(str, dict) @gui_thread_only`.
    Already handles `start` (594-597), `descriptor` (598-603), and **`stop`
    (604-614)**: when `doc["run_start"] == self._live_run_uid` it does a final
    `refresh()`, sets `_is_live=False`, `_update_refresh()`.
    **=> When the 2s timer is removed (Task 3), run-completion / stop-doc
    detection lives HERE (`_on_engine_document`, the `stop` branch). This is the
    single GUI-thread place that already sees stop docs.** Move the
    `_on_refresh_tick` stop-detection logic into this branch (and have it
    `StreamBridge.disconnect()` instead of `_stop_refresh()`), removing the
    `_entry.refresh()`/`metadata["stop"]` poll entirely.
  - `_on_closing` (618-626) — `_stop_refresh()` + engine `sigOutput.disconnect`.
    (Task 3: also `StreamBridge.disconnect()` here, BEFORE widget teardown — the
    theater-teardown rule.)
- **Teardown ordering note:** `_set_current_widget` (336-360) hides/removes the
  old proxy. Per the theater-teardown rule, `StreamBridge.disconnect()` must run
  **before** the widget/proxy is hidden or removed.

### `src/lightfall/services/tiled_service.py`
- **`client` property (172-174)** — returns `self._client` (the authenticated
  root client) or `None`. **Reuse this** for the bridge (per spec). Note:
  `VisualizationPanel._resolve_entry` currently reads `service._client` directly
  (439); the bridge should use the public `service.client`.
- **`_install_tiled_stream_ws_proxy_patch` (973-1037)** — module-level, installed
  eagerly at import (1037). Monkey-patches `tiled.client.stream.connect` so WS
  subscriptions honor Lightfall's SOCKS proxy (live `ProxySettingsProvider`
  lookup per call; no-op when no proxy). Idempotent. **The StreamBridge gets
  SOCKS-proxied WS for free** simply because it imports/uses
  `tiled.client.stream` — no extra wiring. (Guard-rail: the prior spike noted
  `disconnect()` can hang against a cache-less server while stamina retries; bound
  disconnect / only subscribe when streaming is available.)
- `_subscribe_writer` (754-834) builds `TiledWriter(self._client, batch_size=1)`
  — **the Task-5 `max_array_size=0` lever lives here** if chosen.

### Viz models (read-only references)
- **Array viz** — `src/lightfall/visualization/widgets/image_stack.py`:
  `set_run` (135-136), `get_streams` (138-146), `set_stream` (148-162) caches
  `_stream`+`_data_keys`, `set_field` (193-265) resolves the `ArrayClient`
  (`self._stream[field_name]`) → `_image_client`, `refresh` (267-286) polls
  `_image_client.shape[0]` for new frames. An STXM-map viz mirrors this shape
  (open `self._stream["Counter1"]` ArrayClient) and **overrides
  `on_stream_update`** to blit `update.data()` at `update.offset` row.
- **Table viz** — `src/lightfall/visualization/widgets/table.py`: `set_stream`
  (163-172), `set_field` (178-180)→`_reload`, `refresh` (182-183)→`_reload`
  (193-215), reading via `lightfall.utils.tiled_helpers.read_events(self._stream)`
  (189-191). Default `on_stream_update`→`refresh()`→`_reload()` works unchanged.

---

## Environment note (packaging gaps in this venv, not 0.2.9 limits)

The spike needed two things the prior spike didn't:
- **`adbc_driver_sqlite` 1.11.0** — required for a SQL-backed appendable table
  (the writer's `internal` table). Installed into the venv for this spike.
  (Production uses PostgreSQL; this is only for the local SQLite catalog.)
- The streaming-cache deps from the prior spike (`zarr 2.18.7`, `redis`,
  `openpyxl`, `canonicaljson`) were already present.
- SQL `writable_storage` URI form for Windows is `sqlite:///C:/...` (THREE
  slashes; four → mangled `\C:\...`).

## Spike server config (scratchpad, not committed)

`config_viz_streaming.yml`: `streaming_cache: {uri: memory}`, anonymous + single
`single_user_api_key`, BOTH a `file://` writable_storage (arrays/zarr) AND a
`sqlite:///` writable_storage (appendable tables). Served via
`python -m tiled serve config <yaml> --host 127.0.0.1 --port <free>`.

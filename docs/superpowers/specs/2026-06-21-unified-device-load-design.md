# Unified device-load architecture — design

**Status:** direction approved (Ron, 2026-06-21); spec for review
**Date:** 2026-06-21
**Area:** `lightfall` core — `DeviceBackend`, `DeviceCatalog`, `DeviceConnectionManager`
**Author:** Ayaka (with Ron)

## Summary

Replace the two divergent device-load paths (startup `DeviceCatalog.connect()` vs
late `DeviceCatalog.add_and_connect_backend()`) with **one standard pipeline for
every backend**: *load metadata → instantiate → connect*, run in the background,
concurrently, never blocking the GUI thread, with one slow/timed-out device
unable to block the rest. The backend interface becomes **abstract over the
device layer** (ophyd today, swappable later).

## Motivation

- **A real bug today:** the late path runs `backend.connect()` (and the phased
  background-connect it triggers) on a worker thread that exits the instant
  `connect()` returns. The connection manager's phase-1 `QThreadFuture` callback
  (`_on_phase1_done`, which starts phase 2) is bound to that dead worker, so
  phase 2 (`wait_for_connection`) never runs and devices sit at `CONNECTING`
  forever. The startup path runs on the main thread, so its callbacks fire —
  which is why "standard happi loading worked." (Observed on ws5: 98 devices
  instantiated, 0 connected.)
- **No reason for two paths.** CMS now uses the happi backend like everything
  else; a plugin-contributed backend should load exactly like a startup one.
- **Requirements (Ron):** (1) instantiate **and** connect, queued from the
  device list, in the background; (2) never block the main thread; (3) concurrent
  — one device holding up/timing out must not block the others; (4) **one**
  standard `load metadata → instantiate → connect` path for **all** backends;
  (5) abstract enough to swap the device abstraction layer in the future.

## Current architecture (as-is)

- `DeviceBackend.connect()` **conflates** metadata-load and connection: e.g.
  `HappiBackend.connect()` loads happi metadata *and* (in `background` mode)
  calls `_start_background_connections()` → `DeviceConnectionManager.connect_all_phased()`.
- Two catalog entry points: `connect()` (startup, **main thread**) and
  `add_and_connect_backend()` (late, runs `backend.connect()` on a **worker**).
- `DeviceConnectionManager.connect_all_phased()`: phase 1 instantiates all ophyd
  objects on one thread (to batch caproto PV searches), phase 2 calls
  `wait_for_connection` on parallel `QThreadFuture`s. The phase callbacks are
  bound to whichever thread called `connect_all_phased` — fine from main, broken
  from a transient worker.
- `DeviceInfo` already carries `_ophyd_device` (the instantiated object) and a
  `DeviceStatus` (UNKNOWN/CONNECTING/CONNECTED/FAILED).

## Target design (to-be)

### 1. `DeviceBackend` = metadata + device-layer hooks (abstract)

Split the conflated `connect()` into:

- `load_metadata() -> list[DeviceInfo]` — return device metadata only; **no**
  instantiation, **no** connection. May be slow (DB / network), so the catalog
  runs it on a background thread.
- `instantiate(info: DeviceInfo) -> object` — build the device object for one
  `DeviceInfo` (happi `result.get()`, mock construction, …).
- `check_connection(obj, timeout) -> bool` — block until the object is connected
  or the timeout elapses (ophyd `wait_for_connection`, mock immediate-True, …).

These three hooks are the **only** device-layer-specific code. The catalog and
connection manager call them generically — so swapping ophyd for another layer
(req 5) means writing a backend with these hooks, nothing else changes.

(`connect()`/`disconnect()` for whole-backend liveness remain; the data-query
methods — `get_device`, `list_devices`, … — are unchanged.)

### 2. `DeviceCatalog` = one `add_backend` pipeline

A single path, used at startup **and** for late plugin backends:

```
add_backend(backend)
  → [background]  infos = backend.load_metadata()
  → [main]        register infos (status UNKNOWN) + emit device_added
  → [main]        connection_manager.connect_devices(backend, infos)
```

- Orchestration is **kicked from the main thread** (the catalog is a main-thread
  `QObject`), so the connection manager's per-device `QThreadFuture` callbacks
  always marshal to main — fixing the dead-worker bug structurally.
- `connect()` (startup) becomes "add each configured backend via `add_backend`."
  `add_and_connect_backend()` is **removed** (folded into `add_backend`).
- The metadata load is non-blocking (background); the only main-thread work is
  cheap registration + dispatch.

### 3. `DeviceConnectionManager` = concurrent, bounded, isolated, generic

- For each `DeviceInfo`: one background task does `instantiate()` then
  `check_connection()`, updating status UNKNOWN → CONNECTING → CONNECTED/FAILED
  and emitting per-device signals.
- **Bounded concurrency** (a pool of N, configurable; default ~8–16) so 100+
  devices don't spawn 100+ threads, but a slow/timeout device only ties up one
  slot and never blocks the queue's progress (req 3).
- Callbacks/state updates marshal to the main thread (now reliable thanks to the
  early-invoker fix). The manager is **ophyd-agnostic** — it only calls the
  backend hooks (req 5).
- Per-device timeout (configurable, as today via `set_device_timeout`).

### 4. Status / signals (unchanged contract)

`device_connecting` / `device_connected` / `device_failed` and `DeviceStatus`
transitions stay as they are, so panels / `mark_device_live` / the device tree
keep working. `DeviceInfo._ophyd_device` is still populated with the instantiated
object.

## Open questions

- **caproto search batching.** Phase-1-serial-instantiate today batches PV
  searches into one caproto round (an efficiency win). A fully per-device
  pipeline loses that. Options: (a) drop it (instantiate is 1–50 ms/device — not
  the bottleneck; the timeouts are in connect, which stays concurrent); (b)
  expose an optional `prepare(infos)` backend hook that does the batch
  pre-instantiate, called once before the concurrent connect phase. Leaning (a)
  for simplicity + abstraction; revisit if box timing regresses.
- **`check_connection` shape.** Blocking `wait_for_connection(timeout)` in a pool
  thread (simple) vs poll `is_connected` + connection callbacks (no thread per
  wait). Blocking-in-pool is simpler and bounded by the pool; start there.
- **Pool size default** and whether it's a preference.

## Testability vs box validation

- **Unit-testable (no ophyd/IOC):** the whole pipeline with a **fake backend**
  (`load_metadata`/`instantiate`/`check_connection` returning canned
  values/delays/failures) — verify: one path for startup & late; background +
  non-blocking; concurrency + isolation (a hanging device doesn't block others);
  status transitions; callbacks land on the main thread. This is where the
  dead-worker regression gets a real regression test.
- **Box-validated (ws5):** 101 CMS happi devices instantiate + connect to live
  IOCs, status reaches CONNECTED, concurrency/timeout behavior under real
  latency.

## Migration / blast radius

- `DeviceBackend` interface change touches **all** backends: `MockBackend`
  (synchronous instantiate → hooks), `BCSBackend`, `HappiBackend` (drop
  `_start_background_connections`; provide `instantiate`/`check_connection`).
- `add_and_connect_backend` callers (the plugin loader's `device_backend`
  registration; the CMS endstation) switch to `add_backend`.
- Public catalog query API unchanged; UI consumers unaffected.

## Risks

- Broad interface change across backends — covered by the fake-backend unit
  suite + box validation.
- Concurrency correctness (pool, cancellation on disconnect, re-entrancy) —
  exercised by the fake-backend tests with injected delays/failures.

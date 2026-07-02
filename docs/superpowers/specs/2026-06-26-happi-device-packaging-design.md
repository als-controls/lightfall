# Happi-based device packaging for Lightfall plugins

**Status:** Design — pending review
**Date:** 2026-06-26
**Related:** [`2026-06-16-device-backend-plugin-type-design.md`](2026-06-16-device-backend-plugin-type-design.md), [`2026-06-21-unified-device-load-design.md`](2026-06-21-unified-device-load-design.md), [`2026-06-23-pystxmcontrol-ophyd-integration-design.md`](2026-06-23-pystxmcontrol-ophyd-integration-design.md)

## Problem

Plugins want to **ship a device set packaged inside their own repo**. Today there are two ways to do that, and they have diverged badly:

- **CMS (`lightfall-endstation-cms`)** ships a packaged `cms_happi.json` (103 EPICS devices) and its `DeviceBackendPlugin.create_backend()` returns `HappiBackend(path="…/cms_happi.json", instantiate="none")`. This is the good path: devices are *data*, loaded by the maintained built-in happi backend.
- **pystxm (`lightfall-pystxmcontrol`)** hand-wrote a full `DeviceBackend` subclass (`PystxmStxmBackend`, ~310 lines re-implementing the ~20-method ABC). On 2026-06-26 it crashed at startup (`PystxmStxmBackend does not implement load_metadata()`) because it had drifted out of sync with the unified load pipeline ([`2026-06-21`](2026-06-21-unified-device-load-design.md)). A hot-fix migrated it, but the underlying problem stands: **hand-writing the backend ABC is a drift trap.**

The two cases are the *same need* solved two ways. We want **one canonical way to package devices with a plugin: a happi database**, and we want it to work for *code-built ophyd-async* device sets (pystxm) as well as *data* EPICS device sets (CMS).

Two things are missing today:

1. **Ergonomics.** Even the good (CMS) path makes the author hand-wire `create_backend()` plus a packaged-resource path resolver. There is no declarative "here is my packaged happi JSON" plugin type.
2. **Ophyd-async connection.** Lightfall has **zero ophyd-async awareness** (verified: no `ophyd_async` import anywhere in `src/lightfall`). The device connection pipeline (`connection_manager._instantiate_and_connect` → `instantiate()` → `check_connection()`) only drives *classic* ophyd connection (`wait_for_connection()` / poll `.connected`). Nobody ever calls `await device.connect(mock=…)`. For pystxm this is fatal, not cosmetic: `PystxmAxis.connect()` is where the device *builds its simulated motor and wires its signal backends*. A happi-constructed pystxm device without an awaited `connect()` is a hollow shell.

## Goal

Make a packaged happi JSON the single device-packaging mechanism for plugins, and teach lightfall core to load **and connect** ophyd-async devices through the existing `HappiBackend`. Concretely: pystxm ships a `pystxm_happi.json`, retires `PystxmStxmBackend`, and a new declarative `HappiDatabasePlugin` base class makes the whole thing ~4 lines.

## Current flow (as-is)

```
pyproject  [project.entry-points."lightfall.plugins"]
  → manifest.py  PluginEntry(type_name="device_backend", import_path="…:SomePlugin")
  → PluginLoader, post-login wave            (plugins/loader.py:696-720)
  → pref gate  device_plugin_<name>_enabled
  → plugin.create_backend() → DeviceBackend
  → DeviceCatalog.add_and_connect_backend()   (devices/catalog.py:155)
  → unified pipeline (devices/catalog.py:166-283):
        worker:  backend.connect()? → backend.load_metadata() → list[DeviceInfo]
        main:    register infos, emit signals
        DeviceConnectionManager.connect_devices() →
            per device (worker thread):  obj = backend.instantiate(info)
                                         ok  = backend.check_connection(obj, timeout)
```

Built-in backends (`mock`, `bcs`, `happi`) are selected at startup in `main._setup_devices()` via `device_{mock,bcs,happi}_enabled` prefs. Plugin-contributed backends are added in the **post-login** plugin wave. Both use the same `_load_and_connect_backend` pipeline.

**Engine event loop (load-bearing fact for Component B):** `BlueskyEngine` constructs its `RunEngine` with `loop=self._loop` (`acquire/engine/bluesky.py:250-255`). bluesky **1.14.6**'s `RunEngine.__init__` calls `_ensure_event_loop_running(loop)` (`run_engine.py:419, 2829`), which starts `loop.run_forever()` on a daemon thread. **The engine loop therefore runs continuously** (both the self-created and `adopt()` paths), so `asyncio.run_coroutine_threadsafe(coro, engine_loop)` is safe at startup/idle — and it is the *same* loop the RunEngine drives the device on during plans, so loop affinity is correct. Lightfall already uses this pattern in `ipc/service.py`.

## Design

Three components, sequenced **B → A → C** (A is independent and can land any time; C depends on B).

### Component A — `HappiDatabasePlugin` (declarative backend plugin)

A new base class subclassing the existing `DeviceBackendPlugin`, so it slots into the loader unchanged.

**New file:** `src/lightfall/plugins/happi_database_plugin.py`

```python
class HappiDatabasePlugin(DeviceBackendPlugin):
    """DeviceBackendPlugin that vends a HappiBackend over a packaged happi JSON.

    Subclasses declare WHERE their database lives and the rest is implemented
    here. The database ships inside the plugin's wheel and is resolved via
    importlib.resources, so it works from an installed package (not just a
    source checkout).
    """

    # --- subclass declares these ---
    database_resource: ClassVar[tuple[str, str] | str]   # ("pkg.subpkg", "devices.json") or an abs path
    beamline: ClassVar[str | None] = None
    instantiate: ClassVar[str] = "background"            # "none" | "blocking" | "background"

    @property
    @abstractmethod
    def name(self) -> str: ...

    def database_path(self) -> Path:
        """Resolve database_resource to a concrete filesystem Path.

        - tuple ("package", "resource.json") → importlib.resources.files(package)/resource
        - str → treated as a filesystem path (absolute, or relative to cwd)
        Override for custom resolution.
        """

    def create_backend(self) -> DeviceBackend:
        return HappiBackend(
            path=str(self.database_path()),
            beamline=self.beamline,
            instantiate=self.instantiate,
        )
```

- **Resource resolution** uses `importlib.resources.files(package).joinpath(resource)` and an `as_file` context so it works from a zipped wheel. Because `HappiBackend` reads the path eagerly in `connect()`, `database_path()` materializes a real file (extract to a temp path if packaged inside a zip; for normal wheels it is already a real path).
- **Enable gate** is the existing `device_plugin_<name>_enabled` pref — handled by the loader (`loader.py:703`), no new wiring.
- **Default `instantiate="background"`** (matches the device-settings UI default). Note a verified subtlety: in the current unified pipeline the catalog calls `DeviceConnectionManager.connect_devices` for **every** registered device regardless of `instantiate_mode` — `connect_devices` has no mode gate (`connection_manager.py:272-336`), and the mode-branching `_discover_devices` path is no longer called from `connect()` (`happi.py:638`). So device connection — and therefore Component B's `check_connection` hook — fires regardless of the mode value; the `instantiate` choice is low-stakes for this design. CMS passes `"none"` and additionally injects/connects its devices in its own post-login profile bootstrap; it avoids *pre-login* EPICS construction by loading in the post-login plugin wave, **not** by the mode. (That `instantiate_mode` is now vestigial in the unified pipeline is a pre-existing wart, noted as an optional follow-up, out of scope here.)
- Built-in `HappiBackend` and the `DeviceBackendPlugin`/loader path are unchanged.

### Component B — ophyd-async connection support in the device pipeline

Teach the connection pipeline to connect an ophyd-async device after `instantiate()`.

**Detection (no hard `ophyd_async` dependency in core):** an object is "async-connectable" when `inspect.iscoroutinefunction(getattr(obj, "connect", None))` is true. This catches `StandardReadable` subclasses (`PystxmAxis`, `PystxmCounter`) and the plain-`Flyable` `PystxmLineFlyer` (which also exposes `async def connect`).

**New helper:** `src/lightfall/devices/async_connect.py`

```python
def connect_async_device(obj, *, mock: bool = False, timeout: float = 5.0,
                         loop_wait: float = 5.0) -> bool:
    """Drive `await obj.connect(mock=mock)` on the engine's running loop.

    Acquires the BlueskyEngine event loop (waiting up to loop_wait for it to
    exist), schedules obj.connect(mock=mock) via run_coroutine_threadsafe, and
    blocks up to `timeout` for completion. Returns True on success.
    Returns False (with a clear log) if no engine loop becomes available or
    connect() raises/times out — never hangs.
    """
```

- **Loop acquisition** via `get_engine()` and a new public accessor `BlueskyEngine.event_loop` (returns `self._loop`, or `None` before the RE is constructed). The helper polls for the loop up to `loop_wait` because the loop only exists after the engine's `RunEngine` is built in `_process_queue`. In normal operation the engine is initialized at startup and plugin backends connect post-login, so the loop is present; the bounded wait is a safety net, and absence resolves to a failed connection (device OFFLINE) with a clear message, **not a hang**.
- **Seam:** enhance `DeviceBackend.check_connection()` (`devices/base.py`) with an async-connectable branch that calls `connect_async_device(...)`. This is contract-consistent — `check_connection` is documented as "block until obj is connected or timeout; return connected," and for an async device "blocking until connected" *is* awaiting `connect()`. Placing it in the base makes it universal (any backend vending async devices benefits, including `HappiBackend`, which does not override `check_connection`). `MockBackend` overrides `check_connection` and is unaffected.
- **`mock` parameter:** default `False` (production EPICS connects for real; pystxm sim devices build their own sim backend in `connect()`). An optional per-backend opt-in (`HappiBackend(..., connect_mock=True)` surfaced as a `DeviceInfo.metadata` flag) is available for test/sim, default off. YAGNI: no UI for it.
- **Loop affinity:** because connect runs on the engine loop, later `set()`/`trigger()` during a plan (driven by the RunEngine on the same loop) are safe.

### Component C — convert pystxm to a packaged happi database

- **Author `src/lightfall_pystxmcontrol/devices/pystxm_happi.json`** with four entries:
  - `SampleX`, `SampleY` → `device_class="lightfall_pystxmcontrol.devices:PystxmAxis"`, `args=[]`, `prefix=""`, kwargs carrying the axis config.
  - `Counter1` → `…:PystxmCounter`, kwargs `{daq_config, dwell}`.
  - `STXMLineFlyer` → `…:PystxmLineFlyer`, kwargs `{daq_config, x_axis_config}`, **`name="STXMLineFlyer"`** (its constructor default is `"Counter1"`, which would collide).
  - Config dicts already live in `config.py` as plain JSON-able dicts, so they embed directly. **Risk to verify:** happi's template-filler must pass nested-dict kwargs through untouched; if it mangles them, fall back to the *factory-lookup* variant (entry passes a scalar key like `{"axis_name": "SampleX"}` and the constructor reads `config.DEFAULT_AXES[axis_name]`).
- **`PystxmBackendPlugin` becomes a `HappiDatabasePlugin`** with `database_resource = ("lightfall_pystxmcontrol.devices", "pystxm_happi.json")`, `beamline=…`, `instantiate="background"`.
- **Delete `PystxmStxmBackend`** (`backend.py`, ~310 lines) and the 2026-06-26 migration tests it carried. Keep `devices.py` / `flyer.py` / `config.py` unchanged.
- Existing pystxm plan + visualization plugins are unaffected.

## Data flow (after change)

```
plugin (HappiDatabasePlugin).create_backend() → HappiBackend(path=packaged JSON, instantiate="background")
  → catalog.add_and_connect_backend
  → load_metadata():  client.search() over the JSON → list[DeviceInfo]   (no construction)
  → per device:
        instantiate(info):       happi result.get() → constructs ophyd-async obj (NOT connected)
        check_connection(obj):   iscoroutinefunction(obj.connect)? →
                                 connect_async_device(obj, mock=False, timeout) →
                                 run_coroutine_threadsafe(obj.connect(mock=False), engine_loop).result(timeout)
                                 → device CONNECTED
```

## Error handling

- **happi not installed:** `HappiBackend.connect()` already logs and returns `False`.
- **Packaged resource missing:** `database_path()` raises a clear `FileNotFoundError` naming the package + resource.
- **Engine loop never appears:** `connect_async_device` returns `False` after `loop_wait`; device → OFFLINE/FAILED with a logged reason. No hang.
- **`connect()` raises or times out:** `connect_async_device` returns `False`; existing `DeviceConnectionManager` handling maps it to TIMEOUT/ERROR → device OFFLINE.
- **Name collision across backends:** existing catalog behavior skips the conflicting name with a warning (`catalog.py:211-218`).

## Testing

- **A:** `database_path()` resolves a packaged resource from an installed package; `create_backend()` returns a `HappiBackend` with the resolved path/beamline/instantiate; enable-pref gating is already covered at the loader level.
- **B:** `connect_async_device` connects a fake async device on a running loop and returns `True`; `check_connection` routes async vs. classic devices correctly; engine-loop-absent path returns `False` within `loop_wait` (no hang); an integration test connects a real ophyd-async sim device through the pipeline.
- **C:** `load_metadata()` over `pystxm_happi.json` returns the 4 devices; `instantiate()` returns the ophyd objects; after pipeline connect, `SampleX.readback` reads a real value and `Counter1` triggers; the existing fly-raster plan still runs. These replace today's `PystxmStxmBackend` tests.

## Out of scope (YAGNI)

- Multiple happi databases per plugin.
- Auto-generating happi JSON from a Python device registry.
- Per-device `mock` toggle in the UI.
- Migrating CMS to `HappiDatabasePlugin` (optional cleanup; CMS already works).

## Open questions for review

1. **happi nested-dict kwargs:** confirm happi 1.x passes nested-dict kwargs to constructors untouched; otherwise use the factory-lookup variant in Component C.
2. **`check_connection` as the seam:** agree it belongs in `DeviceBackend.check_connection` (universal) rather than a `HappiBackend`-only override?
3. **Default `instantiate` for `HappiDatabasePlugin`:** `"background"` (proposed). Low-stakes since the unified pipeline connects regardless of mode; keep it to match the UI default. Optional separate cleanup: make `instantiate_mode` actually gate the unified pipeline (out of scope here).
4. **`mock` default + opt-in shape:** default `False` with a `HappiBackend(connect_mock=…)` flag — acceptable?
5. **Engine-loop-not-ready policy:** bounded wait then fail (proposed) vs. proactively starting the engine queue processor from the connect helper.

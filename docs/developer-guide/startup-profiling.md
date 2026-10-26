# Startup Profiling

Lightfall includes a built-in startup timeline profiler (`lightfall.utils.startup_timeline`) that records the duration and nesting of startup phases from `main()` entry through the post-login plugin and device waves.

When disabled (the default), `startup_timeline.phase(...)` returns `contextlib.nullcontext()` so there is near-zero runtime overhead during normal operation.

## Enabling the Profiler

Set the `LIGHTFALL_STARTUP_PROFILE` environment variable before launching Lightfall:

```bash
# Record phase timings and write JSON + self-contained HTML waterfall
LIGHTFALL_STARTUP_PROFILE=1 lightfall
```

Any non-empty value other than `0` enables phase recording.

### Function-Level Profiling (`cprofile`)

To capture a Python `cProfile` dump alongside the phase timeline, set `LIGHTFALL_STARTUP_PROFILE=cprofile`:

```bash
LIGHTFALL_STARTUP_PROFILE=cprofile lightfall
```

This enables a `cProfile.Profile` instance at `mark_origin()` and dumps the stats when `finish()` runs. You can inspect the `.prof` file with `snakeviz`, `pstats`, or `gprof2dot`:

```bash
python -m pstats ~/.lightfall/diagnostics/startup-<YYYYmmdd-HHMMSS>.prof
```

## Output Location and Files

Five seconds after the Qt event loop begins (`event_loop_enter`), `startup_timeline.finish()` writes the captured timeline into the diagnostics directory:

- **Default directory:** `~/.lightfall/diagnostics/` (or the path configured via `LIGHTFALL_CRASH_LOG_DIR`)
- **`startup-<YYYYmmdd-HHMMSS>.html`** — Self-contained HTML waterfall chart with inline CSS and SVG showing each phase bar in start order, nested indentation, thread name, duration in milliseconds, and instant marks as vertical lines.
- **`startup-<YYYYmmdd-HHMMSS>.json`** — Raw timeline data (`total_s` and the list of `records` with `name`, `start_s`, `end_s`, `depth`, `thread`, and optional `error` / `instant` flags).
- **`startup-<YYYYmmdd-HHMMSS>.prof`** — Written only when `LIGHTFALL_STARTUP_PROFILE=cprofile`.

The path to the generated `.html` file is logged at `INFO` level.

## Adding a New Phase or Mark

Use `startup_timeline.phase(name)` as a context manager around any synchronous startup step, or `startup_timeline.mark(name)` for an instant milestone:

```python
from lightfall.utils import startup_timeline

with startup_timeline.phase("my_subsystem_init"):
    initialize_subsystem()
    with startup_timeline.phase("load_subsystem_cache"):
        load_cache()

startup_timeline.mark("subsystem_ready")
```

Nested `with startup_timeline.phase(...)` blocks on the same thread automatically increment the recorded `depth` so the HTML waterfall indents child phases under their parent. If an exception is raised inside a `phase(...)` block, the phase still records its `end_s` and marks `"error": True` before re-raising the exception unchanged.

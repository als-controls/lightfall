# Lightfall — Claude Code instructions

## Crash diagnostics

`lightfall.utils.crash_diagnostics` is the project's deterministic-debugging layer.
It is installed automatically by `lightfall.main` and exposes helpers for new code.

### Auto-installed (do not reinstall in tests/scripts unless isolating)

- `faulthandler.enable(all_threads=True)` — segfaults write per-thread Python
  tracebacks to `logs/diagnostics/fault.log` and stderr.
- `sys.excepthook` and `threading.excepthook` — unhandled exceptions log at
  CRITICAL through loguru, then chain to whatever was installed previously
  (Sentry's hooks, the default Python hook).
- `qInstallMessageHandler` bridge — Qt's internal warnings (thread-affinity
  violations, queued-connection failures) route into loguru instead of
  vanishing.
- On-demand thread dump: `kill -USR1 <pid>` (POSIX) / Ctrl-Break in the
  console window (Windows). Output goes to `fault.log`.

### When writing or reviewing slot/callback code

Use these tripwires liberally; they are no-ops in correct code:

```python
from lightfall.utils.crash_diagnostics import (
    assert_gui_thread,    # raises off the GUI thread; pass an obj for context
    assert_object_thread, # raises if not on obj.thread()
    gui_thread_only,      # decorator form; stack BELOW @Slot
    safe_call,            # safe_call(obj, "setText", "...") — checks isValid first
    valid_or_skip,        # context manager; yields None if wrapper is dead
)

@Slot(object)
@gui_thread_only
def _handle_value_received(self, value): ...
```

Apply `@gui_thread_only` to slots connected via explicit `QueuedConnection`,
to slots that touch widgets and could plausibly be invoked from a worker, and
to anything called inside Bluesky / ophyd / caproto callbacks. **Do not** use
it on methods that are intentionally called from worker threads.

Use `safe_call` / `valid_or_skip` when a long-lived QObject reference can
outlive the underlying C++ wrapper (deferred callbacks, dangling refs from
disconnected signals, etc.).

### When you do NOT have evidence of a specific crash

Do not go on a wide `@gui_thread_only` decoration spree. The decorator is
cheap, but adding it to methods that legitimately run off the GUI thread
turns correct code into spurious failures. Prefer instrumenting the
specific path that produced the crash you are chasing.

### When you DO have a crash

- Read `logs/diagnostics/fault.log` — segfaults dump per-thread Python
  tracebacks there.
- Check loguru output around the crash time — the excepthook catches what
  Qt's `notify()` boundary swallows.
- Trigger an on-demand thread dump if the process is hung but alive.
- `lightfall.utils.error_collector.ErrorCollector.get_recent_errors()` returns
  the last 50 ERROR+ records for bug-report dialogs.

## Surrounding infrastructure (for context)

- Logging: `from lightfall.utils.logging import logger` — loguru, configured in
  `app.initialize()`. File handler is opt-in via `log_file=...`.
- Sentry: auto-initialized in `main()`. `@sentry_slot()` (in `lightfall.utils.sentry`)
  is the Slot decorator that captures exceptions to Sentry; the QApplication
  subclass also wraps `notify()` for the same purpose.
- Threading: `lightfall.utils.threads` provides `QThreadFuture`, `ManagedThreadPool`,
  `invoke_in_main_thread`, `is_main_thread`, and the `@method` / `@iterator`
  decorators. Prefer these over raw `threading.Thread` so shutdown is clean.

## Visualization theming (pyqtgraph)

Use `from lightfall.visualization import pg` instead of `import pyqtgraph as pg`
in viz widgets. The wrapper re-exports the full pyqtgraph namespace and
adds themed subclasses for items that hold their own pen/brush:

- `pg.PlotDataItem` — default pen tracks `VisualizationColors.primary_line`.
- `pg.ScatterPlotItem` — default brush tracks `primary_line`.
- `pg.InfiniteLine` — default pen tracks `highlight` (good for crosshairs).
- `pg.series_pen(i)` — palette color for the *i*-th series, cycles.
- `pg.retheme_all()` — re-applies the current palette to every live themed
  item (called automatically from `LFMainWindow._apply_theme`).

Background, axis, tick, and grid colors are handled globally by
`apply_pyqtgraph_theme()`, also called from `_apply_theme`. Items
constructed with explicit `pen=` / `brush=` keep the caller's value across
theme changes.

For widgets that need more than per-item retheme (e.g. a custom legend or
overlay), connect to `ThemeManager.colors_changed` and recompute colors via
`colors_from_theme` / `get_visualization_colors` from
`lightfall.visualization.theme` (see `visualization/pg.py` for the pattern).

## Test runner

Always use the venv Python: `.venv/Scripts/python -m pytest`. Bare `pytest`
resolves to the system Python 3.10 which cannot import lightfall.

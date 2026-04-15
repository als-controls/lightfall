# RunEngine Spinner Indicator

**Date:** 2026-04-15
**Status:** Design
**Scope:** `ncs/src/lucid/ui/widgets/runengine_control.py`

## Summary

Replace the colored-dot `StatusIndicator` in `RunEngineControlWidget` with a
`SpinnerIndicator` that renders the ALS logo. The logo spins while the
RunEngine is running, goes grayscale when idle/stopped, and flashes red for
1.5 seconds on errors. Paused state is shown as the static color logo at
50% opacity.

## Motivation

The current 12x12 colored dot conveys state through color alone. Replacing
it with a recognizable spinning brand mark gives stronger at-a-glance
feedback that the engine is actively executing, and ties the beamline UI
visually to the ALS facility.

## Asset

- **Source:** `C:\Users\rp\PycharmProjects\control-system-management\csm-frontend\public\logo.png`
  (290 KB, transparent background)
- **Destination:** `ncs/src/lucid/ui/resources/logo.png`
- **Loading:** `importlib.resources.files("lucid.ui.resources") / "logo.png"`,
  read as bytes and loaded into a `QPixmap` via `loadFromData`. Works in
  editable installs and packaged builds.
- **Packaging:** Ensure `lucid.ui.resources` is included as package data in
  `ncs/pyproject.toml` (add to `[tool.hatch.build.targets.wheel]` package
  inclusion if not already implicit).

## Pre-baked Pixmaps

Three 24x24 pixmaps are computed once at widget construction and cached as
instance attributes. All are built from the source QImage at 24x24 with
`Qt.SmoothTransformation`.

### Color (running, paused)
Original PNG scaled to 24x24. No pixel manipulation.

### Gray (idle, stopped, no-engine)
Per pixel:
```
luminance = (r * 299 + g * 587 + b * 114) // 1000
output    = (luminance, luminance, luminance, a)
```
Alpha preserved so soft edges stay anti-aliased against any background.
(Plain `QImage.Format_Grayscale8` zeroes alpha — hence the manual pass.)

### Red (error flash)
Per pixel:
```
luminance = (r * 299 + g * 587 + b * 114) // 1000
output    = (luminance, 0, 0, a)
```
The luminance drives the red channel, preserving the logo's internal
contrast. Brighter parts of the original become bright red; darker parts
become dark red. Alpha preserved.

## State → Visual Mapping

| Engine state | Pixmap | Spinning | Opacity |
|---|---|---|---|
| `idle` | Gray | No | 1.0 |
| `running` | Color | Yes | 1.0 |
| `stopping` | Color | Yes | 1.0 |
| `paused`, `pausing` | Color | No | 0.5 |
| `aborting` | Red | No | 1.0 |
| `panicked` | Red | No | 1.0 |
| no engine attached | Gray | No | 1.0 |
| *error flash active* | Red | No | 1.0 |

Error flash takes precedence over state mapping while active.

## Spin Mechanism

- `QTimer` at 30 fps (~33 ms interval)
- Each tick: `self._rotation = (self._rotation + 12) % 360` then
  `self.update()` → 360°/sec, one full rotation per second
- Timer starts only when entering a spinning state; stops otherwise to
  avoid redraw churn when the engine is idle for long periods
- `_rotation` is preserved across start/stop so the logo doesn't snap back
  to 0° when briefly paused

## Error Flash

- `QTimer` single-shot, 1500 ms
- `flash_error()` method sets `_flash_active = True`, starts (or restarts)
  the timer, calls `self.update()`
- On timeout: `_flash_active = False`, `self.update()`
- Re-entrant: calling `flash_error()` while already flashing restarts the
  timer rather than stacking

### Triggers (wired in `RunEngineControlWidget._connect_signals`)

- `engine.sigException` → `flash_error()` (plan raised an exception)
- `_on_state_changed`: if new state is `"aborting"` or `"panicked"`,
  call `flash_error()` in addition to the normal state update

## paintEvent

```python
painter = QPainter(self)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

pixmap = self._red_pixmap if self._flash_active else self._pixmap_for_state()
opacity = 0.5 if self._status in ("paused", "pausing") and not self._flash_active else 1.0

painter.setOpacity(opacity)
painter.translate(12, 12)
painter.rotate(self._rotation)
painter.translate(-12, -12)
painter.drawPixmap(0, 0, pixmap)
```

## Public API

Drop-in replacement for `StatusIndicator`. The caller
(`RunEngineControlWidget._update_state`) does not change its invocation
pattern.

```python
class SpinnerIndicator(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None: ...
    def set_status(self, status: str) -> None: ...
    def flash_error(self) -> None: ...
```

`set_status` additionally controls whether the spin timer is running, based
on whether the new status is a spinning state.

## Wiring in RunEngineControlWidget

Two edits to `_connect_signals` / `_disconnect_signals`:

```python
self._engine.sigException.connect(self._on_exception)
```

```python
@Slot(Exception)
def _on_exception(self, ex: Exception) -> None:
    self._status_indicator.flash_error()
```

And in `_on_state_changed`:

```python
if state in ("aborting", "panicked"):
    self._status_indicator.flash_error()
```

## Files Changed

- **New:** `ncs/src/lucid/ui/resources/__init__.py` (empty, marks package)
- **New:** `ncs/src/lucid/ui/resources/logo.png` (copied)
- **Modified:** `ncs/src/lucid/ui/widgets/runengine_control.py`
  - Replace `StatusIndicator` class with `SpinnerIndicator`
  - Update imports (remove unused `QColor`, keep `QPainter`, add `QPixmap`, `QImage`, `QTimer`)
  - Wire `sigException` and extend `_on_state_changed` for flash triggers
- **Modified:** `ncs/pyproject.toml` — include `lucid.ui.resources` package data if needed

## Testing

- Unit test: instantiate `SpinnerIndicator`, call `set_status("running")`,
  verify spin timer is active; call `set_status("idle")`, verify timer stops
- Unit test: call `flash_error()`, verify `_flash_active` true, wait 1500 ms
  (use `QTest.qWait`), verify it clears
- Unit test: re-entrant flash — call flash_error twice with a 500 ms gap,
  verify it stays active for 500 + 1500 = 2000 ms total from first call
- Visual test (manual): run `RunEngineControlWidget` example, submit a plan,
  confirm logo spins; pause, confirm static + dimmed; raise an exception in
  the plan, confirm red flash

## Out of Scope

- QSS styling of the logo (effects are pixmap-baked)
- Theme awareness (logo is its own colorway; gray/red variants work on both
  light and dark backgrounds due to alpha preservation)
- Configurable spin speed
- `.qrc` compilation
- Replacing the logo with a different asset at runtime

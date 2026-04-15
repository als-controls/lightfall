# RunEngine Spinner Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the colored-dot `StatusIndicator` in the RunEngine control widget with a 24x24 spinning ALS logo that goes grayscale when idle, dims when paused, and flashes red on errors.

**Architecture:** A new `SpinnerIndicator` QWidget pre-bakes three pixmaps (color/gray/red) at construction by per-pixel luminance computation with alpha preserved. State changes drive a 30 fps rotation timer; `sigException` and entry into `aborting`/`panicked` states trigger a 1500 ms red flash via a single-shot timer.

**Tech Stack:** PySide6 (QPainter, QTimer, QPixmap, QImage), `importlib.resources` for packaged asset loading, pytest-qt for widget testing.

**Spec:** [2026-04-15-runengine-spinner-indicator-design.md](../specs/2026-04-15-runengine-spinner-indicator-design.md)

---

## File Structure

**New files:**
- `ncs/src/lucid/ui/resources/__init__.py` — empty marker so the directory is a Python package and `importlib.resources` can find it
- `ncs/src/lucid/ui/resources/logo.png` — copied from `C:\Users\rp\PycharmProjects\control-system-management\csm-frontend\public\logo.png`
- `ncs/tests/test_spinner_indicator.py` — unit tests for the new widget

**Modified files:**
- `ncs/src/lucid/ui/widgets/runengine_control.py` — replace `StatusIndicator` class with `SpinnerIndicator`; wire `sigException` and extend `_on_state_changed` in `RunEngineControlWidget`
- `ncs/pyproject.toml` — ensure `*.png` files under `src/lucid` are included in the wheel

---

## Task 1: Set up the resources package and copy the logo asset

**Files:**
- Create: `ncs/src/lucid/ui/resources/__init__.py`
- Create: `ncs/src/lucid/ui/resources/logo.png` (copy from external path)
- Modify: `ncs/pyproject.toml` (add force-include for PNG files)

- [ ] **Step 1: Create the resources package marker**

Create `ncs/src/lucid/ui/resources/__init__.py` with this exact content:

```python
"""Static asset resources (images, icons) bundled with the lucid UI."""
```

- [ ] **Step 2: Copy the logo PNG into the resources directory**

Run from the repo root (`C:/Users/rp/PycharmProjects/ncs`):

```bash
cp "C:/Users/rp/PycharmProjects/control-system-management/csm-frontend/public/logo.png" "ncs/src/lucid/ui/resources/logo.png"
```

Verify the file exists and is ~290 KB:

```bash
ls -la ncs/src/lucid/ui/resources/logo.png
```

Expected: file present, size around 290362 bytes.

- [ ] **Step 3: Add force-include for PNG files in pyproject.toml**

Hatchling by default includes Python source but may skip binary asset files. Make the inclusion explicit. In `ncs/pyproject.toml`, find the section:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/lucid"]
```

Replace with:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/lucid"]

[tool.hatch.build.targets.wheel.force-include]
"src/lucid/ui/resources/logo.png" = "lucid/ui/resources/logo.png"
```

- [ ] **Step 4: Verify the asset can be discovered via importlib.resources**

Run this one-liner from the repo root:

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -c "from importlib.resources import files; p = files('lucid.ui.resources') / 'logo.png'; print(p, p.is_file(), p.read_bytes()[:8])"
```

Expected output: prints the resolved path, `True`, and the PNG magic bytes `b'\x89PNG\r\n\x1a\n'`.

- [ ] **Step 5: Commit**

```bash
cd ncs && git add src/lucid/ui/resources/__init__.py src/lucid/ui/resources/logo.png pyproject.toml
git commit -m "Add ALS logo asset and resources package for UI widgets"
```

---

## Task 2: Write failing tests for SpinnerIndicator pixmap baking

**Files:**
- Create: `ncs/tests/test_spinner_indicator.py`

- [ ] **Step 1: Write the failing tests for pixmap construction**

Create `ncs/tests/test_spinner_indicator.py` with:

```python
"""Tests for the SpinnerIndicator widget."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage

from lucid.ui.widgets.runengine_control import SpinnerIndicator


@pytest.fixture
def indicator(qtbot):
    """Create a SpinnerIndicator and register it with qtbot."""
    widget = SpinnerIndicator()
    qtbot.addWidget(widget)
    return widget


class TestPixmapBaking:
    """The three color variants must be pre-baked at construction."""

    def test_widget_is_24x24(self, indicator):
        assert indicator.width() == 24
        assert indicator.height() == 24

    def test_color_pixmap_exists_and_is_24x24(self, indicator):
        pm = indicator._color_pixmap
        assert not pm.isNull()
        assert pm.width() == 24
        assert pm.height() == 24

    def test_gray_pixmap_pixels_have_equal_rgb(self, indicator):
        """In the gray pixmap every opaque pixel must have R == G == B."""
        img: QImage = indicator._gray_pixmap.toImage()
        opaque_pixels = 0
        for y in range(img.height()):
            for x in range(img.width()):
                px = img.pixelColor(x, y)
                if px.alpha() == 0:
                    continue
                opaque_pixels += 1
                assert px.red() == px.green() == px.blue(), (
                    f"Non-gray pixel at ({x},{y}): "
                    f"r={px.red()} g={px.green()} b={px.blue()}"
                )
        assert opaque_pixels > 0, "Gray pixmap has no opaque pixels"

    def test_red_pixmap_pixels_have_zero_green_and_blue(self, indicator):
        """In the red pixmap every opaque pixel must have G == B == 0
        and R should equal the source luminance (non-zero for visible pixels)."""
        img: QImage = indicator._red_pixmap.toImage()
        opaque_pixels = 0
        non_zero_red = 0
        for y in range(img.height()):
            for x in range(img.width()):
                px = img.pixelColor(x, y)
                if px.alpha() == 0:
                    continue
                opaque_pixels += 1
                assert px.green() == 0, (
                    f"Non-zero green at ({x},{y}): {px.green()}"
                )
                assert px.blue() == 0, (
                    f"Non-zero blue at ({x},{y}): {px.blue()}"
                )
                if px.red() > 0:
                    non_zero_red += 1
        assert opaque_pixels > 0, "Red pixmap has no opaque pixels"
        assert non_zero_red > 0, "Red pixmap has no visible red intensity"

    def test_alpha_preserved_across_variants(self, indicator):
        """All three variants must have the same alpha channel as the color pixmap."""
        color_img = indicator._color_pixmap.toImage()
        gray_img = indicator._gray_pixmap.toImage()
        red_img = indicator._red_pixmap.toImage()
        for y in (0, 5, 12, 20):
            for x in (0, 5, 12, 20):
                a_color = color_img.pixelColor(x, y).alpha()
                assert gray_img.pixelColor(x, y).alpha() == a_color
                assert red_img.pixelColor(x, y).alpha() == a_color
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -m pytest ncs/tests/test_spinner_indicator.py -v
```

Expected: `ImportError` or `AttributeError` because `SpinnerIndicator` doesn't exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
cd ncs && git add tests/test_spinner_indicator.py
git commit -m "Add failing tests for SpinnerIndicator pixmap baking"
```

---

## Task 3: Implement SpinnerIndicator pixmap baking

**Files:**
- Modify: `ncs/src/lucid/ui/widgets/runengine_control.py:28-77` (replace `StatusIndicator` class)

- [ ] **Step 1: Add a helper module-level function for the per-pixel transform**

In `ncs/src/lucid/ui/widgets/runengine_control.py`, find the existing imports block (lines 7-22) and replace it with this expanded version:

```python
"""RunEngine control widget for state management.

Provides a compact GUI for inspecting and managing the RunEngine state,
including status display, control buttons, and queue information.
"""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPainter, QPixmap, qAlpha, qRed, qGreen, qBlue, qRgba
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from lucid.acquire.engine import Engine


SPINNING_STATES = frozenset({"running", "stopping"})
PAUSED_STATES = frozenset({"paused", "pausing"})
ERROR_STATES = frozenset({"aborting", "panicked"})

_LOGO_SIZE = 24
_FLASH_DURATION_MS = 1500
_SPIN_INTERVAL_MS = 33  # ~30 fps
_SPIN_DEGREES_PER_TICK = 12  # 30 fps * 12 deg = 360 deg/sec
```

(Note: this preserves the existing module docstring, removes the now-unused `QColor`, and adds `QTimer`, `QImage`, `QPixmap`, `qAlpha`, `qRed`, `qGreen`, `qBlue`, `qRgba` imports plus the constants.)

- [ ] **Step 2: Replace the StatusIndicator class with SpinnerIndicator (pixmap-baking part only)**

Delete the entire existing `StatusIndicator` class (originally at lines 28-77 of the pre-edit file). Insert in its place:

```python
def _bake_gray_pixmap(source: QImage) -> QPixmap:
    """Build a grayscale variant of source, preserving alpha.

    Pure Format_Grayscale8 conversion zeroes alpha. Doing it manually keeps
    soft edges anti-aliased against any background.
    """
    out = QImage(source.size(), QImage.Format.Format_ARGB32)
    out.fill(0)
    for y in range(source.height()):
        for x in range(source.width()):
            px = source.pixel(x, y)
            a = qAlpha(px)
            if a == 0:
                continue
            r, g, b = qRed(px), qGreen(px), qBlue(px)
            lum = (r * 299 + g * 587 + b * 114) // 1000
            out.setPixel(x, y, qRgba(lum, lum, lum, a))
    return QPixmap.fromImage(out)


def _bake_red_pixmap(source: QImage) -> QPixmap:
    """Build a red-tinted variant: red channel = source luminance, G=B=0.

    Preserves the logo's internal contrast (bright parts stay bright red,
    dark parts stay dark red). Alpha is preserved for soft edges.
    """
    out = QImage(source.size(), QImage.Format.Format_ARGB32)
    out.fill(0)
    for y in range(source.height()):
        for x in range(source.width()):
            px = source.pixel(x, y)
            a = qAlpha(px)
            if a == 0:
                continue
            r, g, b = qRed(px), qGreen(px), qBlue(px)
            lum = (r * 299 + g * 587 + b * 114) // 1000
            out.setPixel(x, y, qRgba(lum, 0, 0, a))
    return QPixmap.fromImage(out)


def _load_logo_pixmaps() -> tuple[QPixmap, QPixmap, QPixmap]:
    """Load logo.png and bake (color, gray, red) 24x24 pixmaps.

    Returns:
        Tuple of (color, gray, red) QPixmaps, all 24x24.
    """
    resource = files("lucid.ui.resources") / "logo.png"
    data = resource.read_bytes()
    raw = QImage.fromData(data, "PNG")
    if raw.isNull():
        raise RuntimeError("Failed to decode logo.png from lucid.ui.resources")
    scaled = raw.scaled(
        _LOGO_SIZE,
        _LOGO_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_ARGB32)
    color = QPixmap.fromImage(scaled)
    gray = _bake_gray_pixmap(scaled)
    red = _bake_red_pixmap(scaled)
    return color, gray, red


class SpinnerIndicator(QWidget):
    """Spinning ALS logo indicating RunEngine status.

    States:
    - idle / no engine: gray, static
    - running / stopping: color, spinning at 360 deg/sec
    - paused / pausing: color at 50% opacity, static
    - aborting / panicked: red, static (also triggers a 1500 ms flash)
    - error flash (transient): red, static, overrides state for 1500 ms

    The three color variants are pre-baked once at construction. A 30 fps
    timer drives rotation; it only runs while in a spinning state to avoid
    redraw churn when idle.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the spinner indicator."""
        super().__init__(parent)
        self.setFixedSize(_LOGO_SIZE, _LOGO_SIZE)

        self._color_pixmap, self._gray_pixmap, self._red_pixmap = _load_logo_pixmaps()
        self._status = "idle"
        self._rotation = 0.0
        self._flash_active = False

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(_SPIN_INTERVAL_MS)
        self._spin_timer.timeout.connect(self._on_spin_tick)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(_FLASH_DURATION_MS)
        self._flash_timer.timeout.connect(self._on_flash_timeout)

    def set_status(self, status: str) -> None:
        """Update the displayed status.

        Starts/stops the spin timer based on whether the new status spins.
        """
        self._status = status.lower()
        if self._status in SPINNING_STATES:
            if not self._spin_timer.isActive():
                self._spin_timer.start()
        else:
            if self._spin_timer.isActive():
                self._spin_timer.stop()
        self.update()

    def flash_error(self) -> None:
        """Flash red for 1500 ms. Re-entrant: restarts the timer if called again."""
        self._flash_active = True
        self._flash_timer.start()  # restarts if already running
        self.update()

    @Slot()
    def _on_spin_tick(self) -> None:
        self._rotation = (self._rotation + _SPIN_DEGREES_PER_TICK) % 360
        self.update()

    @Slot()
    def _on_flash_timeout(self) -> None:
        self._flash_active = False
        self.update()

    def _pixmap_for_state(self) -> QPixmap:
        if self._status in ERROR_STATES:
            return self._red_pixmap
        if self._status in (PAUSED_STATES | SPINNING_STATES):
            return self._color_pixmap
        return self._gray_pixmap

    def paintEvent(self, event) -> None:
        """Render the (possibly rotated, possibly dimmed) pixmap."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._flash_active:
            pixmap = self._red_pixmap
            opacity = 1.0
        else:
            pixmap = self._pixmap_for_state()
            opacity = 0.5 if self._status in PAUSED_STATES else 1.0

        painter.setOpacity(opacity)
        painter.translate(_LOGO_SIZE / 2, _LOGO_SIZE / 2)
        painter.rotate(self._rotation)
        painter.translate(-_LOGO_SIZE / 2, -_LOGO_SIZE / 2)
        painter.drawPixmap(0, 0, pixmap)
```

- [ ] **Step 3: Run the pixmap tests to verify they pass**

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -m pytest ncs/tests/test_spinner_indicator.py::TestPixmapBaking -v
```

Expected: all 5 tests in `TestPixmapBaking` pass.

- [ ] **Step 4: Commit**

```bash
cd ncs && git add src/lucid/ui/widgets/runengine_control.py
git commit -m "Implement SpinnerIndicator with pre-baked color/gray/red pixmaps"
```

---

## Task 4: Write and verify tests for spin timer behavior

**Files:**
- Modify: `ncs/tests/test_spinner_indicator.py` (append new test class)

- [ ] **Step 1: Append failing tests for spin behavior**

Append to `ncs/tests/test_spinner_indicator.py`:

```python
class TestSpinTimer:
    """Spin timer should run only while in a spinning state."""

    def test_spin_timer_inactive_at_construction(self, indicator):
        assert not indicator._spin_timer.isActive()

    def test_spin_timer_starts_when_running(self, indicator):
        indicator.set_status("running")
        assert indicator._spin_timer.isActive()

    def test_spin_timer_starts_when_stopping(self, indicator):
        indicator.set_status("stopping")
        assert indicator._spin_timer.isActive()

    def test_spin_timer_stops_when_idle(self, indicator):
        indicator.set_status("running")
        assert indicator._spin_timer.isActive()
        indicator.set_status("idle")
        assert not indicator._spin_timer.isActive()

    def test_spin_timer_stops_when_paused(self, indicator):
        indicator.set_status("running")
        indicator.set_status("paused")
        assert not indicator._spin_timer.isActive()

    def test_rotation_advances_on_tick(self, indicator, qtbot):
        indicator.set_status("running")
        start_rotation = indicator._rotation
        # Wait for at least 2 ticks (~66 ms); use 200 ms for safety margin
        qtbot.wait(200)
        assert indicator._rotation != start_rotation

    def test_rotation_preserved_across_pause(self, indicator, qtbot):
        """Pausing stops the timer but should not reset the angle."""
        indicator.set_status("running")
        qtbot.wait(150)
        rotation_at_pause = indicator._rotation
        indicator.set_status("paused")
        assert indicator._rotation == rotation_at_pause
```

- [ ] **Step 2: Run the new tests**

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -m pytest ncs/tests/test_spinner_indicator.py::TestSpinTimer -v
```

Expected: all 7 tests pass (the implementation already supports this behavior).

- [ ] **Step 3: Commit**

```bash
cd ncs && git add tests/test_spinner_indicator.py
git commit -m "Add tests for SpinnerIndicator spin timer behavior"
```

---

## Task 5: Write and verify tests for error flash behavior

**Files:**
- Modify: `ncs/tests/test_spinner_indicator.py` (append new test class)

- [ ] **Step 1: Append failing tests for flash behavior**

Append to `ncs/tests/test_spinner_indicator.py`:

```python
class TestErrorFlash:
    """flash_error must set the flag, auto-clear after 1500 ms, and be re-entrant."""

    def test_flash_inactive_at_construction(self, indicator):
        assert indicator._flash_active is False

    def test_flash_error_sets_flag_and_starts_timer(self, indicator):
        indicator.flash_error()
        assert indicator._flash_active is True
        assert indicator._flash_timer.isActive()

    def test_flash_clears_after_timeout(self, indicator, qtbot):
        indicator.flash_error()
        # Wait slightly longer than the 1500 ms flash duration
        qtbot.wait(1700)
        assert indicator._flash_active is False
        assert not indicator._flash_timer.isActive()

    def test_flash_is_reentrant(self, indicator, qtbot):
        """Calling flash_error during an active flash should restart the timer.

        First call at t=0; second call at t=500. Without re-entrancy the flash
        would clear at t=1500 (1000 ms after second call). With re-entrancy it
        clears at t=500+1500=2000 ms. We check at t=1700.
        """
        indicator.flash_error()
        qtbot.wait(500)
        indicator.flash_error()
        qtbot.wait(1200)  # total elapsed: 1700 ms
        # Should still be active because second call restarted the 1500 ms timer
        assert indicator._flash_active is True
        # And clear after another ~400 ms (at total ~2100 ms)
        qtbot.wait(500)
        assert indicator._flash_active is False
```

- [ ] **Step 2: Run the new tests**

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -m pytest ncs/tests/test_spinner_indicator.py::TestErrorFlash -v
```

Expected: all 4 tests pass. Note these tests take ~3.5 seconds total due to real-time waits — that's acceptable for a single feature's test suite.

- [ ] **Step 3: Commit**

```bash
cd ncs && git add tests/test_spinner_indicator.py
git commit -m "Add tests for SpinnerIndicator error flash behavior"
```

---

## Task 6: Wire SpinnerIndicator into RunEngineControlWidget

**Files:**
- Modify: `ncs/src/lucid/ui/widgets/runengine_control.py` (`RunEngineControlWidget` class)

- [ ] **Step 1: Replace StatusIndicator usage with SpinnerIndicator**

In `ncs/src/lucid/ui/widgets/runengine_control.py`, find the `_setup_ui` method of `RunEngineControlWidget` and locate the line:

```python
        self._status_indicator = StatusIndicator()
```

Replace it with:

```python
        self._status_indicator = SpinnerIndicator()
```

(The variable name stays the same so all downstream uses continue to work.)

- [ ] **Step 2: Wire sigException to flash_error in _connect_signals**

In `RunEngineControlWidget._connect_signals`, find the existing block:

```python
    def _connect_signals(self) -> None:
        """Connect to engine signals."""
        if self._engine is None:
            return

        self._engine.sigStateChanged.connect(self._on_state_changed)
        self._engine.sigStart.connect(self._on_run_start)
        self._engine.sigFinish.connect(self._on_run_finish)
        self._engine.sigPause.connect(self._on_pause)
        self._engine.sigResume.connect(self._on_resume)
        self._engine.sigQueueChanged.connect(self._on_queue_changed)
```

Add one line at the end of the method body:

```python
        self._engine.sigException.connect(self._on_exception)
```

- [ ] **Step 3: Mirror the disconnect**

In `RunEngineControlWidget._disconnect_signals`, find the `try` block and add one matching disconnect line:

```python
        try:
            self._engine.sigStateChanged.disconnect(self._on_state_changed)
            self._engine.sigStart.disconnect(self._on_run_start)
            self._engine.sigFinish.disconnect(self._on_run_finish)
            self._engine.sigPause.disconnect(self._on_pause)
            self._engine.sigResume.disconnect(self._on_resume)
            self._engine.sigQueueChanged.disconnect(self._on_queue_changed)
            self._engine.sigException.disconnect(self._on_exception)
        except RuntimeError:
            pass  # Already disconnected
```

- [ ] **Step 4: Add the _on_exception slot**

In `RunEngineControlWidget`, find the existing `_on_queue_changed` slot. Immediately after it, add this new slot:

```python
    @Slot(Exception)
    def _on_exception(self, ex: Exception) -> None:
        """Flash the indicator red when the engine raises an exception."""
        self._status_indicator.flash_error()
```

- [ ] **Step 5: Trigger flash on entry to error states**

In `RunEngineControlWidget._on_state_changed`, find:

```python
    @Slot(str)
    def _on_state_changed(self, state: str) -> None:
        """Handle RunEngine state change.

        Args:
            state: New state string.
        """
        self._update_state()
        logger.debug(f"RunEngine state changed: {state}")
```

Replace with:

```python
    @Slot(str)
    def _on_state_changed(self, state: str) -> None:
        """Handle RunEngine state change.

        Args:
            state: New state string.
        """
        self._update_state()
        if state.lower() in ERROR_STATES:
            self._status_indicator.flash_error()
        logger.debug(f"RunEngine state changed: {state}")
```

- [ ] **Step 6: Smoke-test the import**

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -c "from lucid.ui.widgets.runengine_control import RunEngineControlWidget, SpinnerIndicator; print('OK')"
```

Expected: prints `OK`. Any `ImportError` or `AttributeError` here means a typo in the wiring.

- [ ] **Step 7: Re-run the full SpinnerIndicator test file to confirm nothing broke**

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -m pytest ncs/tests/test_spinner_indicator.py -v
```

Expected: all 16 tests pass (5 + 7 + 4).

- [ ] **Step 8: Commit**

```bash
cd ncs && git add src/lucid/ui/widgets/runengine_control.py
git commit -m "Wire SpinnerIndicator into RunEngineControlWidget with exception flash"
```

---

## Task 7: Manual visual verification

This is a UI feature; type checks and unit tests confirm the wiring is correct, but the visual behavior (spin smoothness, color appearance, flash timing) needs eyeballs.

- [ ] **Step 1: Launch the LUCID app**

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/lucid.exe
```

Or, if that script is not on the PATH yet:

```bash
C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe -m lucid.main
```

- [ ] **Step 2: Verify idle state**

Locate the RunEngine status widget (toolbar / status bar). Confirm:
- The ALS logo is visible at 24x24
- It is grayscale
- It is not rotating

- [ ] **Step 3: Verify running state**

Submit a plan that takes a few seconds to execute (e.g., a `count` plan over a slow detector, or any test plan available in the environment). Confirm:
- The logo turns to its full color
- The logo rotates smoothly at roughly one full revolution per second

- [ ] **Step 4: Verify paused state**

While the plan is running, click "Pause". Confirm:
- The logo stops rotating
- The logo dims to roughly 50% opacity (still in color, but visibly faded)

- [ ] **Step 5: Verify resume**

Click "Resume". Confirm:
- The logo resumes spinning at full opacity
- The angle continues from where it paused (no snap-back to 0°)

- [ ] **Step 6: Verify error flash on aborting**

While a plan is running, click "Abort". Confirm:
- The logo briefly goes red (with the logo's internal contrast preserved — bright parts bright red, dark parts dark red)
- After ~1.5 seconds it returns to gray (idle)

- [ ] **Step 7: Verify error flash on plan exception**

Submit a plan that raises an exception (e.g., a plan that calls `raise RuntimeError("test")`). Confirm:
- The logo flashes red for ~1.5 seconds when the exception is raised
- After the flash it returns to gray (idle)

- [ ] **Step 8: Report results**

If anything looks off (jittery rotation, wrong shade of gray/red, flash too fast/slow, dimming barely visible), note it. Otherwise this task is complete.

---

## Self-Review Notes

Coverage check against [the spec](../specs/2026-04-15-runengine-spinner-indicator-design.md):

- **Asset / loading:** Task 1 (copy + force-include + importlib.resources verification)
- **Color pixmap:** Task 3 (`_load_logo_pixmaps`)
- **Gray pixmap (luminance, alpha preserved):** Task 3 (`_bake_gray_pixmap`); test in Task 2
- **Red pixmap (R = luminance, G=B=0, alpha preserved):** Task 3 (`_bake_red_pixmap`); test in Task 2
- **State → visual mapping:** Task 3 (`_pixmap_for_state` + `paintEvent` opacity logic)
- **Spin mechanism:** Task 3 (`_spin_timer`, `_on_spin_tick`); tests in Task 4
- **Error flash:** Task 3 (`flash_error`, `_flash_timer`); tests in Task 5
- **Flash triggers (sigException + state entry):** Task 6 (steps 2-5)
- **Drop-in API (`set_status`):** Task 3 (signature matches the old `StatusIndicator.set_status`)
- **Files-changed list:** all four files in the spec are touched (resources/__init__.py, logo.png, runengine_control.py, pyproject.toml)
- **Manual visual verification:** Task 7 covers all six visual behaviors from the spec

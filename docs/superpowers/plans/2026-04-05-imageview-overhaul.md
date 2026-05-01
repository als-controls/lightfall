# OphydImageView Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the minimal `OphydImageView` with a full-featured scientific image viewer — axes, crosshair, ROI stats, log intensity, background correction, acquisition progress, and correct LUT/orientation behavior.

**Architecture:** Composition-based — `OphydImageView` remains the top-level `QWidget` container, composed of a `pg.PlotItem` (axes), `pg.ImageItem` (image), toolbar buttons, crosshair overlays, and a coordinate label. No deep mixin chains. Features are added as internal components wired together in `_setup_ui()`. A separate `DarkFrameManager` handles dark frame caching via RunEngine subscription and Tiled readback. Log intensity works by displaying `log1p(data)` in the ImageItem while keeping the HistogramLUTItem bound to raw linear data — the histogram shows true intensity values and level handles operate in real units, but the levels are log-transformed before being applied to the display image.

**Tech Stack:** PySide6, pyqtgraph 0.13, numpy, ophyd, bluesky (RunEngine subscription), Tiled (dark frame readback)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/lucid/ui/widgets/camera/image_view.py` | **New.** `OphydImageView` rewrite — axes, crosshair, coords, toolbar, log LUT, ROI stats overlay, progress bar. Self-contained widget. |
| `src/lucid/ui/widgets/camera/dark_frames.py` | **New.** `DarkFrameManager` — subscribes to RunEngine, watches for `"dark"` stream events, reads frames from Tiled, caches them, exposes current dark for subtraction. |
| `src/lucid/ui/widgets/camera/base.py` | **Modify.** Remove old `OphydImageView` class (lines 200-344). Import new one from `image_view.py`. Wire progress bar to acquisition signals. |
| `src/lucid/ui/widgets/camera/plan_based.py` | **Modify.** Add "Capture Dark" button. Wire `DarkFrameManager` into the widget. |
| `src/lucid/ui/widgets/camera/__init__.py` | **Modify.** Export new public names. |
| `tests/test_ophyd_image_view.py` | **New.** Tests for `OphydImageView` — LUT behavior, orientation, crosshair, log mode, ROI stats display, progress bar. |
| `tests/test_dark_frame_manager.py` | **New.** Tests for `DarkFrameManager` — document handling, caching, Tiled readback. |

---

## Task 1: Scaffold `OphydImageView` with PlotItem axes and correct orientation

**Files:**
- Create: `src/lucid/ui/widgets/camera/image_view.py`
- Create: `tests/test_ophyd_image_view.py`

This task creates the new `OphydImageView` with a `PlotItem` providing axis ticks, an `ImageItem` for the image, and a `HistogramLUTItem` for the color scale. The image is displayed right-side-up by inverting the Y axis. No toolbar or features yet — just the core display.

- [ ] **Step 1: Write test for image orientation and axes presence**

```python
"""Tests for OphydImageView."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from lucid.ui.widgets.camera.image_view import OphydImageView


@pytest.fixture()
def qapp():
    """Ensure QApplication exists."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_mock_device(image_data: np.ndarray | None = None):
    """Create a mock ophyd device with image plugin."""
    if image_data is None:
        image_data = np.random.randint(0, 255, (480, 640), dtype=np.uint8)

    device = MagicMock()
    device.name = "sim_det"
    device.image1.array_data.get.return_value = image_data
    device.image1.width.get.return_value = image_data.shape[1]
    device.image1.height.get.return_value = image_data.shape[0]
    return device


class TestOphydImageViewBasic:
    """Basic display tests."""

    def test_has_axes(self, qapp):
        """PlotItem should provide visible axes."""
        device = _make_mock_device()
        view = OphydImageView(device)

        # PlotItem provides axes
        assert view._plot_item is not None
        assert view._plot_item.axes["bottom"]["item"].isVisible()
        assert view._plot_item.axes["left"]["item"].isVisible()
        view.close()

    def test_image_orientation_y_inverted(self, qapp):
        """Y axis should be inverted so row 0 is at the top."""
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._plot_item.getViewBox().yInverted()
        view.close()

    def test_histogram_present(self, qapp):
        """Histogram LUT widget should be present."""
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._histogram is not None
        view.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lucid.ui.widgets.camera.image_view'`

- [ ] **Step 3: Implement `OphydImageView` scaffold**

```python
"""Scientific image viewer for ophyd area detector devices.

Displays live image data with:
- Axis ticks via PlotItem
- Histogram/LUT control
- Correct orientation (row 0 at top)
- Efficient frame updates via ImageItem.setImage()

The LUT is auto-scaled on the first frame received, then held stable.
Users reset it manually via the Reset LUT button (added in a later task).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class OphydImageView(QWidget):
    """PyQtGraph-based scientific image viewer for ophyd area detectors.

    Uses PlotItem for axes, ImageItem for rendering, and HistogramLUTItem
    for color scale control. Polls the device's image plugin at ~10 fps.
    """

    def __init__(self, ophyd_device: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = ophyd_device
        self._timer: QTimer | None = None
        self._first_frame = True

        self._setup_ui()
        self._start_updates()

    def _setup_ui(self) -> None:
        """Build the viewer layout: [image + axes | histogram]."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Main horizontal split: image view | histogram
        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        # PlotItem provides axes around the image
        self._plot_item = pg.PlotItem()
        self._plot_item.setDefaultPadding(0)
        self._plot_item.hideButtons()
        self._plot_item.setMenuEnabled(False)
        self._plot_item.getViewBox().invertY(True)
        self._plot_item.getViewBox().setAspectLocked(True)
        self._plot_item.setLabel("bottom", "x (px)")
        self._plot_item.setLabel("left", "y (px)")

        # ImageItem lives inside the PlotItem
        self._image_item = pg.ImageItem()
        self._image_item.setOpts(axisOrder="row-major")
        self._plot_item.addItem(self._image_item)

        # GraphicsView to host the PlotItem
        self._graphics_view = pg.GraphicsView()
        self._graphics_view.setCentralItem(self._plot_item)
        h_layout.addWidget(self._graphics_view, stretch=1)

        # HistogramLUTItem for color scale control
        self._histogram = pg.HistogramLUTItem()
        self._histogram.setImageItem(self._image_item)

        self._hist_view = pg.GraphicsView()
        self._hist_view.setCentralItem(self._histogram)
        self._hist_view.setFixedWidth(120)
        h_layout.addWidget(self._hist_view)

        layout.addLayout(h_layout)

    def _start_updates(self) -> None:
        """Start polling the device for image data."""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_image)
        self._timer.start(100)  # ~10 fps

    def _update_image(self) -> None:
        """Poll device image plugin and update display."""
        if self._device is None:
            return

        try:
            image_plugin = None
            for attr in ("image1", "image"):
                plugin = getattr(self._device, attr, None)
                if plugin is not None and hasattr(plugin, "array_data"):
                    image_plugin = plugin
                    break

            if image_plugin is not None:
                image_data = image_plugin.array_data.get()
                if image_data is not None:
                    self._display_array(image_data, image_plugin)
        except Exception as e:
            logger.warning(f"Failed to update image: {e}")

    def _display_array(self, array: np.ndarray, image_plugin: Any = None) -> None:
        """Process and display a numpy array.

        First frame: autoLevels=True to set initial LUT range.
        Subsequent frames: autoLevels=False to preserve user adjustments.
        """
        if array is None or array.size == 0:
            return

        arr = np.squeeze(array)

        if arr.ndim == 1:
            width, height = self._get_image_dimensions(image_plugin)
            if width and height and width * height == arr.size:
                arr = arr.reshape((height, width))
            else:
                return

        if arr.ndim != 2:
            return

        auto_levels = self._first_frame
        self._image_item.setImage(arr, autoLevels=auto_levels)
        if self._first_frame:
            self._first_frame = False
            self._plot_item.getViewBox().autoRange()

    def _get_image_dimensions(self, image_plugin: Any = None) -> tuple[int | None, int | None]:
        """Get image width and height from plugin or cam."""
        try:
            if image_plugin is not None:
                w = getattr(image_plugin, "width", None)
                h = getattr(image_plugin, "height", None)
                if w is not None and h is not None:
                    width, height = int(w.get()), int(h.get())
                    if width > 0 and height > 0:
                        return width, height

            cam = getattr(self._device, "cam", None)
            if cam is not None:
                size = getattr(cam, "array_size", None)
                if size is not None:
                    dims = size.get()
                    if hasattr(dims, "array_size_x") and hasattr(dims, "array_size_y"):
                        width = int(dims.array_size_x)
                        height = int(dims.array_size_y)
                        if width > 0 and height > 0:
                            return width, height
        except Exception as e:
            logger.debug(f"Failed to get image dimensions: {e}")

        return None, None

    def close(self) -> None:
        """Stop updates and clean up."""
        if self._timer is not None:
            self._timer.stop()
        super().close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/widgets/camera/image_view.py tests/test_ophyd_image_view.py
git commit -m "feat(camera): scaffold new OphydImageView with PlotItem axes and correct orientation"
```

---

## Task 2: LUT behavior — auto on first frame, stable thereafter

**Files:**
- Modify: `tests/test_ophyd_image_view.py`
- Modify: `src/lucid/ui/widgets/camera/image_view.py`

The current code passes `autoLevels=True` on every frame, resetting the LUT constantly. The new behavior: auto-scale on the first frame only, then hold stable. This task adds tests that verify this behavior. The implementation is already in the Task 1 scaffold (`self._first_frame` flag), so this task focuses on testing.

- [ ] **Step 1: Write tests for LUT stability**

Add to `tests/test_ophyd_image_view.py`:

```python
class TestLUTBehavior:
    """LUT should auto-scale on first frame, then stay stable."""

    def test_first_frame_sets_levels(self, qapp):
        """First frame should auto-scale the histogram levels."""
        data = np.zeros((100, 100), dtype=np.uint16)
        data[50, 50] = 1000
        device = _make_mock_device(data)
        view = OphydImageView(device)

        # Simulate first frame
        view._display_array(data)
        assert view._first_frame is False

        levels = view._histogram.getLevels()
        assert levels[0] < levels[1]  # Valid range set
        view.close()

    def test_subsequent_frames_preserve_levels(self, qapp):
        """After first frame, levels should not change on new frames."""
        data1 = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data1)
        view = OphydImageView(device)

        view._display_array(data1)
        levels_after_first = view._histogram.getLevels()

        # Second frame with very different range
        data2 = np.random.randint(500, 1000, (100, 100), dtype=np.uint16)
        view._display_array(data2)
        levels_after_second = view._histogram.getLevels()

        assert levels_after_first == levels_after_second
        view.close()

    def test_reset_lut_flag(self, qapp):
        """reset_lut() should re-enable auto-levels for next frame."""
        data = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        assert view._first_frame is False

        view.reset_lut()
        assert view._first_frame is True
        view.close()
```

- [ ] **Step 2: Run tests — `test_reset_lut_flag` should fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py::TestLUTBehavior -v`
Expected: FAIL on `test_reset_lut_flag` — `AttributeError: 'OphydImageView' has no attribute 'reset_lut'`

- [ ] **Step 3: Add `reset_lut()` and `reset_axes()` methods**

Add to `OphydImageView` in `image_view.py`:

```python
    def reset_lut(self) -> None:
        """Reset LUT to auto-scale on the next frame."""
        self._first_frame = True

    def reset_axes(self) -> None:
        """Reset view to fit the entire image."""
        self._plot_item.getViewBox().autoRange()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_ophyd_image_view.py src/lucid/ui/widgets/camera/image_view.py
git commit -m "feat(camera): LUT auto-scales on first frame only, add reset_lut/reset_axes"
```

---

## Task 3: Toolbar with Reset LUT, Reset Axes, Log Intensity buttons

**Files:**
- Modify: `tests/test_ophyd_image_view.py`
- Modify: `src/lucid/ui/widgets/camera/image_view.py`

Adds a horizontal toolbar above the image with three buttons: Reset LUT, Reset Axes, and a checkable Log Intensity toggle.

- [ ] **Step 1: Write toolbar tests**

Add to `tests/test_ophyd_image_view.py`:

```python
class TestToolbar:
    """Toolbar buttons above the image."""

    def test_toolbar_buttons_exist(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._reset_lut_btn is not None
        assert view._reset_axes_btn is not None
        assert view._log_intensity_btn is not None
        assert view._log_intensity_btn.isCheckable()
        view.close()

    def test_reset_lut_button_resets_flag(self, qapp):
        data = np.random.randint(0, 100, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        assert view._first_frame is False

        view._reset_lut_btn.click()
        assert view._first_frame is True
        view.close()

    def test_reset_axes_button_calls_autorange(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)

        # Should not raise
        view._reset_axes_btn.click()
        view.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py::TestToolbar -v`
Expected: FAIL — `AttributeError: 'OphydImageView' has no attribute '_reset_lut_btn'`

- [ ] **Step 3: Add toolbar to `_setup_ui()`**

Add toolbar creation to `_setup_ui()` in `image_view.py`, inserted into the main layout before the image/histogram row:

```python
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

# In _setup_ui(), before the h_layout section:

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)

        self._reset_lut_btn = QPushButton("Reset LUT")
        self._reset_lut_btn.setFixedHeight(24)
        self._reset_lut_btn.clicked.connect(self.reset_lut)
        toolbar.addWidget(self._reset_lut_btn)

        self._reset_axes_btn = QPushButton("Reset Axes")
        self._reset_axes_btn.setFixedHeight(24)
        self._reset_axes_btn.clicked.connect(self.reset_axes)
        toolbar.addWidget(self._reset_axes_btn)

        self._log_intensity_btn = QPushButton("Log Intensity")
        self._log_intensity_btn.setFixedHeight(24)
        self._log_intensity_btn.setCheckable(True)
        self._log_intensity_btn.toggled.connect(self._on_log_intensity_toggled)
        toolbar.addWidget(self._log_intensity_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)
```

Add the stub toggle handler:

```python
    def _on_log_intensity_toggled(self, checked: bool) -> None:
        """Toggle log intensity display. Implementation in Task 4."""
        self._log_mode = checked
```

And initialize `self._log_mode = False` in `__init__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_ophyd_image_view.py src/lucid/ui/widgets/camera/image_view.py
git commit -m "feat(camera): add toolbar with Reset LUT, Reset Axes, Log Intensity buttons"
```

---

## Task 4: Log intensity — log-transformed display with linear histogram

**Files:**
- Modify: `tests/test_ophyd_image_view.py`
- Modify: `src/lucid/ui/widgets/camera/image_view.py`

When log mode is on, the ImageItem displays `np.log1p(data)` (truly log-scaled rendering), but the HistogramLUTItem remains bound to the raw linear data. This means:
- The histogram shows the real intensity distribution and level handles operate in real units
- The displayed image is log-scaled so faint features are visible
- The histogram levels are log-transformed before being applied as display levels

Implementation: disconnect the automatic `HistogramLUTItem → ImageItem` level link. Instead, manually wire `sigLevelsChanged` to a handler that transforms levels through `log1p()` when in log mode before applying them to the ImageItem. The histogram continues to compute its bins from raw data (set via `_histogram.setImageItem()` or by updating the histogram data directly).

Key subtlety: when we call `_image_item.setImage(log_data)`, the ImageItem holds log values. The histogram must still hold the *raw* data for its bin computation. We achieve this by:
1. Storing `self._raw_image` alongside the displayed image
2. Updating the histogram bins from `_raw_image` manually via `_histogram.setHistogramRange()` and `_histogram.setLevels()`
3. The ImageItem gets `log1p(data)` and its levels are `log1p(histogram_levels)`

- [ ] **Step 1: Write log intensity tests**

Add to `tests/test_ophyd_image_view.py`:

```python
class TestLogIntensity:
    """Log intensity: displayed image is log-scaled, histogram shows true values."""

    def test_log_mode_off_by_default(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        assert view._log_mode is False
        view.close()

    def test_toggle_log_mode(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        view._log_intensity_btn.setChecked(True)
        assert view._log_mode is True
        view._log_intensity_btn.setChecked(False)
        assert view._log_mode is False
        view.close()

    def test_log_mode_displays_log_data(self, qapp):
        """ImageItem should contain log1p(data) when log mode is on."""
        data = np.full((100, 100), 100, dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._log_intensity_btn.setChecked(True)
        view._display_array(data)

        displayed = view._image_item.image
        expected = np.log1p(data.astype(np.float64))
        np.testing.assert_allclose(displayed, expected, rtol=1e-5)
        view.close()

    def test_linear_mode_displays_raw_data(self, qapp):
        """ImageItem should contain raw data when log mode is off."""
        data = np.full((100, 100), 100, dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._log_intensity_btn.setChecked(False)
        view._display_array(data)

        displayed = view._image_item.image
        np.testing.assert_array_equal(displayed, data)
        view.close()

    def test_histogram_levels_in_real_units(self, qapp):
        """Histogram level handles should operate in real intensity units."""
        data = np.random.randint(10, 1000, (100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._display_array(data)
        levels_linear = view._histogram.getLevels()

        view._log_intensity_btn.setChecked(True)
        view._display_array(data)
        levels_log = view._histogram.getLevels()

        # Histogram levels stay in real units regardless of log mode
        assert abs(levels_linear[0] - levels_log[0]) < 1.0
        assert abs(levels_linear[1] - levels_log[1]) < 1.0
        view.close()

    def test_raw_image_cached(self, qapp):
        """_raw_image should always contain the original un-transformed data."""
        data = np.full((100, 100), 42, dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._log_intensity_btn.setChecked(True)
        view._display_array(data)
        np.testing.assert_array_equal(view._raw_image, data)
        view.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py::TestLogIntensity -v`
Expected: FAIL — attributes not yet implemented

- [ ] **Step 3: Implement log intensity display**

The core changes to `_display_array()` and new helper methods in `image_view.py`:

```python
# In __init__:
        self._log_mode = False
        self._raw_image: np.ndarray | None = None

# Modified _display_array:
    def _display_array(self, array: np.ndarray, image_plugin: Any = None) -> None:
        if array is None or array.size == 0:
            return

        arr = np.squeeze(array)

        if arr.ndim == 1:
            width, height = self._get_image_dimensions(image_plugin)
            if width and height and width * height == arr.size:
                arr = arr.reshape((height, width))
            else:
                return

        if arr.ndim != 2:
            return

        # Background correction (applied before log transform)
        if self._bg_correct_btn.isChecked():
            arr = self._dark_manager.subtract(arr)

        # Cache raw image for histogram and coordinate readback
        self._raw_image = arr

        # Apply log transform for display only
        if self._log_mode:
            display_data = np.log1p(arr.astype(np.float64))
        else:
            display_data = arr

        auto_levels = self._first_frame
        self._image_item.setImage(display_data, autoLevels=False)

        if auto_levels:
            # Set histogram levels from raw data
            self._histogram.setLevels(float(np.nanmin(arr)), float(np.nanmax(arr)))
            self._first_frame = False
            self._plot_item.getViewBox().autoRange()

        # Apply current histogram levels (possibly log-transformed) to image
        self._apply_display_levels()

# New methods:
    def _apply_display_levels(self) -> None:
        """Apply histogram levels to the ImageItem, log-transforming if needed."""
        levels = self._histogram.getLevels()
        if levels is None:
            return

        lo, hi = levels
        if self._log_mode:
            display_lo = np.log1p(max(lo, 0))
            display_hi = np.log1p(max(hi, 0))
        else:
            display_lo, display_hi = lo, hi

        self._image_item.setLevels([display_lo, display_hi])

    def _on_log_intensity_toggled(self, checked: bool) -> None:
        """Toggle log intensity display and re-render current frame."""
        self._log_mode = checked
        if self._raw_image is not None:
            self._display_array(self._raw_image)

    def _on_levels_changed(self) -> None:
        """When user adjusts histogram levels, update display levels."""
        self._apply_display_levels()
```

In `_setup_ui()`, disconnect automatic histogram→image level binding and wire manual control:

```python
        # Do NOT call self._histogram.setImageItem(self._image_item) — we manage
        # the level link manually so we can log-transform levels for display.
        # Instead, connect sigLevelsChanged to our handler:
        self._histogram.sigLevelsChanged.connect(self._on_levels_changed)
```

Note: since the histogram is not auto-linked to the ImageItem, we also need to update
the histogram bins manually when new data arrives. Add to `_display_array()` after
setting `self._raw_image`:

```python
        # Update histogram bins from raw data (not log-transformed)
        self._histogram.imageChanged(autoLevel=False, autoRange=False)
        # Manually set histogram data since we're not using setImageItem()
        # Use the plot method on the histogram
        import pyqtgraph.functions as fn
        histogram_data = fn.histogram(arr, bins='auto')
        self._histogram.plot.setData(*histogram_data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_ophyd_image_view.py src/lucid/ui/widgets/camera/image_view.py
git commit -m "feat(camera): log intensity display with log1p transform, histogram stays in real units"
```

---

## Task 5: Crosshair and pixel coordinate display

**Files:**
- Modify: `tests/test_ophyd_image_view.py`
- Modify: `src/lucid/ui/widgets/camera/image_view.py`

Orange crosshair (`InfiniteLine` pair) follows the mouse cursor over the image. A `QLabel` below the image shows `x=... y=... I=...` with the pixel coordinates and intensity value under the cursor.

- [ ] **Step 1: Write crosshair and coordinates tests**

Add to `tests/test_ophyd_image_view.py`:

```python
from pyqtgraph import InfiniteLine


class TestCrosshair:
    """Crosshair and coordinate display."""

    def test_crosshair_lines_exist(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)

        assert isinstance(view._vline, InfiniteLine)
        assert isinstance(view._hline, InfiniteLine)
        # Hidden by default (no mouse position yet)
        assert not view._vline.isVisible()
        assert not view._hline.isVisible()
        view.close()

    def test_coords_label_exists(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)

        assert view._coords_label is not None
        assert view._coords_label.text() == ""
        view.close()

    def test_format_coordinates(self, qapp):
        """_format_coordinates should produce x=... y=... I=... string."""
        data = np.ones((100, 100), dtype=np.uint16) * 42
        device = _make_mock_device(data)
        view = OphydImageView(device)
        view._display_array(data)

        text = view._format_coordinates(50.0, 25.0)
        assert "x=50.0" in text
        assert "y=25.0" in text
        assert "I=42" in text
        view.close()

    def test_format_coordinates_out_of_bounds(self, qapp):
        """Out-of-bounds coordinates should return empty string."""
        data = np.ones((100, 100), dtype=np.uint16)
        device = _make_mock_device(data)
        view = OphydImageView(device)
        view._display_array(data)

        assert view._format_coordinates(-1, 50) == ""
        assert view._format_coordinates(50, 200) == ""
        view.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py::TestCrosshair -v`
Expected: FAIL — `AttributeError: 'OphydImageView' has no attribute '_vline'`

- [ ] **Step 3: Implement crosshair and coordinates**

Add to `_setup_ui()` in `image_view.py`:

```python
from pyqtgraph import InfiniteLine, mkPen
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt

# In _setup_ui(), after adding the image_item to the plot_item:

        # Crosshair
        linepen = mkPen("#FFA500", width=1)
        self._vline = InfiniteLine(angle=90, movable=False, pen=linepen)
        self._hline = InfiniteLine(angle=0, movable=False, pen=linepen)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self._plot_item.addItem(self._vline)
        self._plot_item.addItem(self._hline)

        # Mouse tracking
        self._plot_item.scene().sigMouseMoved.connect(self._on_mouse_moved)

# After the h_layout, add the coords label:

        # Coordinate display
        self._coords_label = QLabel("")
        self._coords_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._coords_label.setFixedHeight(20)
        self._coords_label.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self._coords_label)
```

Add methods:

```python
    def _on_mouse_moved(self, pos) -> None:
        """Update crosshair and coordinates on mouse move."""
        vb = self._plot_item.getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            self._coords_label.setText("")
            return

        mouse_point = vb.mapSceneToView(pos)
        x, y = mouse_point.x(), mouse_point.y()

        text = self._format_coordinates(x, y)
        if text:
            self._vline.setPos(x)
            self._hline.setPos(y)
            self._vline.setVisible(True)
            self._hline.setVisible(True)
            self._coords_label.setText(text)
        else:
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            self._coords_label.setText("")

    def _format_coordinates(self, x: float, y: float) -> str:
        """Format pixel coordinates and intensity at (x, y).

        Returns empty string if position is outside image bounds.
        """
        image = self._image_item.image
        if image is None:
            return ""

        row, col = int(y), int(x)
        if row < 0 or col < 0 or row >= image.shape[0] or col >= image.shape[1]:
            return ""

        intensity = image[row, col]
        return f"x={x:.1f}  y={y:.1f}  I={intensity:.0f}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_ophyd_image_view.py src/lucid/ui/widgets/camera/image_view.py
git commit -m "feat(camera): crosshair cursor and pixel coordinate/intensity display"
```

---

## Task 6: ROI statistics display from IOC

**Files:**
- Modify: `tests/test_ophyd_image_view.py`
- Modify: `src/lucid/ui/widgets/camera/image_view.py`

Reads hardware-computed ROI statistics from the device's `roi_stat1` plugin (ophyd `ROIStatNPlugin_V23` or `SimStatsPlugin`) and displays them as a text overlay on the image. Stats are polled alongside the image update.

- [ ] **Step 1: Write ROI stats tests**

Add to `tests/test_ophyd_image_view.py`:

```python
class TestROIStats:
    """Hardware ROI statistics display."""

    def _make_device_with_stats(self):
        """Device with roi_stat1 plugin."""
        device = _make_mock_device()
        stats = MagicMock()
        stats.min_value.get.return_value = 10
        stats.max_value.get.return_value = 950
        stats.mean_value.get.return_value = 123.4
        stats.total.get.return_value = 1234000
        stats.centroid_x.get.return_value = 320.5
        stats.centroid_y.get.return_value = 240.1
        device.roi_stat1 = stats
        return device

    def test_stats_overlay_shown_when_available(self, qapp):
        device = self._make_device_with_stats()
        view = OphydImageView(device)

        view._update_roi_stats()
        text = view._stats_text.toPlainText()
        assert "max" in text.lower() or "950" in text
        view.close()

    def test_stats_overlay_hidden_when_no_plugin(self, qapp):
        device = _make_mock_device()
        # No roi_stat1 attribute
        if hasattr(device, "roi_stat1"):
            del device.roi_stat1
        view = OphydImageView(device)

        view._update_roi_stats()
        assert not view._stats_text.isVisible()
        view.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py::TestROIStats -v`
Expected: FAIL — `AttributeError: 'OphydImageView' has no attribute '_stats_text'`

- [ ] **Step 3: Implement ROI stats overlay**

Add to `_setup_ui()`:

```python
        # ROI stats overlay (top-right corner of image)
        self._stats_text = pg.TextItem(anchor=(1, 0), color="#00FF00")
        self._stats_text.setFont(pg.QtGui.QFont("monospace", 9))
        self._stats_text.setVisible(False)
        self._plot_item.addItem(self._stats_text)
```

Add to `_update_image()`, after the `_display_array` call:

```python
            self._update_roi_stats()
```

Add method:

```python
    # Stat signal names to display, in order
    _STAT_FIELDS = ("min_value", "max_value", "mean_value", "total", "centroid_x", "centroid_y")

    def _update_roi_stats(self) -> None:
        """Read and display hardware ROI statistics from roi_stat1 plugin."""
        stats_plugin = getattr(self._device, "roi_stat1", None)
        if stats_plugin is None:
            self._stats_text.setVisible(False)
            return

        try:
            lines = []
            for field in self._STAT_FIELDS:
                signal = getattr(stats_plugin, field, None)
                if signal is not None:
                    value = signal.get()
                    label = field.replace("_", " ").title()
                    if isinstance(value, float):
                        lines.append(f"{label}: {value:.1f}")
                    else:
                        lines.append(f"{label}: {value}")

            if lines:
                self._stats_text.setText("\n".join(lines))
                # Position at top-right of current view
                vb = self._plot_item.getViewBox()
                view_range = vb.viewRange()
                self._stats_text.setPos(view_range[0][1], view_range[1][0])
                self._stats_text.setVisible(True)
            else:
                self._stats_text.setVisible(False)
        except Exception as e:
            logger.debug(f"Failed to read ROI stats: {e}")
            self._stats_text.setVisible(False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_ophyd_image_view.py src/lucid/ui/widgets/camera/image_view.py
git commit -m "feat(camera): ROI statistics overlay from hardware roi_stat1 plugin"
```

---

## Task 7: Acquisition progress bar

**Files:**
- Modify: `tests/test_ophyd_image_view.py`
- Modify: `src/lucid/ui/widgets/camera/image_view.py`

A `QProgressBar` at the bottom of the image view shows acquisition progress by polling the device's HDF5 plugin (`num_captured` / `num_images`), or falling back to `cam.array_counter` / `cam.num_images`. Only visible during active acquisition.

- [ ] **Step 1: Write progress bar tests**

Add to `tests/test_ophyd_image_view.py`:

```python
from PySide6.QtWidgets import QProgressBar


class TestProgressBar:
    """Acquisition progress tracking."""

    def test_progress_bar_exists_and_hidden(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        assert isinstance(view._progress_bar, QProgressBar)
        assert not view._progress_bar.isVisible()
        view.close()

    def test_update_progress_from_cam(self, qapp):
        device = _make_mock_device()
        device.cam = MagicMock()
        device.cam.array_counter.get.return_value = 5
        device.cam.num_images.get.return_value = 10
        device.cam.acquire.get.return_value = 1
        view = OphydImageView(device)

        view._update_progress()
        assert view._progress_bar.isVisible()
        assert view._progress_bar.value() == 5
        assert view._progress_bar.maximum() == 10
        view.close()

    def test_progress_hides_when_idle(self, qapp):
        device = _make_mock_device()
        device.cam = MagicMock()
        device.cam.acquire.get.return_value = 0
        view = OphydImageView(device)

        view._update_progress()
        assert not view._progress_bar.isVisible()
        view.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py::TestProgressBar -v`
Expected: FAIL — `AttributeError: 'OphydImageView' has no attribute '_progress_bar'`

- [ ] **Step 3: Implement progress bar**

Add to `_setup_ui()`, after the coords label:

```python
from PySide6.QtWidgets import QProgressBar

        # Acquisition progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(16)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v / %m  (%p%)")
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)
```

Call `_update_progress()` inside `_update_image()`:

```python
            self._update_progress()
```

Add method:

```python
    def _update_progress(self) -> None:
        """Update acquisition progress bar from device signals."""
        cam = getattr(self._device, "cam", None)
        if cam is None:
            self._progress_bar.setVisible(False)
            return

        try:
            acquiring = getattr(cam, "acquire", None)
            if acquiring is None or not acquiring.get():
                self._progress_bar.setVisible(False)
                return

            # Try HDF5 plugin first (most accurate for file-writing acquisitions)
            hdf5 = getattr(self._device, "hdf5", None)
            if hdf5 is not None:
                capture = getattr(hdf5, "capture", None)
                if capture is not None and capture.get():
                    current = int(hdf5.num_captured.get())
                    total = int(cam.num_images.get())
                    self._progress_bar.setMaximum(total)
                    self._progress_bar.setValue(current)
                    self._progress_bar.setVisible(True)
                    return

            # Fall back to cam.array_counter
            counter = getattr(cam, "array_counter", None)
            num_images = getattr(cam, "num_images", None)
            if counter is not None and num_images is not None:
                current = int(counter.get())
                total = int(num_images.get())
                self._progress_bar.setMaximum(total)
                self._progress_bar.setValue(current)
                self._progress_bar.setVisible(True)
                return

            self._progress_bar.setVisible(False)
        except Exception:
            self._progress_bar.setVisible(False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (22 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_ophyd_image_view.py src/lucid/ui/widgets/camera/image_view.py
git commit -m "feat(camera): acquisition progress bar from HDF5/cam counters"
```

---

## Task 8: `DarkFrameManager` — RunEngine subscription and Tiled readback

**Files:**
- Create: `src/lucid/ui/widgets/camera/dark_frames.py`
- Create: `tests/test_dark_frame_manager.py`

`DarkFrameManager` subscribes to the acquisition engine's output stream and watches for
events on the `"dark"` stream. For **simulated devices** (embedded array data), it can
capture dark frames directly from the event documents. For **real file-writing detectors**
(PIMTE3, Andor with HDF5), the event `data` contains datum references, not arrays. In that
case, `DarkFrameManager` records the run UID from the `"dark"` stream and reads the actual
dark frame data from **Tiled** on the `"stop"` document.

Additionally, `load_dark_from_tiled()` searches recent runs for the most recent dark stream
to populate the cache at initialization (historical dark frame lookup).

- [ ] **Step 1: Write `DarkFrameManager` tests**

```python
"""Tests for DarkFrameManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from lucid.ui.widgets.camera.dark_frames import DarkFrameManager


class TestDarkFrameManager:
    """Dark frame caching and RunEngine integration."""

    def test_initial_state(self):
        mgr = DarkFrameManager(device_name="sim_det")
        assert mgr.dark_frame is None
        assert mgr.has_dark is False

    def test_handles_dark_stream_with_embedded_data(self):
        """When dark events contain actual arrays (sim device), cache immediately."""
        mgr = DarkFrameManager(device_name="sim_det")

        mgr("start", {"uid": "run-123", "time": 0})
        mgr("descriptor", {
            "uid": "desc-1",
            "name": "dark",
            "data_keys": {"sim_det_image": {"shape": [480, 640]}},
        })
        mgr("event", {
            "descriptor": "desc-1",
            "data": {"sim_det_image": np.zeros((480, 640))},
            "filled": {"sim_det_image": True},
            "seq_num": 1,
            "time": 0,
        })

        # Dark is cached immediately on event, no need to wait for stop
        assert mgr.has_dark is True
        assert mgr.dark_frame.shape == (480, 640)

    def test_handles_dark_stream_with_datum_reference(self):
        """When dark events contain datum refs (file-writing detector), use Tiled."""
        dark_data = np.full((480, 640), 42.0)

        # Mock Tiled service
        mock_tiled = MagicMock()
        mock_client = MagicMock()
        mock_tiled.is_connected = True
        mock_tiled._client = mock_client
        # Tiled run["dark"]["data"]["sim_det_image"].read() returns xarray
        mock_xarray = MagicMock()
        mock_xarray.values = dark_data[np.newaxis, ...]  # (1, 480, 640)
        mock_run = MagicMock()
        mock_run.__getitem__ = MagicMock(side_effect=lambda k: {
            "dark": MagicMock(
                __getitem__=MagicMock(side_effect=lambda k2: {
                    "data": MagicMock(
                        __getitem__=MagicMock(side_effect=lambda k3: {
                            "sim_det_image": mock_xarray,
                        }[k3])
                    )
                }[k2])
            )
        }[k])
        mock_client.__getitem__ = MagicMock(return_value=mock_run)

        with patch(
            "lucid.ui.widgets.camera.dark_frames.TiledService.get_instance",
            return_value=mock_tiled,
        ):
            mgr = DarkFrameManager(device_name="sim_det")

            mgr("start", {"uid": "run-123", "time": 0})
            mgr("descriptor", {
                "uid": "desc-1",
                "name": "dark",
                "data_keys": {"sim_det_image": {"shape": [480, 640]}},
            })
            # Event with a string datum reference, not an array
            mgr("event", {
                "descriptor": "desc-1",
                "data": {"sim_det_image": "datum-uid-abc"},
                "filled": {},
                "seq_num": 1,
                "time": 0,
            })
            mgr("stop", {"uid": "run-123", "exit_status": "success"})

        assert mgr.has_dark is True
        np.testing.assert_allclose(mgr.dark_frame, 42.0)

    def test_ignores_primary_stream(self):
        """Primary stream events should not set dark frame."""
        mgr = DarkFrameManager(device_name="sim_det")

        mgr("start", {"uid": "run-123", "time": 0})
        mgr("descriptor", {
            "uid": "desc-1",
            "name": "primary",
            "data_keys": {"sim_det_image": {"shape": [480, 640]}},
        })
        mgr("event", {
            "descriptor": "desc-1",
            "data": {"sim_det_image": np.ones((480, 640))},
            "filled": {"sim_det_image": True},
            "seq_num": 1,
            "time": 0,
        })
        mgr("stop", {"uid": "run-123", "exit_status": "success"})

        assert mgr.has_dark is False

    def test_caches_multiple_embedded_dark_frames(self):
        """Multiple dark events with embedded data should be averaged."""
        mgr = DarkFrameManager(device_name="sim_det")

        mgr("start", {"uid": "run-123", "time": 0})
        mgr("descriptor", {
            "uid": "desc-1",
            "name": "dark",
            "data_keys": {"sim_det_image": {"shape": [10, 10]}},
        })

        frame1 = np.full((10, 10), 100.0)
        frame2 = np.full((10, 10), 200.0)

        mgr("event", {
            "descriptor": "desc-1",
            "data": {"sim_det_image": frame1},
            "filled": {"sim_det_image": True},
            "seq_num": 1, "time": 0,
        })
        mgr("event", {
            "descriptor": "desc-1",
            "data": {"sim_det_image": frame2},
            "filled": {"sim_det_image": True},
            "seq_num": 2, "time": 0,
        })
        mgr("stop", {"uid": "run-123", "exit_status": "success"})

        assert mgr.has_dark
        np.testing.assert_allclose(mgr.dark_frame, 150.0)

    def test_clear_dark(self):
        mgr = DarkFrameManager(device_name="sim_det")
        mgr._cached_dark = np.zeros((10, 10))
        assert mgr.has_dark

        mgr.clear()
        assert not mgr.has_dark
        assert mgr.dark_frame is None

    def test_subtract(self):
        """subtract() returns image minus dark frame."""
        mgr = DarkFrameManager(device_name="sim_det")
        mgr._cached_dark = np.full((10, 10), 50.0)

        image = np.full((10, 10), 200.0)
        result = mgr.subtract(image)
        np.testing.assert_allclose(result, 150.0)

    def test_subtract_clips_to_zero(self):
        """Subtraction should not produce negative values."""
        mgr = DarkFrameManager(device_name="sim_det")
        mgr._cached_dark = np.full((10, 10), 200.0)

        image = np.full((10, 10), 50.0)
        result = mgr.subtract(image)
        assert np.all(result >= 0)

    def test_subtract_no_dark_returns_original(self):
        """Without a dark frame, subtract returns the input unchanged."""
        mgr = DarkFrameManager(device_name="sim_det")

        image = np.full((10, 10), 100.0)
        result = mgr.subtract(image)
        np.testing.assert_array_equal(result, image)


class TestLoadDarkFromTiled:
    """Historical dark frame lookup via Tiled."""

    def test_load_from_recent_run(self):
        """load_dark_from_tiled should search recent runs for dark stream."""
        dark_data = np.full((100, 100), 77.0)

        mock_tiled = MagicMock()
        mock_tiled.is_connected = True
        mock_client = MagicMock()
        mock_tiled._client = mock_client

        # Simulate a catalog with one recent run that has a "dark" stream
        mock_xarray = MagicMock()
        mock_xarray.values = dark_data[np.newaxis, ...]
        mock_run = MagicMock()
        mock_run.keys.return_value = ["dark", "primary"]
        mock_dark_stream = MagicMock()
        mock_dark_data = MagicMock()
        mock_dark_data.__getitem__ = MagicMock(
            side_effect=lambda k: {"sim_det_image": mock_xarray}[k]
        )
        mock_dark_stream.__getitem__ = MagicMock(
            side_effect=lambda k: {"data": mock_dark_data}[k]
        )
        mock_run.__getitem__ = MagicMock(
            side_effect=lambda k: {"dark": mock_dark_stream}[k]
        )

        # values_indexer supports [-N:] slicing for recent runs
        mock_client.values_indexer.__getitem__ = MagicMock(return_value=[mock_run])

        with patch(
            "lucid.ui.widgets.camera.dark_frames.TiledService.get_instance",
            return_value=mock_tiled,
        ):
            mgr = DarkFrameManager(device_name="sim_det")
            mgr.load_dark_from_tiled(image_field="sim_det_image", search_last_n=5)

        assert mgr.has_dark
        np.testing.assert_allclose(mgr.dark_frame, 77.0)

    def test_no_tiled_connection_is_noop(self):
        """If Tiled is not connected, load is a no-op."""
        mock_tiled = MagicMock()
        mock_tiled.is_connected = False

        with patch(
            "lucid.ui.widgets.camera.dark_frames.TiledService.get_instance",
            return_value=mock_tiled,
        ):
            mgr = DarkFrameManager(device_name="sim_det")
            mgr.load_dark_from_tiled(image_field="sim_det_image")

        assert not mgr.has_dark
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_dark_frame_manager.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `DarkFrameManager`**

```python
"""Dark frame management for background correction.

DarkFrameManager acts as a Bluesky document callback. Subscribe it to the
acquisition engine to automatically capture dark frames from the "dark"
stream.

Two data paths are supported:
1. **Embedded data** (SimDetector): Dark frame arrays are in the event
   document's `data` dict. Captured inline and averaged on `stop`.
2. **File-written data** (PIMTE3, Andor): Event `data` contains datum
   references (strings). On `stop`, the dark frame is read from Tiled
   using the run UID.

`load_dark_from_tiled()` searches recent Tiled runs for the most recent
dark stream to populate the cache at initialization time.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal

from lucid.utils.logging import logger


class DarkFrameManager(QObject):
    """Manages dark frame capture, caching, and subtraction.

    Signals:
        dark_updated: Emitted when a new dark frame is cached.
        dark_cleared: Emitted when the cached dark frame is cleared.
    """

    dark_updated = Signal()
    dark_cleared = Signal()

    def __init__(self, device_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._device_name = device_name
        self._cached_dark: np.ndarray | None = None

        # Per-run tracking
        self._run_uid: str | None = None
        self._dark_descriptor_uids: set[str] = set()
        self._dark_frames: list[np.ndarray] = []  # Embedded frames only
        self._image_field: str | None = None
        self._has_dark_stream: bool = False  # Whether this run has a dark stream at all

    # === Bluesky Callback Interface ===

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        """Handle a Bluesky document."""
        if name == "start":
            self._on_start(doc)
        elif name == "descriptor":
            self._on_descriptor(doc)
        elif name == "event":
            self._on_event(doc)
        elif name == "stop":
            self._on_stop(doc)

    def _on_start(self, doc: dict[str, Any]) -> None:
        """Reset per-run state."""
        self._run_uid = doc.get("uid")
        self._dark_descriptor_uids.clear()
        self._dark_frames.clear()
        self._image_field = None
        self._has_dark_stream = False

    def _on_descriptor(self, doc: dict[str, Any]) -> None:
        """Track dark stream descriptors and identify the image field."""
        stream_name = doc.get("name", "")
        if stream_name != "dark":
            return

        self._has_dark_stream = True
        self._dark_descriptor_uids.add(doc.get("uid", ""))

        data_keys = doc.get("data_keys", {})
        # Find image field for our device
        for key in data_keys:
            if self._device_name in key and "image" in key:
                self._image_field = key
                break
        # Fallback: first 2D field
        if self._image_field is None:
            for key, info in data_keys.items():
                shape = info.get("shape", [])
                if len(shape) >= 2:
                    self._image_field = key
                    break

    def _on_event(self, doc: dict[str, Any]) -> None:
        """Capture dark frame immediately on each dark event.

        For embedded data (SimDetector): accumulate frames and update
        the running average immediately.
        For file-written data (datum refs): read from Tiled right away —
        the event is only emitted after data is persisted to disk.
        """
        descriptor_uid = doc.get("descriptor", "")
        if descriptor_uid not in self._dark_descriptor_uids:
            return

        if not self._image_field:
            return

        data = doc.get("data", {})
        filled = doc.get("filled", {})
        value = data.get(self._image_field)

        if value is None:
            return

        is_filled = filled.get(self._image_field, False)

        if is_filled and isinstance(value, np.ndarray):
            self._dark_frames.append(value.astype(np.float64))
            self._update_cached_dark()
        elif is_filled and hasattr(value, "__array__"):
            self._dark_frames.append(np.asarray(value, dtype=np.float64))
            self._update_cached_dark()
        elif self._run_uid and self._image_field:
            # Datum reference — read from Tiled immediately
            self._read_dark_from_tiled(self._run_uid, self._image_field)

    def _update_cached_dark(self) -> None:
        """Average all accumulated dark frames and update the cache."""
        if self._dark_frames:
            self._cached_dark = np.mean(self._dark_frames, axis=0)
            logger.info(
                f"Cached dark frame for {self._device_name} "
                f"(inline, {len(self._dark_frames)} frame(s) averaged)"
            )
            self.dark_updated.emit()

    def _on_stop(self, doc: dict[str, Any]) -> None:
        """Clean up per-run state."""
        self._dark_frames.clear()
        self._dark_descriptor_uids.clear()

    def _read_dark_from_tiled(self, run_uid: str, image_field: str) -> None:
        """Read dark frame data from Tiled for a given run."""
        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            if not service.is_connected:
                logger.debug("Tiled not connected — cannot read dark frame")
                return

            client = service._client
            run = client[run_uid]
            dark_data = run["dark"]["data"][image_field]

            # dark_data is an xarray DataArray; .values gives numpy array
            # Shape is typically (n_frames, height, width)
            arr = np.asarray(dark_data.values, dtype=np.float64)
            if arr.ndim == 3:
                arr = np.mean(arr, axis=0)
            elif arr.ndim > 3:
                arr = np.squeeze(arr)
                if arr.ndim == 3:
                    arr = np.mean(arr, axis=0)

            self._cached_dark = arr
            logger.info(
                f"Cached dark frame for {self._device_name} "
                f"(from Tiled run {run_uid[:8]})"
            )
            self.dark_updated.emit()
        except Exception as e:
            logger.warning(f"Failed to read dark from Tiled: {e}")

    # === Historical Lookup ===

    def load_dark_from_tiled(
        self, image_field: str | None = None, search_last_n: int = 10
    ) -> None:
        """Search recent Tiled runs for the most recent dark frame.

        Call this at initialization to populate the dark cache from
        historical data. Searches the most recent `search_last_n` runs
        for one that contains a "dark" stream.

        Args:
            image_field: The data key for the image field (e.g., "sim_det_image").
                         If None, uses "{device_name}_image".
            search_last_n: How many recent runs to search.
        """
        if image_field is None:
            image_field = f"{self._device_name}_image"

        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            if not service.is_connected:
                logger.debug("Tiled not connected — cannot search for historical darks")
                return

            client = service._client

            # Get the most recent N runs (Tiled supports slicing)
            recent_runs = client.values_indexer[-search_last_n:]

            for run in reversed(list(recent_runs)):
                try:
                    if "dark" in run.keys():
                        dark_data = run["dark"]["data"][image_field]
                        arr = np.asarray(dark_data.values, dtype=np.float64)
                        if arr.ndim == 3:
                            arr = np.mean(arr, axis=0)
                        elif arr.ndim > 3:
                            arr = np.squeeze(arr)
                            if arr.ndim == 3:
                                arr = np.mean(arr, axis=0)

                        self._cached_dark = arr
                        logger.info(
                            f"Loaded historical dark frame for {self._device_name} "
                            f"from Tiled"
                        )
                        self.dark_updated.emit()
                        return
                except Exception:
                    continue  # Try next run

            logger.debug(f"No historical dark frame found in last {search_last_n} runs")
        except Exception as e:
            logger.warning(f"Failed to search Tiled for historical darks: {e}")

    # === Public API ===

    @property
    def has_dark(self) -> bool:
        """Whether a dark frame is cached."""
        return self._cached_dark is not None

    @property
    def dark_frame(self) -> np.ndarray | None:
        """The cached dark frame, or None."""
        return self._cached_dark

    def subtract(self, image: np.ndarray) -> np.ndarray:
        """Subtract the cached dark frame from an image.

        Returns the original image if no dark frame is cached.
        Clips result to zero (no negative values).
        """
        if self._cached_dark is None:
            return image

        if self._cached_dark.shape != image.shape:
            logger.warning(
                f"Dark frame shape {self._cached_dark.shape} doesn't match "
                f"image shape {image.shape} — skipping subtraction"
            )
            return image

        result = image.astype(np.float64) - self._cached_dark
        np.clip(result, 0, None, out=result)
        return result.astype(image.dtype)

    def clear(self) -> None:
        """Clear the cached dark frame."""
        self._cached_dark = None
        self.dark_cleared.emit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_dark_frame_manager.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/widgets/camera/dark_frames.py tests/test_dark_frame_manager.py
git commit -m "feat(camera): DarkFrameManager with inline capture, Tiled readback, and historical lookup"
```

---

## Task 9: Background correction toggle in `OphydImageView`

**Files:**
- Modify: `tests/test_ophyd_image_view.py`
- Modify: `src/lucid/ui/widgets/camera/image_view.py`

Adds a "BG Correct" toggle button to the toolbar. When checked and a dark frame is available, the cached dark is subtracted from each frame before display.

- [ ] **Step 1: Write background correction tests**

Add to `tests/test_ophyd_image_view.py`:

```python
from lucid.ui.widgets.camera.dark_frames import DarkFrameManager


class TestBackgroundCorrection:
    """Dark frame subtraction in the image viewer."""

    def test_bg_correct_button_exists(self, qapp):
        device = _make_mock_device()
        view = OphydImageView(device)
        assert view._bg_correct_btn is not None
        assert view._bg_correct_btn.isCheckable()
        view.close()

    def test_bg_correct_subtracts_dark(self, qapp):
        """When BG correction is on and dark is cached, subtract it."""
        data = np.full((100, 100), 200, dtype=np.uint16)
        dark = np.full((100, 100), 50, dtype=np.float64)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._dark_manager._cached_dark = dark
        view._bg_correct_btn.setChecked(True)
        view._display_array(data)

        displayed = view._image_item.image
        np.testing.assert_allclose(displayed, 150, atol=1)
        view.close()

    def test_bg_correct_off_shows_raw(self, qapp):
        """When BG correction is off, raw data is shown."""
        data = np.full((100, 100), 200, dtype=np.uint16)
        dark = np.full((100, 100), 50, dtype=np.float64)
        device = _make_mock_device(data)
        view = OphydImageView(device)

        view._dark_manager._cached_dark = dark
        view._bg_correct_btn.setChecked(False)
        view._display_array(data)

        displayed = view._image_item.image
        np.testing.assert_allclose(displayed, 200, atol=1)
        view.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py::TestBackgroundCorrection -v`
Expected: FAIL — `AttributeError: 'OphydImageView' has no attribute '_bg_correct_btn'`

- [ ] **Step 3: Wire BG correction into `OphydImageView`**

In `__init__`:

```python
        self._dark_manager = DarkFrameManager(
            device_name=ophyd_device.name if hasattr(ophyd_device, "name") else "unknown"
        )
```

Add "BG Correct" button to toolbar in `_setup_ui()`:

```python
        self._bg_correct_btn = QPushButton("BG Correct")
        self._bg_correct_btn.setFixedHeight(24)
        self._bg_correct_btn.setCheckable(True)
        toolbar.addWidget(self._bg_correct_btn)
```

Modify `_display_array()` to apply correction:

```python
    def _display_array(self, array: np.ndarray, image_plugin: Any = None) -> None:
        if array is None or array.size == 0:
            return

        arr = np.squeeze(array)

        if arr.ndim == 1:
            width, height = self._get_image_dimensions(image_plugin)
            if width and height and width * height == arr.size:
                arr = arr.reshape((height, width))
            else:
                return

        if arr.ndim != 2:
            return

        # Background correction
        if self._bg_correct_btn.isChecked():
            arr = self._dark_manager.subtract(arr)

        auto_levels = self._first_frame
        self._image_item.setImage(arr, autoLevels=auto_levels)
        if self._first_frame:
            self._first_frame = False
            self._plot_item.getViewBox().autoRange()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py -v`
Expected: PASS (25 tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_ophyd_image_view.py src/lucid/ui/widgets/camera/image_view.py
git commit -m "feat(camera): background correction toggle using DarkFrameManager"
```

---

## Task 10: Wire `DarkFrameManager` into `PlanBasedCameraControlWidget`

**Files:**
- Modify: `src/lucid/ui/widgets/camera/plan_based.py`
- Modify: `src/lucid/ui/widgets/camera/base.py`

The `PlanBasedCameraControlWidget` subscribes the `DarkFrameManager` to the engine so dark frames are captured automatically. It also adds a "Capture Dark" button that runs a dark-only acquisition plan.

In `base.py`, the old `OphydImageView` class is removed and replaced with the import from `image_view.py`.

- [ ] **Step 1: Update `base.py` — remove old `OphydImageView`, import new one**

In `base.py`, remove the old `OphydImageView` class (lines 200-344) and add at the top:

```python
from lucid.ui.widgets.camera.image_view import OphydImageView
```

The `_update_image_view()` method (line 637) already creates `OphydImageView(self._device)` — no change needed there.

- [ ] **Step 2: Add `DarkFrameManager` wiring and Capture Dark button to `plan_based.py`**

```python
from lucid.ui.widgets.camera.dark_frames import DarkFrameManager

# In PlanBasedCameraControlWidget:

    def __init__(self, parent: QWidget | None = None) -> None:
        self._collect_dark_checkbox: QCheckBox | None = None
        self._dark_manager_token: int | None = None
        super().__init__(parent)

    def _create_device_panels(self) -> list[QGroupBox]:
        panels = super()._create_device_panels()

        # Existing acquisition options panel code stays the same...

        # Add Capture Dark button to options panel
        self._capture_dark_btn = QPushButton("Capture Dark")
        self._capture_dark_btn.setToolTip(
            "Capture a dark frame now (closes shutter, acquires, reopens)"
        )
        self._capture_dark_btn.clicked.connect(self._on_capture_dark)
        options_layout.addWidget(self._capture_dark_btn)

        panels.insert(0, options_group)
        return panels

    def _update_image_view(self) -> None:
        """Create image view, subscribe DarkFrameManager, load historical dark."""
        super()._update_image_view()

        if self._image_view is not None:
            self._subscribe_dark_manager()
            # Try to load a historical dark frame from Tiled
            self._image_view._dark_manager.load_dark_from_tiled()

    def _subscribe_dark_manager(self) -> None:
        """Subscribe the DarkFrameManager to the acquisition engine."""
        self._unsubscribe_dark_manager()
        try:
            from lucid.acquire.engine import get_engine
            engine = get_engine()
            dark_mgr = self._image_view._dark_manager
            self._dark_manager_token = engine.subscribe(dark_mgr)
        except Exception as e:
            logger.debug(f"Could not subscribe dark manager: {e}")

    def _unsubscribe_dark_manager(self) -> None:
        """Unsubscribe from engine."""
        if self._dark_manager_token is not None:
            try:
                from lucid.acquire.engine import get_engine
                get_engine().unsubscribe(self._dark_manager_token)
            except Exception:
                pass
            self._dark_manager_token = None

    def _on_capture_dark(self) -> None:
        """Run a dark-frame-only acquisition plan."""
        if self._device is None:
            return

        from lucid.acquire.plans.ncs_plans import simple_acquire
        plan = simple_acquire(detector=self._device, num_images=1, collect_dark=True)

        try:
            from lucid.acquire.engine import get_engine
            get_engine().submit(plan)
        except Exception as e:
            logger.error(f"Failed to capture dark: {e}")

    def closeEvent(self, event) -> None:
        self._unsubscribe_dark_manager()
        super().closeEvent(event)
```

- [ ] **Step 3: Update `__init__.py` exports**

```python
from lucid.ui.widgets.camera.dark_frames import DarkFrameManager
from lucid.ui.widgets.camera.image_view import OphydImageView

__all__ = [
    "CameraControlWidget",
    "DarkFrameManager",
    "OphydImageView",
    "PlanBasedCameraControlWidget",
    "TVModeMixin",
]
```

- [ ] **Step 4: Run the full test suite**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_ophyd_image_view.py tests/test_dark_frame_manager.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/widgets/camera/base.py src/lucid/ui/widgets/camera/plan_based.py src/lucid/ui/widgets/camera/__init__.py
git commit -m "feat(camera): wire DarkFrameManager into plan-based widget, add Capture Dark button"
```

---

## Task 11: Run full test suite and fix regressions

**Files:**
- Possibly: any files touched in prior tasks

Run the full project test suite to catch regressions from the `base.py` refactor (old `OphydImageView` removal).

- [ ] **Step 1: Run full test suite**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/ -v --tb=short 2>&1 | head -100`

- [ ] **Step 2: Fix any import errors or regressions**

The main risk is other code importing `OphydImageView` from `base.py`. Check for imports:

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs && grep -r "from lucid.ui.widgets.camera.base import.*OphydImageView" src/
```

If any are found, update them to import from `image_view` instead. The `__init__.py` re-export should cover most cases.

- [ ] **Step 3: Run full suite again to confirm green**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 4: Commit any fixes**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add -u
git commit -m "fix: resolve import regressions from OphydImageView refactor"
```

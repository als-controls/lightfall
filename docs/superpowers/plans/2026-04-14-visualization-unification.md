# Visualization Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three visualization data paths (eager live, eager historical, lazy tiled) with a single tiled-only path, using a new `BaseVisualization` ABC.

**Architecture:** Each visualization widget inherits from `BaseVisualization` and reads data directly from a tiled BlueskyRun entry. The `VisualizationPanel` controller calls `open_run(entry)` to score, create, and configure the winning widget. Stream/field selection is driven by the widget's `get_streams()`/`get_fields()` methods. Live runs poll via `refresh()`.

**Tech Stack:** PySide6, pyqtgraph, tiled (client), bluesky_tiled_plugins

**Spec:** `docs/superpowers/specs/2026-04-14-visualization-unification-design.md`

---

### Task 1: Create BaseVisualization ABC

**Files:**
- Create: `src/lucid/visualization/base_visualization.py`

This is the new ABC that all visualization widgets will inherit from. Created as a new file to avoid conflicts with the old `base.py` during migration.

- [ ] **Step 1: Write BaseVisualization**

```python
# src/lucid/visualization/base_visualization.py
"""Base class for tiled-backed visualization widgets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from PySide6.QtWidgets import QWidget


class BaseVisualization(QWidget, ABC):
    """Abstract base for visualization widgets that read from tiled.

    Every visualization receives a BlueskyRun tiled entry via set_run(),
    then displays data from a selected stream and field. The controller
    (VisualizationPanel) orchestrates the selection flow:

        1. can_handle(run) to score
        2. set_run(run) to bind
        3. get_streams() to populate stream combo
        4. set_stream(name) to display (auto-picks best field)
        5. get_fields() to populate field combo
        6. set_field(name) for user override
        7. refresh() on timer for live runs

    Subclasses must define class-level metadata:
        viz_name: str           — unique id (e.g. "image_stack")
        viz_display_name: str   — UI label (e.g. "Image Stack")
        viz_icon: str           — icon name (e.g. "images")
    """

    viz_name: str = ""
    viz_display_name: str = ""
    viz_icon: str = "chart-line"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run: Any | None = None
        self._stream_name: str = ""
        self._field_name: str = ""

    @staticmethod
    @abstractmethod
    def can_handle(run: Any) -> int:
        """Score 0-100 for how well this viz handles the given run.

        Access run.metadata["start"] for scan geometry and
        run[stream].metadata["data_keys"] for field types.
        """

    @abstractmethod
    def set_run(self, run: Any) -> None:
        """Set the BlueskyRun tiled entry. Cache reference and start metadata."""

    @abstractmethod
    def get_streams(self) -> list[str]:
        """Stream names sorted by this viz's preference."""

    @abstractmethod
    def set_stream(self, stream_name: str) -> None:
        """Select stream. Read metadata, auto-pick best field, render."""

    @abstractmethod
    def get_fields(self) -> list[str]:
        """Field names for current stream, sorted by preference."""

    @abstractmethod
    def set_field(self, field_name: str) -> None:
        """Switch field within current stream."""

    @abstractmethod
    def refresh(self) -> None:
        """Poll for new data. No-op for completed runs."""
```

- [ ] **Step 2: Commit**

```bash
git add src/lucid/visualization/base_visualization.py
git commit -m "Add BaseVisualization ABC for tiled-only viz path"
```

---

### Task 2: Port ImageStackVisualization

**Files:**
- Create: `src/lucid/visualization/widgets/image_stack.py`
- Reference: `src/lucid/visualization/widgets/image_sequence.py` (old, kept temporarily)
- Reference: `src/lucid/visualization/widgets/lazy_image_view.py` (kept, reused)

Port the image stack visualization to the new ABC. This is the most complex widget (lazy ArrayClient, LazyImageView, timeline, ROI, log intensity, dark frames). The old `image_sequence.py` stays until all widgets are ported and the controller is updated.

- [ ] **Step 1: Create the new ImageStackVisualization**

The new widget reads directly from the tiled stream. Key differences from old:
- Constructor takes no `spec` or `buffer` — just `parent`
- `set_stream` reads `data_keys` from stream metadata, finds 2D+ fields, sets up `LazyImageView`
- `refresh` checks `stream[field].shape[0]` for new frames
- No `_on_new_point`, no `_images` list, no `_update_image_stack`

Write `src/lucid/visualization/widgets/image_stack.py` with the full implementation. Key sections:

```python
# src/lucid/visualization/widgets/image_stack.py
"""Image stack visualization backed by tiled ArrayClient."""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.visualization.base_visualization import BaseVisualization
from lucid.visualization.widgets.lazy_image_view import LazyImageView
from lucid.visualization.widgets.time_axis import HumanReadableTimeAxis


class ImageStackVisualization(BaseVisualization):
    """Image stack visualization reading from tiled ArrayClient.

    Displays a sequence of 2D images with timeline navigation,
    ROI selection, and lazy per-frame HTTP fetching.
    """

    viz_name = "image_stack"
    viz_display_name = "Image Stack"
    viz_icon = "images"

    def __init__(self, parent: QWidget | None = None) -> None:
        self._image_view: LazyImageView | None = None
        self._stream: Any | None = None
        self._image_client: Any | None = None
        self._frame_shape: tuple[int, ...] = ()
        self._current_frame: int = 0
        self._last_frame_count: int = 0

        # Image processing state
        self._log_mode: bool = False

        # ROI state
        self._roi: pg.RectROI | None = None
        self._roi_curves: list[pg.PlotDataItem] = []

        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the UI: toolbar + LazyImageView + timeline."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Image view
        self._image_view = LazyImageView()
        self._image_view.ui.roiPlot.show()
        self._image_view.ui.roiPlot.setMinimumHeight(80)
        self._image_view.ui.splitter.setSizes([400, 100])

        self._time_axis = HumanReadableTimeAxis(orientation="bottom")
        self._image_view.ui.roiPlot.setAxisItems({"bottom": self._time_axis})
        self._image_view.sigTimeChanged.connect(self._on_time_changed)

        timeline = self._image_view.timeLine
        timeline.setPen(pg.mkPen("y", width=3))
        timeline.setHoverPen(pg.mkPen("y", width=5))
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()

        self._apply_colormap("viridis")
        main_layout.addWidget(self._image_view)

        container = QWidget()
        container.setLayout(main_layout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar with colormap, Reset LUT, log, ROI."""
        import qtawesome as qta

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Colormap selector
        cmap_label = QLabel("Colormap:")
        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems([
            "viridis", "plasma", "inferno", "magma", "gray", "hot",
        ])
        self._cmap_combo.currentTextChanged.connect(self._apply_colormap)
        toolbar.addWidget(cmap_label)
        toolbar.addWidget(self._cmap_combo)

        toolbar.addStretch()

        # Reset LUT
        reset_lut_btn = QPushButton(qta.icon("mdi6.chart-histogram"), "Reset LUT")
        reset_lut_btn.setFixedHeight(24)
        reset_lut_btn.clicked.connect(self._on_reset_lut)
        toolbar.addWidget(reset_lut_btn)

        # Reset Axes
        reset_axes_btn = QPushButton(qta.icon("mdi6.magnify"), "Reset Axes")
        reset_axes_btn.setFixedHeight(24)
        reset_axes_btn.clicked.connect(
            lambda: self._image_view.getView().autoRange() if self._image_view else None
        )
        toolbar.addWidget(reset_axes_btn)

        # Log Intensity toggle
        self._log_icon_off = qta.icon("mdi6.lightbulb")
        self._log_icon_on = qta.icon("mdi6.lightbulb-on-outline")
        self._log_btn = QPushButton(self._log_icon_off, "Log Intensity")
        self._log_btn.setFixedHeight(24)
        self._log_btn.setCheckable(True)
        self._log_btn.toggled.connect(self._on_log_toggled)
        toolbar.addWidget(self._log_btn)

        # ROI toggle
        self._roi_btn = QPushButton("ROI")
        self._roi_btn.setCheckable(True)
        self._roi_btn.toggled.connect(self._on_roi_toggled)
        toolbar.addWidget(self._roi_btn)

        return toolbar

    # === BaseVisualization interface ===

    @staticmethod
    def can_handle(run: Any) -> int:
        start = run.metadata.get("start", {})
        for stream_name in run:
            try:
                data_keys = run[stream_name].metadata.get("data_keys", {})
            except Exception:
                continue
            for _key, info in data_keys.items():
                shape = info.get("shape", [])
                # Per-frame shape is last 2 dims; need at least 2D
                if len(shape) >= 2:
                    return 75
        return 0

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        if not self._run:
            return []
        names = list(self._run.keys())
        # Put "primary" first if it exists
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
        except Exception as e:
            logger.error("Cannot access stream '{}': {}", stream_name, e)
            return

        # Auto-select best field
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        if not self._stream:
            return []
        data_keys = self._stream.metadata.get("data_keys", {})
        hints = self._stream.metadata.get("hints", {})

        # Collect hinted field names
        hinted = set()
        for device_hints in hints.values():
            if isinstance(device_hints, dict):
                hinted.update(device_hints.get("fields", []))

        # Partition: hinted 2D+ first, then other 2D+, then rest
        image_hinted = []
        image_other = []
        rest = []
        for key, info in data_keys.items():
            shape = info.get("shape", [])
            if len(shape) >= 2:
                if key in hinted:
                    image_hinted.append(key)
                else:
                    image_other.append(key)
            else:
                rest.append(key)

        return image_hinted + image_other + rest

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        if not self._stream:
            return

        # Resolve the ArrayClient for this field
        stream_keys = list(self._stream.keys())
        image_client = None
        if field_name in stream_keys:
            image_client = self._stream[field_name]
        elif "external" in stream_keys:
            try:
                ext = self._stream["external"]
                if field_name in ext:
                    image_client = ext[field_name]
            except Exception:
                pass

        if image_client is None:
            logger.warning("Field '{}' not found in stream '{}'", field_name, self._stream_name)
            return

        self._image_client = image_client
        self._frame_shape = tuple(image_client.shape[-2:])

        # Read timestamps from internal/events table or stream
        timestamps = self._read_timestamps()

        # Configure LazyImageView
        self._image_view.setArraySource(image_client, timestamps, self._frame_shape)

        n_frames = image_client.shape[0]
        self._last_frame_count = n_frames
        if n_frames > 0:
            self._image_view.setCurrentIndex(n_frames - 1)
            self._current_frame = n_frames - 1
            self._on_reset_lut()

        self._update_status()

    def refresh(self) -> None:
        if not self._image_client or not self._image_view:
            return
        try:
            n_frames = self._image_client.shape[0]
        except Exception:
            return
        if n_frames > self._last_frame_count:
            timestamps = self._read_timestamps()
            self._image_view.updateFrameCount(n_frames, timestamps)
            self._image_view.setCurrentIndex(n_frames - 1)
            self._current_frame = n_frames - 1
            self._last_frame_count = n_frames
            self._update_status()

    # === Internal helpers ===

    def _read_timestamps(self) -> np.ndarray:
        """Read timestamps from the current stream."""
        if not self._stream:
            return np.array([])
        stream_keys = list(self._stream.keys())

        # Try internal/events table first (bluesky_tiled_plugins)
        if "internal" in stream_keys:
            try:
                internal = self._stream["internal"]
                if "events" in internal:
                    table = internal["events"].read()
                    if "time" in table.columns:
                        return np.asarray(table["time"], dtype=np.float64)
            except Exception as e:
                logger.debug("Could not read internal/events timestamps: {}", e)

        # Fallback: direct time array
        if "time" in stream_keys:
            try:
                return np.asarray(self._stream["time"].read(), dtype=np.float64)
            except Exception as e:
                logger.debug("Could not read time array: {}", e)

        return np.array([])

    def _apply_colormap(self, cmap_name: str) -> None:
        try:
            cmap = pg.colormap.get(cmap_name)
            if cmap and self._image_view:
                self._image_view.setColorMap(cmap)
        except Exception as e:
            logger.debug("Could not apply colormap: {}", e)

    def _on_reset_lut(self) -> None:
        """Reset LUT using 1st/99th percentile bounds."""
        if not self._image_view:
            return
        img = self._image_view.imageItem.image
        if img is None:
            return
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            step = max(1, img.size // 1_000_000)
            data = img.ravel()[::step].astype(np.float64)
            if data.size == 0:
                return
            lo = float(np.nanpercentile(data, 1))
            hi = float(np.nanpercentile(data, 99))
            if lo >= hi:
                lo, hi = float(np.nanmin(data)), float(np.nanmax(data))
            if lo == hi:
                hi = lo + 1.0
        self._image_view.setLevels(lo, hi)

    def _on_log_toggled(self, checked: bool) -> None:
        self._log_mode = checked
        self._log_btn.setIcon(self._log_icon_on if checked else self._log_icon_off)
        if self._image_view:
            self._image_view.set_log_mode(checked)

    def _on_time_changed(self, ind: int, time: float) -> None:
        self._current_frame = ind
        self._update_status()

    def _on_roi_toggled(self, enabled: bool) -> None:
        if enabled:
            self._create_roi()
            if self._roi:
                self._roi.show()
        else:
            if self._roi:
                self._roi.hide()

    def _create_roi(self) -> None:
        if self._roi is not None or not self._frame_shape:
            return
        h, w = self._frame_shape
        self._roi = pg.RectROI(
            [w // 4, h // 4], [w // 2, h // 2],
            pen=pg.mkPen("r", width=2),
        )
        self._roi.addScaleHandle([1, 1], [0, 0])
        self._roi.addScaleHandle([0, 0], [1, 1])
        self._image_view.addItem(self._roi)

    def _update_status(self) -> None:
        if self._image_client is not None:
            total = self._image_client.shape[0]
        else:
            total = 0
        current = self._current_frame + 1 if total > 0 else 0
        self._time_axis.setLabel(f"Frame {current}/{total}")
```

- [ ] **Step 2: Commit**

```bash
git add src/lucid/visualization/widgets/image_stack.py
git commit -m "Add new ImageStackVisualization on BaseVisualization ABC"
```

---

### Task 3: Port Scalar Widgets (Plot, Heatmap, Scatter, Table)

**Files:**
- Create: `src/lucid/visualization/widgets/plot_1d.py`
- Create: `src/lucid/visualization/widgets/heatmap_new.py`
- Create: `src/lucid/visualization/widgets/scatter_new.py`
- Create: `src/lucid/visualization/widgets/table_new.py`

All four scalar widgets follow the same pattern: `set_stream` reads scalars eagerly, `refresh` re-reads to find new rows. Create each as a new file next to its old counterpart.

Since these widgets are structurally similar (read scalars, plot them), I'll show the pattern with Plot1D in full and then the key differences for the others.

- [ ] **Step 1: Create Plot1DVisualization**

```python
# src/lucid/visualization/widgets/plot_1d.py
"""1D plot visualization backed by tiled."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from lucid.visualization.base_visualization import BaseVisualization


class Plot1DVisualization(BaseVisualization):
    """1D line plot for scalar data from tiled runs."""

    viz_name = "plot_1d"
    viz_display_name = "1D Plot"
    viz_icon = "chart-line"

    def __init__(self, parent: QWidget | None = None) -> None:
        self._plot_widget: pg.PlotWidget | None = None
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._stream: Any | None = None
        self._x_field: str = ""
        self._last_count: int = 0
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("X:"))
        self._x_combo = QComboBox()
        self._x_combo.setMinimumWidth(100)
        self._x_combo.currentTextChanged.connect(self._on_x_changed)
        toolbar.addWidget(self._x_combo)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._plot_widget = pg.PlotWidget()
        self._plot_widget.addLegend()
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        layout.addWidget(self._plot_widget)

    @staticmethod
    def can_handle(run: Any) -> int:
        start = run.metadata.get("start", {})
        hints = start.get("hints", {})
        dims = hints.get("dimensions", [])
        # 1D scan with scalar data
        if len(dims) <= 1:
            for stream_name in run:
                try:
                    data_keys = run[stream_name].metadata.get("data_keys", {})
                except Exception:
                    continue
                for info in data_keys.values():
                    if len(info.get("shape", [])) == 0:
                        return 80
        return 0

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        if not self._run:
            return []
        names = list(self._run.keys())
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
        except Exception as e:
            logger.error("Cannot access stream '{}': {}", stream_name, e)
            return

        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        if not self._stream:
            return []
        data_keys = self._stream.metadata.get("data_keys", {})
        hints = self._stream.metadata.get("hints", {})
        start = self._run.metadata.get("start", {}) if self._run else {}

        hinted = set()
        for device_hints in hints.values():
            if isinstance(device_hints, dict):
                hinted.update(device_hints.get("fields", []))

        # Exclude motor (independent) fields
        motors = set(start.get("motors", []))

        scalar_hinted = []
        scalar_other = []
        for key, info in data_keys.items():
            if key in motors:
                continue
            if len(info.get("shape", [])) == 0 and info.get("dtype") in ("number", "integer"):
                if key in hinted:
                    scalar_hinted.append(key)
                else:
                    scalar_other.append(key)

        return scalar_hinted + scalar_other

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._load_and_plot()

    def refresh(self) -> None:
        self._load_and_plot()

    def _load_and_plot(self) -> None:
        """Read scalar data from tiled and update plot."""
        if not self._stream or not self._field_name:
            return

        data_keys = self._stream.metadata.get("data_keys", {})
        start = self._run.metadata.get("start", {}) if self._run else {}
        motors = start.get("motors", [])

        # Read data from internal/events table
        table = self._read_events_table()
        if table is None:
            return

        # Determine X axis: first motor, or seq_num
        x_field = motors[0] if motors and motors[0] in table.columns else "seq_num"

        # Populate X combo if needed
        x_options = [c for c in table.columns if c not in ("time",)]
        self._x_combo.blockSignals(True)
        self._x_combo.clear()
        self._x_combo.addItems(x_options)
        idx = self._x_combo.findText(x_field)
        if idx >= 0:
            self._x_combo.setCurrentIndex(idx)
        self._x_combo.blockSignals(False)
        self._x_field = x_field

        x = np.asarray(table[self._x_field]) if self._x_field in table.columns else np.arange(len(table))
        y = np.asarray(table[self._field_name]) if self._field_name in table.columns else np.array([])

        if len(x) == 0 or len(y) == 0:
            return

        # Update or create curve
        if self._field_name not in self._curves:
            self._plot_widget.clear()
            curve = self._plot_widget.plot(x, y, pen=pg.mkPen("y", width=2), name=self._field_name)
            self._curves[self._field_name] = curve
        else:
            self._curves[self._field_name].setData(x, y)

        self._plot_widget.setLabel("bottom", self._x_field)
        self._plot_widget.setLabel("left", self._field_name)
        self._last_count = len(x)

    def _read_events_table(self):
        """Read the internal/events table from the current stream."""
        stream_keys = list(self._stream.keys())
        if "internal" in stream_keys:
            try:
                internal = self._stream["internal"]
                if "events" in internal:
                    return internal["events"].read()
            except Exception as e:
                logger.debug("Could not read events table: {}", e)
        return None

    def _on_x_changed(self, x_field: str) -> None:
        self._x_field = x_field
        self._load_and_plot()
```

- [ ] **Step 2: Create HeatmapVisualization**

```python
# src/lucid/visualization/widgets/heatmap_new.py
"""Heatmap visualization backed by tiled."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lucid.visualization.base_visualization import BaseVisualization


class HeatmapVisualization(BaseVisualization):
    """2D color map for rectilinear grid data from tiled runs."""

    viz_name = "heatmap"
    viz_display_name = "Heatmap"
    viz_icon = "grid"

    def __init__(self, parent: QWidget | None = None) -> None:
        self._plot_widget: pg.PlotWidget | None = None
        self._image_item: pg.ImageItem | None = None
        self._stream: Any | None = None
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._plot_widget = pg.PlotWidget()
        self._image_item = pg.ImageItem()
        self._plot_widget.addItem(self._image_item)
        layout.addWidget(self._plot_widget)

    @staticmethod
    def can_handle(run: Any) -> int:
        start = run.metadata.get("start", {})
        hints = start.get("hints", {})
        dims = hints.get("dimensions", [])
        gridding = hints.get("gridding")
        if len(dims) == 2 and gridding == "rectilinear":
            return 85
        if len(dims) == 2:
            return 30
        return 0

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        if not self._run:
            return []
        names = list(self._run.keys())
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
        except Exception as e:
            logger.error("Cannot access stream '{}': {}", stream_name, e)
            return
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        if not self._stream:
            return []
        data_keys = self._stream.metadata.get("data_keys", {})
        hints = self._stream.metadata.get("hints", {})
        start = self._run.metadata.get("start", {}) if self._run else {}
        motors = set(start.get("motors", []))
        hinted = set()
        for dh in hints.values():
            if isinstance(dh, dict):
                hinted.update(dh.get("fields", []))

        scalar_hinted = []
        scalar_other = []
        for key, info in data_keys.items():
            if key in motors:
                continue
            if len(info.get("shape", [])) == 0 and info.get("dtype") in ("number", "integer"):
                if key in hinted:
                    scalar_hinted.append(key)
                else:
                    scalar_other.append(key)
        return scalar_hinted + scalar_other

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._load_and_plot()

    def refresh(self) -> None:
        self._load_and_plot()

    def _load_and_plot(self) -> None:
        if not self._stream or not self._field_name:
            return
        table = self._read_events_table()
        if table is None or self._field_name not in table.columns:
            return
        start = self._run.metadata.get("start", {}) if self._run else {}
        shape = tuple(start.get("shape", []))
        z = np.asarray(table[self._field_name])
        if shape and len(shape) == 2:
            expected = shape[0] * shape[1]
            if len(z) >= expected:
                grid = z[:expected].reshape(shape[1], shape[0])
            else:
                grid = np.full((shape[1], shape[0]), np.nan)
                grid.ravel()[:len(z)] = z
        else:
            side = int(np.ceil(np.sqrt(len(z))))
            grid = np.full(side * side, np.nan)
            grid[:len(z)] = z
            grid = grid.reshape(side, side)
        self._image_item.setImage(grid.T)

    def _read_events_table(self):
        stream_keys = list(self._stream.keys())
        if "internal" in stream_keys:
            try:
                internal = self._stream["internal"]
                if "events" in internal:
                    return internal["events"].read()
            except Exception as e:
                logger.debug("Could not read events table: {}", e)
        return None
```

- [ ] **Step 3: Create ScatterVisualization**

```python
# src/lucid/visualization/widgets/scatter_new.py
"""Scatter plot visualization backed by tiled."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lucid.visualization.base_visualization import BaseVisualization


class ScatterVisualization(BaseVisualization):
    """Scatter plot for irregular 2D data from tiled runs."""

    viz_name = "scatter"
    viz_display_name = "Scatter Plot"
    viz_icon = "scatter-chart"

    def __init__(self, parent: QWidget | None = None) -> None:
        self._plot_widget: pg.PlotWidget | None = None
        self._scatter_item: pg.ScatterPlotItem | None = None
        self._stream: Any | None = None
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._plot_widget = pg.PlotWidget()
        self._scatter_item = pg.ScatterPlotItem(size=8, pen=pg.mkPen(None), brush=pg.mkBrush("y"))
        self._plot_widget.addItem(self._scatter_item)
        layout.addWidget(self._plot_widget)

    @staticmethod
    def can_handle(run: Any) -> int:
        start = run.metadata.get("start", {})
        hints = start.get("hints", {})
        dims = hints.get("dimensions", [])
        gridding = hints.get("gridding")
        if len(dims) == 2 and gridding != "rectilinear":
            return 70
        if len(dims) == 2:
            return 50
        return 0

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        if not self._run:
            return []
        names = list(self._run.keys())
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
        except Exception as e:
            logger.error("Cannot access stream '{}': {}", stream_name, e)
            return
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        if not self._stream:
            return []
        data_keys = self._stream.metadata.get("data_keys", {})
        start = self._run.metadata.get("start", {}) if self._run else {}
        motors = set(start.get("motors", []))
        return [
            k for k, v in data_keys.items()
            if k not in motors and len(v.get("shape", [])) == 0
            and v.get("dtype") in ("number", "integer")
        ]

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._load_and_plot()

    def refresh(self) -> None:
        self._load_and_plot()

    def _load_and_plot(self) -> None:
        if not self._stream or not self._field_name:
            return
        start = self._run.metadata.get("start", {}) if self._run else {}
        motors = start.get("motors", [])
        if len(motors) < 2:
            return
        table = self._read_events_table()
        if table is None:
            return
        x = np.asarray(table[motors[0]]) if motors[0] in table.columns else np.array([])
        y = np.asarray(table[motors[1]]) if motors[1] in table.columns else np.array([])
        z = np.asarray(table[self._field_name]) if self._field_name in table.columns else np.array([])
        n = min(len(x), len(y), len(z)) if len(z) > 0 else min(len(x), len(y))
        if n == 0:
            return
        brushes = [pg.mkBrush("y")] * n
        if len(z) >= n:
            cmap = pg.colormap.get("viridis")
            z_norm = z[:n]
            lo, hi = z_norm.min(), z_norm.max()
            if hi > lo:
                z_norm = (z_norm - lo) / (hi - lo)
            colors = cmap.map(z_norm, mode="qcolor")
            brushes = [pg.mkBrush(c) for c in colors]
        self._scatter_item.setData(x[:n], y[:n], brush=brushes)
        self._plot_widget.setLabel("bottom", motors[0])
        self._plot_widget.setLabel("left", motors[1])

    def _read_events_table(self):
        stream_keys = list(self._stream.keys())
        if "internal" in stream_keys:
            try:
                internal = self._stream["internal"]
                if "events" in internal:
                    return internal["events"].read()
            except Exception as e:
                logger.debug("Could not read events table: {}", e)
        return None
```

- [ ] **Step 4: Create TableVisualization**

```python
# src/lucid/visualization/widgets/table_new.py
"""Table visualization backed by tiled."""

from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QHeaderView, QTableView, QVBoxLayout, QWidget

from lucid.visualization.base_visualization import BaseVisualization


class _TableModel(QAbstractTableModel):
    """Simple table model backed by a dict of arrays."""

    def __init__(self) -> None:
        super().__init__()
        self._columns: list[str] = []
        self._data: dict[str, np.ndarray] = {}
        self._row_count: int = 0

    def set_data(self, columns: list[str], data: dict[str, np.ndarray]) -> None:
        self.beginResetModel()
        self._columns = columns
        self._data = data
        self._row_count = max((len(v) for v in data.values()), default=0)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return self._row_count

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        col_name = self._columns[index.column()]
        arr = self._data.get(col_name)
        if arr is None or index.row() >= len(arr):
            return None
        val = arr[index.row()]
        if isinstance(val, float):
            return f"{val:.6g}"
        return str(val)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._columns[section] if section < len(self._columns) else None
        return str(section + 1)


class TableVisualization(BaseVisualization):
    """Tabular view of all data fields from tiled runs."""

    viz_name = "table"
    viz_display_name = "Data Table"
    viz_icon = "table"

    def __init__(self, parent: QWidget | None = None) -> None:
        self._table_view: QTableView | None = None
        self._model = _TableModel()
        self._stream: Any | None = None
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._table_view = QTableView()
        self._table_view.setModel(self._model)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setShowGrid(False)
        self._table_view.horizontalHeader().setStretchLastSection(True)
        self._table_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self._table_view)

    @staticmethod
    def can_handle(run: Any) -> int:
        return 40  # Fallback: can show anything

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        if not self._run:
            return []
        names = list(self._run.keys())
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
        except Exception as e:
            logger.error("Cannot access stream '{}': {}", stream_name, e)
            return
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        # Table shows all fields; return ["(all)"] as single option
        return ["(all)"]

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._load_table()

    def refresh(self) -> None:
        self._load_table()

    def _load_table(self) -> None:
        if not self._stream:
            return
        table = self._read_events_table()
        if table is None:
            return
        columns = [c for c in table.columns if not c.startswith("ts_")]
        data = {col: np.asarray(table[col]) for col in columns}
        self._model.set_data(columns, data)

    def _read_events_table(self):
        stream_keys = list(self._stream.keys())
        if "internal" in stream_keys:
            try:
                internal = self._stream["internal"]
                if "events" in internal:
                    return internal["events"].read()
            except Exception as e:
                logger.debug("Could not read events table: {}", e)
        return None
```

- [ ] **Step 5: Commit all four scalar widgets**

```bash
git add src/lucid/visualization/widgets/plot_1d.py \
        src/lucid/visualization/widgets/heatmap_new.py \
        src/lucid/visualization/widgets/scatter_new.py \
        src/lucid/visualization/widgets/table_new.py
git commit -m "Add tiled-backed scalar visualizations (plot, heatmap, scatter, table)"
```

---

### Task 4: Rewrite VisualizationPanel Controller

**Files:**
- Modify: `src/lucid/ui/panels/visualization_panel.py`

Replace the current panel logic with the simplified `open_run(entry)` flow. Strip out buffer wiring, DocumentProcessor, `open_tiled_run`, and the old widget creation path.

- [ ] **Step 1: Rewrite the panel**

Key changes:
- Remove: `set_engine`, `_on_document`, `_processor`, `_buffer`, `_selection_engine`, `_characteristics`
- Remove: `open_tiled_run` (13 params), `_on_characteristics_ready`, `_select_visualization`, `_create_visualization_by_name`
- Remove: `_start_tiled_poll`, `_stop_tiled_poll`, `_poll_tiled` (replaced by `refresh()`)
- Remove: `_stream_fetch`, `_on_stream_changed` background thread (widget reads tiled directly now)
- Add: `open_run(entry)` — score widgets, create winner, drive set_run/set_stream/set_field
- Add: stream combo and field combo driven by widget's `get_streams()`/`get_fields()`
- Add: refresh timer that calls `widget.refresh()` and checks for stop doc

The panel keeps: viz type combo (manual override), theater mode proxy, fit panel, export.

Read the current `visualization_panel.py` in full, then rewrite it with these changes. The new `_setup_ui` should have:
- Stream combo, Field combo, Viz type combo (left to right in toolbar)
- Fit button, Export button (right side)
- QStackedWidget for viz display
- Fit panel in splitter

The new `open_run` method:
```python
def open_run(self, entry: Any) -> None:
    """Open a tiled BlueskyRun for visualization."""
    self._stop_refresh()
    self._entry = entry

    # Score all registered widget classes
    from lucid.visualization.widgets.image_stack import ImageStackVisualization
    from lucid.visualization.widgets.plot_1d import Plot1DVisualization
    from lucid.visualization.widgets.heatmap_new import HeatmapVisualization
    from lucid.visualization.widgets.scatter_new import ScatterVisualization
    from lucid.visualization.widgets.table_new import TableVisualization

    widget_classes = [
        ImageStackVisualization, Plot1DVisualization,
        HeatmapVisualization, ScatterVisualization, TableVisualization,
    ]

    best_cls = None
    best_score = 0
    for cls in widget_classes:
        try:
            score = cls.can_handle(entry)
            if score > best_score:
                best_score = score
                best_cls = cls
        except Exception as e:
            logger.warning("Error in {}.can_handle: {}", cls.viz_name, e)

    if best_cls is None:
        logger.warning("No visualization can handle this run")
        return

    # Create widget
    widget = best_cls(parent=self)
    self._set_current_widget(widget)

    # Drive the selection flow
    widget.set_run(entry)

    # Populate stream combo
    streams = widget.get_streams()
    self._stream_combo.blockSignals(True)
    self._stream_combo.clear()
    self._stream_combo.addItems(streams)
    self._stream_combo.blockSignals(False)
    self._stream_label.setVisible(len(streams) > 1)
    self._stream_combo.setVisible(len(streams) > 1)

    if streams:
        widget.set_stream(streams[0])
        self._populate_field_combo()

    # Start refresh if live
    if entry.metadata.get("stop") is None:
        self._start_refresh()
```

- [ ] **Step 2: Commit**

```bash
git add src/lucid/ui/panels/visualization_panel.py
git commit -m "Rewrite VisualizationPanel for tiled-only open_run(entry) flow"
```

---

### Task 5: Simplify TiledBrowserPanel

**Files:**
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py`

Replace the background thread + `_setup_visualization` + `_on_visualization_ready` chain with a direct call to `viz_panel.open_run(entry)`.

- [ ] **Step 1: Simplify double-click handler**

Replace `_on_table_double_clicked` (and remove `_setup_visualization`, `_on_visualization_ready`, `_on_replay_error`):

```python
@Slot()
def _on_table_double_clicked(self) -> None:
    """Handle table row double-click - open run in Visualization panel."""
    selection = self._table_view.selectionModel().selectedRows()
    if not selection:
        return

    proxy_index = selection[0]
    source_index = self._proxy_model.mapToSource(proxy_index)
    record = self._model.get_record(source_index.row())
    if not record:
        return

    self.record_double_clicked.emit(record)
    logger.info("Opening run {} in visualization", record.uid[:8])

    client = self._tiled_service._client
    if client is None:
        return

    try:
        entry = client[record._client_key]
    except Exception as e:
        logger.error("Failed to access run {}: {}", record.uid[:8], e)
        return

    from lucid.core.services import ServiceRegistry
    from lucid.ui.docking import DockingManager
    from lucid.ui.panels.visualization_panel import VisualizationPanel

    dm = ServiceRegistry.get_instance().get(DockingManager, None)
    if dm is None:
        return

    viz_panel_id = "lucid.panels.visualization"
    dm.show_panel(viz_panel_id)
    panel = dm.get_panel(viz_panel_id)
    if isinstance(panel, VisualizationPanel):
        panel.open_run(entry)
```

Remove: `_setup_visualization` static method, `_on_visualization_ready`, `_on_replay_error`, the `QThreadFuture` import if no longer used.

- [ ] **Step 2: Commit**

```bash
git add src/lucid/ui/panels/tiled_browser_panel.py
git commit -m "Simplify TiledBrowserPanel: direct open_run(entry) on double-click"
```

---

### Task 6: Delete Dead Code

**Files to delete:**
- `src/lucid/visualization/processor.py`
- `src/lucid/plugins/visualization_plugin.py`

**Files to clean up:**
- `src/lucid/visualization/__init__.py` — remove DocumentProcessor, SelectionEngine exports
- `src/lucid/visualization/spec.py` — remove DataCharacteristics, VisualizationSpec; keep FieldType/FieldInfo
- `src/lucid/visualization/selection.py` — delete (selection now in panel's open_run loop)
- `src/lucid/visualization/base.py` — replace content with re-export of BaseVisualization
- `src/lucid/acquire/__init__.py` — remove MultiStreamBuffer export
- `src/lucid/acquire/buffer.py` — remove MultiStreamBuffer class (keep LiveDataBuffer if used elsewhere)
- `src/lucid/plugins/builtin_manifest.py` — update viz plugin registrations to new widget classes

- [ ] **Step 1: Delete obsolete files**

```bash
git rm src/lucid/visualization/processor.py
git rm src/lucid/visualization/selection.py
git rm src/lucid/plugins/visualization_plugin.py
```

- [ ] **Step 2: Update visualization/__init__.py**

Remove exports of `DocumentProcessor`, `SelectionEngine`. Add export of `BaseVisualization`.

- [ ] **Step 3: Clean up spec.py**

Remove `DataCharacteristics` and `VisualizationSpec` classes. Keep `FieldType`, `FieldInfo` as optional helpers.

- [ ] **Step 4: Update base.py**

Replace old `BaseVisualizationWidget` content with:
```python
# Backwards compat re-export
from lucid.visualization.base_visualization import BaseVisualization

__all__ = ["BaseVisualization"]
```

- [ ] **Step 5: Check MultiStreamBuffer usage**

Verify `MultiStreamBuffer` in `acquire/buffer.py` is not used outside visualization. If confirmed, remove the class and its export from `acquire/__init__.py`. Keep `LiveDataBuffer` if it has other consumers.

- [ ] **Step 6: Update builtin_manifest.py registrations**

Update the six visualization plugin entries (lines 287-319) to point to the new widget classes. The registration mechanism may need adjustment since we're registering classes directly instead of plugin instances.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Remove dead code: buffer, processor, selection engine, old plugin interfaces"
```

---

### Task 7: Delete Old Widget Files

**Files to delete:**
- `src/lucid/visualization/widgets/image_sequence.py`
- `src/lucid/visualization/widgets/plot.py`
- `src/lucid/visualization/widgets/heatmap.py`
- `src/lucid/visualization/widgets/scatter.py`
- `src/lucid/visualization/widgets/table.py`
- `src/lucid/visualization/widgets/volume.py`

**Files to rename:**
- `heatmap_new.py` → `heatmap.py`
- `scatter_new.py` → `scatter.py`
- `table_new.py` → `table.py`

- [ ] **Step 1: Verify no remaining imports of old files**

```bash
grep -r "from lucid.visualization.widgets.image_sequence" src/lucid/
grep -r "from lucid.visualization.widgets.plot import" src/lucid/
grep -r "from lucid.visualization.widgets.heatmap import" src/lucid/
grep -r "from lucid.visualization.widgets.scatter import" src/lucid/
grep -r "from lucid.visualization.widgets.table import" src/lucid/
grep -r "from lucid.visualization.widgets.volume import" src/lucid/
```

Fix any remaining imports to point to new files.

- [ ] **Step 2: Delete old files and rename new ones**

```bash
git rm src/lucid/visualization/widgets/image_sequence.py
git rm src/lucid/visualization/widgets/plot.py
git rm src/lucid/visualization/widgets/volume.py
git mv src/lucid/visualization/widgets/heatmap_new.py src/lucid/visualization/widgets/heatmap.py
git mv src/lucid/visualization/widgets/scatter_new.py src/lucid/visualization/widgets/scatter.py
git mv src/lucid/visualization/widgets/table_new.py src/lucid/visualization/widgets/table.py
```

Update all imports in `visualization_panel.py`, `builtin_manifest.py`, and `__init__.py` to match final names.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "Remove old visualization widgets, rename new ones to final paths"
```

---

### Task 8: Smoke Test on Beamline

**No files changed — manual verification.**

- [ ] **Step 1: Deploy to beamline**

Pull latest on the machine running LUCID (tsuru / shussebora). Restart LUCID.

- [ ] **Step 2: Verify count run visualization**

Run a `count` plan. Double-click the run in Data Browser. Confirm:
- Image Stack opens (not Volume Viewer)
- Image displays with percentile-based levels
- Stream combo shows "primary" only
- Field combo shows "PI_MTE3_image" (or equivalent)

- [ ] **Step 3: Verify simple_acquire visualization**

Run a `simple_acquire` with `collect_dark=True`. Confirm:
- Stream combo shows "primary" and "dark"
- Primary stream shows light frame
- Switching to dark stream shows dark frame
- Field combo updates when switching streams

- [ ] **Step 4: Verify live run polling**

Start a multi-frame acquisition. While running, confirm:
- New frames appear in the timeline
- Frame count updates in status label
- Polling stops when run completes

- [ ] **Step 5: Verify scalar visualization**

Run a `scan_1d` plan. Confirm:
- 1D Plot opens automatically
- Line plot shows detector vs motor
- Field combo lists available scalar fields
- Switching field re-plots

---

### Notes

**VolumeVisualization** is intentionally omitted from the port. It was rarely the correct choice (it won at score 80 for ARRAY_3D, which was actually a misclassified image stack). It can be re-added later if 3D volume data is actually collected. The image stack widget handles the [N, H, W] case correctly now.

**Theater mode / Fit panel** wire to the widget instance, not the data path. They should continue working as long as the widget is a QWidget. Verify during smoke test but no code changes expected.

**`_read_events_table()` duplication** across scalar widgets is intentional — each widget is self-contained, no shared base beyond `BaseVisualization`. If this becomes a maintenance burden later, extract a helper, but not now (YAGNI).

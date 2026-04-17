"""Adaptive 2D heatmap visualization (posterior mean/variance/acquisition).

Reads from the ``adaptive`` BlueskyEventStream written by Tsuchinoko's
TiledPublisher.  Per-iteration GP data is stored as zarr arrays (one
per field, indexed by event number).  Evaluation grids are in the
descriptor's ``configuration.tsuchinoko.data``.

Provides an iteration slider, colormap selector, and optional
measurement/target overlays.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lucid.visualization.base_visualization import BaseVisualization

# Fields in priority order — only those actually present are offered.
_HEATMAP_FIELDS = ["posterior_mean", "posterior_variance", "acquisition_function"]

_STALE_POLL_LIMIT = 3
_POLL_INTERVAL_MS = 2000


class AdaptiveHeatmapVisualization(BaseVisualization):
    """2D heatmap of GP posterior arrays from an adaptive experiment.

    Renders posterior_mean, posterior_variance, or acquisition_function
    for a selected iteration, with optional measurement and target overlays.
    """

    viz_name = "adaptive_heatmap"
    viz_display_name = "Adaptive Heatmap"
    viz_icon = "grid"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tiled state
        self._adaptive: Any | None = None
        self._grid_x: np.ndarray | None = None
        self._grid_y: np.ndarray | None = None
        self._grid_shape: list[int] | None = None
        self._n_iterations: int = 0
        self._current_index: int = -1
        self._stale_count: int = 0

        # pyqtgraph items
        self._plot_widget: pg.PlotWidget | None = None
        self._image_item: pg.ImageItem | None = None
        self._meas_scatter: pg.ScatterPlotItem | None = None
        self._target_scatter: pg.ScatterPlotItem | None = None

        # UI controls
        self._slider: QSlider | None = None
        self._iter_label: QLabel | None = None
        self._cmap_combo: QComboBox | None = None
        self._meas_check: QCheckBox | None = None
        self._target_check: QCheckBox | None = None

        # Polling timer
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_tick)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        layout.addLayout(self._build_toolbar())

        self._plot_widget = pg.PlotWidget()
        plot_item = self._plot_widget.getPlotItem()
        if plot_item:
            plot_item.setAspectLocked(True)

        self._image_item = pg.ImageItem()
        self._plot_widget.addItem(self._image_item)
        self._apply_colormap("viridis")

        self._meas_scatter = pg.ScatterPlotItem(
            pen=None, symbol="o", size=6,
            brush=pg.mkBrush(255, 255, 255, 120),
        )
        self._target_scatter = pg.ScatterPlotItem(
            pen=pg.mkPen("r", width=1.5), brush=None, symbol="x", size=10,
        )
        self._plot_widget.addItem(self._meas_scatter)
        self._plot_widget.addItem(self._target_scatter)

        layout.addWidget(self._plot_widget)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Iter:"))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.setEnabled(False)
        self._slider.valueChanged.connect(self._on_slider_changed)
        toolbar.addWidget(self._slider)

        self._iter_label = QLabel("0/0")
        toolbar.addWidget(self._iter_label)

        toolbar.addWidget(QLabel("Cmap:"))
        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems([
            "viridis", "plasma", "inferno", "magma", "cividis",
            "gray", "hot", "cool",
        ])
        self._cmap_combo.currentTextChanged.connect(self._apply_colormap)
        toolbar.addWidget(self._cmap_combo)

        toolbar.addStretch()

        self._meas_check = QCheckBox("Measurements")
        self._meas_check.setChecked(True)
        self._meas_check.toggled.connect(self._on_overlay_toggled)
        toolbar.addWidget(self._meas_check)

        self._target_check = QCheckBox("Targets")
        self._target_check.setChecked(True)
        self._target_check.toggled.connect(self._on_overlay_toggled)
        toolbar.addWidget(self._target_check)

        return toolbar

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Return 90 for a 2D Tsuchinoko adaptive run, 0 otherwise."""
        try:
            adaptive = run["adaptive"]
            if adaptive.metadata.get("adaptive_engine") != "tsuchinoko":
                return 0
            config = adaptive.metadata.get("configuration", {})
            tsuchinoko_config = config.get("tsuchinoko", {}).get("data", {})
            has_x = "evaluation_grid_x" in tsuchinoko_config
            has_y = "evaluation_grid_y" in tsuchinoko_config
            has_z = "evaluation_grid_z" in tsuchinoko_config
            if has_x and has_y and not has_z:
                return 90
        except Exception:
            pass
        return 0

    def set_run(self, run: Any) -> None:
        self._run = run

    def get_streams(self) -> list[str]:
        return ["adaptive"]

    def set_stream(self, stream_name: str) -> None:
        self._stream_name = stream_name
        if self._run is None:
            return

        try:
            self._adaptive = self._run["adaptive"]
        except Exception:
            self._adaptive = None
            return

        # Read evaluation grids from descriptor configuration
        try:
            config = self._adaptive.metadata.get("configuration", {})
            tsuchinoko_data = config.get("tsuchinoko", {}).get("data", {})
            self._grid_x = np.asarray(tsuchinoko_data["evaluation_grid_x"])
            self._grid_y = np.asarray(tsuchinoko_data["evaluation_grid_y"])
        except Exception as exc:
            logger.debug("AdaptiveHeatmap: could not read grid config: {}", exc)
            self._grid_x = self._grid_y = None

        # Get grid shape from data_keys metadata
        try:
            dk = self._adaptive.metadata.get("data_keys", {})
            self._grid_shape = dk.get("posterior_mean", {}).get("grid_shape")
        except Exception:
            self._grid_shape = None

        # Count iterations from posterior_mean array (or internal table)
        self._update_iteration_count()

        # Start polling
        self._stale_count = 0
        self._poll_timer.start()

        # Auto-pick best field
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return the subset of heatmap fields present in the stream."""
        if self._adaptive is None:
            return []
        try:
            available = list(self._adaptive)
        except Exception:
            return []
        return [f for f in _HEATMAP_FIELDS if f in available]

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._load_current_iteration()

    def refresh(self) -> None:
        self._poll_tick()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _update_iteration_count(self) -> None:
        """Update iteration count from the posterior_mean array shape."""
        n = 0
        if self._adaptive is not None:
            try:
                # Each zarr array has shape (N_events, flat_size)
                for field in _HEATMAP_FIELDS:
                    if field in self._adaptive:
                        n = self._adaptive[field].shape[0]
                        break
            except Exception:
                pass
        self._n_iterations = n
        if n > 0:
            self._slider.setMaximum(n - 1)
            self._slider.setEnabled(True)
        else:
            self._slider.setMaximum(0)
            self._slider.setEnabled(False)

    def _poll_tick(self) -> None:
        """Check for new iterations and update display."""
        if self._adaptive is None:
            return

        was_at_end = (
            self._current_index == self._n_iterations - 1
            if self._n_iterations
            else True
        )

        old_count = self._n_iterations
        self._update_iteration_count()

        if self._n_iterations == old_count:
            self._stale_count += 1
            if self._stale_count >= _STALE_POLL_LIMIT:
                self._poll_timer.stop()
                logger.debug("AdaptiveHeatmap: polling stopped (stale)")
            return

        # New data found
        self._stale_count = 0
        new_index = self._n_iterations - 1 if was_at_end else self._current_index
        self._slider.setValue(new_index)

    # ------------------------------------------------------------------
    # Iteration loading / rendering
    # ------------------------------------------------------------------

    def _on_slider_changed(self, index: int) -> None:
        if index < 0 or index >= self._n_iterations:
            return
        self._current_index = index
        self._iter_label.setText(f"{index + 1}/{self._n_iterations}")
        self._load_current_iteration()

    def _load_current_iteration(self) -> None:
        if (
            self._adaptive is None
            or self._n_iterations == 0
            or self._current_index < 0
            or not self._field_name
        ):
            return

        idx = self._current_index

        # Render heatmap — read one slice from the zarr array
        try:
            if self._field_name in self._adaptive:
                flat = np.asarray(self._adaptive[self._field_name][idx])
                if self._grid_shape:
                    data = flat.reshape(self._grid_shape)
                else:
                    # Guess square grid
                    side = int(np.sqrt(len(flat)))
                    data = flat.reshape(side, side) if side * side == len(flat) else flat
                self._update_image(data)
        except Exception as exc:
            logger.debug("AdaptiveHeatmap: could not load {}: {}", self._field_name, exc)

        # Target overlay
        try:
            if "targets" in self._adaptive:
                targets = np.asarray(self._adaptive["targets"][idx])
                if self._target_check.isChecked() and len(targets) > 0:
                    self._update_target_overlay(targets)
                else:
                    self._target_scatter.clear()
            else:
                self._target_scatter.clear()
        except Exception:
            self._target_scatter.clear()

        # Measurement overlay
        self._update_measurement_overlay()

    def _update_image(self, data: np.ndarray) -> None:
        if self._image_item is None or self._grid_x is None or self._grid_y is None:
            return
        x0, x1 = float(self._grid_x[0]), float(self._grid_x[-1])
        y0, y1 = float(self._grid_y[0]), float(self._grid_y[-1])
        from PySide6.QtCore import QRectF

        self._image_item.setImage(data.T)
        self._image_item.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))

    def _update_target_overlay(self, targets: np.ndarray) -> None:
        if self._target_scatter is None:
            return
        if targets.ndim == 2 and targets.shape[1] >= 2:
            self._target_scatter.setData(x=targets[:, 0], y=targets[:, 1])
        elif targets.ndim == 1 and len(targets) >= 2:
            # Flat target pair for single target
            self._target_scatter.setData(x=[targets[0]], y=[targets[1]])
        else:
            self._target_scatter.clear()

    def _update_measurement_overlay(self) -> None:
        """Draw measured points from primary stream."""
        if self._meas_scatter is None or not self._meas_check.isChecked():
            if self._meas_scatter is not None:
                self._meas_scatter.clear()
            return

        if self._run is None:
            return

        # Resolve X/Y field names from start.hints.dimensions
        try:
            start = self._run.metadata.get("start", {})
            dims = start.get("hints", {}).get("dimensions", [])
            x_field = dims[0][0][0]  # first dim -> first field
            y_field = dims[1][0][0]  # second dim -> first field
        except (IndexError, KeyError, TypeError):
            self._meas_scatter.clear()
            return

        # Read from primary stream
        try:
            primary = self._run["primary"]
            x_data = np.asarray(primary[x_field].read())
            y_data = np.asarray(primary[y_field].read())
            if len(x_data) > 0:
                self._meas_scatter.setData(x=x_data, y=y_data)
            else:
                self._meas_scatter.clear()
        except Exception:
            self._meas_scatter.clear()

    # ------------------------------------------------------------------
    # Overlay / colormap callbacks
    # ------------------------------------------------------------------

    def _on_overlay_toggled(self, _checked: bool) -> None:
        self._load_current_iteration()

    def _apply_colormap(self, cmap_name: str) -> None:
        try:
            cmap = pg.colormap.get(cmap_name)
            if cmap and self._image_item:
                self._image_item.setLookupTable(cmap.getLookupTable(nPts=256))
        except Exception as exc:
            logger.debug("AdaptiveHeatmap: colormap '{}' failed: {}", cmap_name, exc)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        super().closeEvent(event)

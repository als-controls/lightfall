"""Reusable toolbar-button mixins for ImageView-based visualizations.

Provides Reset LUT, Reset Axes, Log Intensity, and ROI controls that
can be composed into any visualization backed by a :class:`LazyImageView`.
"""

from __future__ import annotations

import warnings

import numpy as np
import pyqtgraph as pg
import qtawesome as qta
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton

from lightfall.utils.threads import QThreadFuture
from lightfall.utils.tiled_helpers import fetch_subcube
from lightfall.visualization.reductions import REDUCTION_OPERATORS, REDUCTIONS_BY_NAME


class ImageViewToolbarMixin:
    """Mixin providing Reset LUT, Reset Axes, Log Intensity, and ROI controls.

    Host class requirements:

    * ``self._image_view`` — a :class:`LazyImageView` (or ``pg.ImageView``
      with ``set_log_mode``, ``fetch_frame``, and ``frame_count``).
    * ``self._frame_shape`` — ``(H, W)`` tuple, set before ROI is enabled.

    Call :meth:`_init_image_view_toolbar_state` from ``__init__`` and
    :meth:`_build_image_view_buttons` from your toolbar builder.
    """

    # ---- Initialisation --------------------------------------------------

    def _init_image_view_toolbar_state(self) -> None:
        """Initialise mixin state.  Call from ``__init__``."""
        self._log_mode: bool = False
        self._roi: pg.RectROI | None = None
        self._roi_curves: list[pg.PlotDataItem] = []
        self._roi_variation_gen: int = 0

    # ---- Toolbar construction --------------------------------------------

    def _build_image_view_buttons(self, toolbar: QHBoxLayout) -> None:
        """Append Reset LUT, Reset Axes, Log Intensity, and ROI widgets."""
        # Reset LUT
        self._reset_lut_btn = QPushButton(
            qta.icon("mdi6.chart-histogram"), "Reset LUT",
        )
        self._reset_lut_btn.setFixedHeight(24)
        self._reset_lut_btn.clicked.connect(self._on_reset_lut)
        toolbar.addWidget(self._reset_lut_btn)

        # Reset Axes
        self._reset_axes_btn = QPushButton(
            qta.icon("mdi6.magnify"), "Reset Axes",
        )
        self._reset_axes_btn.setFixedHeight(24)
        self._reset_axes_btn.clicked.connect(self._on_reset_axes)
        toolbar.addWidget(self._reset_axes_btn)

        # Log Intensity (toggle)
        self._log_icon_off = qta.icon("mdi6.lightbulb")
        self._log_icon_on = qta.icon("mdi6.lightbulb-on-outline")
        self._log_intensity_btn = QPushButton(self._log_icon_off, "Log Intensity")
        self._log_intensity_btn.setFixedHeight(24)
        self._log_intensity_btn.setCheckable(True)
        self._log_intensity_btn.toggled.connect(self._on_log_intensity_toggled)
        toolbar.addWidget(self._log_intensity_btn)

        # ROI toggle
        self._roi_btn = QPushButton("ROI")
        self._roi_btn.setCheckable(True)
        self._roi_btn.setToolTip("Enable region of interest")
        self._roi_btn.toggled.connect(self._on_roi_toggled)
        toolbar.addWidget(self._roi_btn)

        # ROI statistic selector (hidden until ROI is enabled)
        self._roi_stat_combo = QComboBox()
        _basic = ["Mean", "Sum", "Max", "Min", "Std"]
        _variation = [
            op.name for op in REDUCTION_OPERATORS
            if op.per_frame is not None and op.name not in _basic
        ]
        self._roi_stat_combo.addItems(_basic + _variation)
        self._roi_stat_combo.setToolTip("Statistic to plot over the ROI")
        self._roi_stat_combo.currentTextChanged.connect(self._on_roi_stat_changed)
        self._roi_stat_combo.hide()
        toolbar.addWidget(self._roi_stat_combo)

    # ---- Button callbacks ------------------------------------------------

    def _on_reset_lut(self) -> None:
        """Auto-levels using 1st/99th percentile of the current frame.

        Prefers the real-unit frame cached by :class:`LazyImageView` so
        that percentiles are correct when log intensity mode is active.
        """
        if self._image_view is None:
            return
        # Prefer real-unit data (correct in log mode)
        real = getattr(self._image_view, "_last_real_frame", None)
        img = real if real is not None else self._image_view.imageItem.image
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
                lo = float(np.nanmin(data))
                hi = float(np.nanmax(data))
            if lo == hi:
                hi = lo + 1.0
        self._image_view.setLevels(lo, hi)

    def _on_reset_axes(self) -> None:
        """Auto-range the view to fit the image bounds."""
        if self._image_view:
            self._image_view.getView().autoRange()

    def _on_log_intensity_toggled(self, checked: bool) -> None:
        self._log_mode = checked
        self._log_intensity_btn.setIcon(
            self._log_icon_on if checked else self._log_icon_off,
        )
        if self._image_view:
            self._image_view.set_log_mode(checked)

    def _on_roi_toggled(self, enabled: bool) -> None:
        self._roi_stat_combo.setVisible(enabled)
        if enabled:
            self._create_roi()
            if self._roi:
                self._roi.show()
            self._update_roi_plot()
        else:
            if self._roi:
                self._roi.hide()
            self._clear_roi_curves()

    def _on_roi_stat_changed(self, _stat: str) -> None:
        if self._roi_btn.isChecked():
            self._update_roi_plot()

    # ---- ROI helpers -----------------------------------------------------

    def _create_roi(self) -> None:
        if self._roi is not None:
            return
        if not self._frame_shape or len(self._frame_shape) < 2:
            return

        height, width = self._frame_shape
        roi_w, roi_h = width // 2, height // 2
        roi_x, roi_y = width // 4, height // 4

        self._roi = pg.RectROI(
            [roi_x, roi_y], [roi_w, roi_h],
            pen=pg.mkPen("r", width=2),
        )
        self._roi.addScaleHandle([1, 1], [0, 0])
        self._roi.addScaleHandle([0, 0], [1, 1])
        self._roi.addScaleHandle([1, 0], [0, 1])
        self._roi.addScaleHandle([0, 1], [1, 0])

        self._image_view.addItem(self._roi)
        self._roi.sigRegionChanged.connect(self._update_roi_plot)

    def _clear_roi_curves(self) -> None:
        for curve in self._roi_curves:
            self._image_view.ui.roiPlot.removeItem(curve)
        self._roi_curves.clear()

    def _update_roi_plot(self) -> None:
        """Compute the selected ROI statistic over all frames and plot.

        Subsamples up to 200 frames to stay responsive on long runs.
        """
        if not self._roi or self._image_view is None:
            return
        if not self._frame_shape or len(self._frame_shape) < 2:
            return

        img_h, img_w = self._frame_shape
        n_frames = self._image_view.frame_count
        if n_frames == 0:
            return

        # Clamp ROI bounds to image extent
        pos = self._roi.pos()
        size = self._roi.size()
        x0 = max(0, int(pos.x()))
        y0 = max(0, int(pos.y()))
        x1 = min(img_w, int(pos.x() + size.x()))
        y1 = min(img_h, int(pos.y() + size.y()))
        if x1 <= x0 or y1 <= y0:
            self._clear_roi_curves()
            return

        stat_name = self._roi_stat_combo.currentText()
        op = REDUCTIONS_BY_NAME.get(stat_name)

        # Variation operators need consecutive frames -> fetch the ROI sub-cube
        # across all frames in the background and apply operator.per_frame.
        if op is not None and op.per_frame is not None and stat_name not in (
            "Mean", "Sum", "Max", "Min", "Std",
        ):
            client = getattr(self._image_view, "_client", None)
            if client is None:
                return

            self._roi_variation_gen += 1
            _gen = self._roi_variation_gen

            def compute() -> tuple:
                import numpy as _np
                cube = fetch_subcube(client, (None, (y0, y1), (x0, x1)))
                cube = _np.asarray(cube, dtype=_np.float64)
                series = op.per_frame(cube)
                tvals = getattr(self._image_view, "tVals", None)
                if tvals is not None and len(tvals):
                    xs = _np.asarray(tvals)
                else:
                    xs = _np.arange(len(series), dtype=float)
                return xs[: len(series)], series

            def on_result(result: tuple) -> None:
                if _gen != self._roi_variation_gen:
                    return  # a newer ROI/operator change superseded this result
                xs, series = result
                self._clear_roi_curves()
                mask = np.isfinite(series)
                if mask.any():
                    curve = self._image_view.ui.roiPlot.plot(
                        x=xs[mask], y=series[mask],
                        pen=pg.mkPen("c", width=2), name=f"ROI {stat_name}",
                    )
                    self._roi_curves.append(curve)

            QThreadFuture(compute, callback_slot=on_result, register=False).start()
            return

        # Basic stats: fast per-frame subsample path
        stat_func = {
            "Mean": np.mean, "Sum": np.sum, "Max": np.max,
            "Min": np.min, "Std": np.std,
        }.get(stat_name, np.mean)

        max_roi_frames = 200
        if n_frames > max_roi_frames:
            indices = np.linspace(0, n_frames - 1, max_roi_frames, dtype=int)
        else:
            indices = np.arange(n_frames)

        roi_values = np.empty(len(indices))
        for i, idx in enumerate(indices):
            frame = self._image_view.fetch_frame(int(idx))
            roi_values[i] = stat_func(frame[y0:y1, x0:x1])

        tvals = getattr(self._image_view, "tVals", None)
        if tvals is not None and len(tvals) > 0:
            time_vals = np.asarray(tvals)[indices]
        else:
            time_vals = indices.astype(float)

        self._clear_roi_curves()
        n_points = min(len(roi_values), len(time_vals))
        if n_points > 0:
            curve = self._image_view.ui.roiPlot.plot(
                x=time_vals[:n_points],
                y=roi_values[:n_points],
                pen=pg.mkPen("c", width=2),
                name=f"ROI {stat_name}",
            )
            self._roi_curves.append(curve)

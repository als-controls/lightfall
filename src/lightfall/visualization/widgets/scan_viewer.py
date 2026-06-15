"""Scan Viewer visualization.

For scans that measure an image series at each scan point. A left map shows a
per-point scalar reduction of each point's image sub-cube within an ROI; a
right image viewer shows the selected point's frames with frame scrolling and
the ROI that defines the reduction region.

Handles two cube layouts:
  * 4-D ``(n_points, n_frames, H, W)`` — file-per-acquisition writing.
  * 3-D ``(n_points * frame_per_point, H, W)`` — single long file; point
    ``p`` occupies rows ``[p*fpp : (p+1)*fpp]``. ``fpp`` comes from
    ``frame_per_point`` metadata on the array client.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from loguru import logger
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lightfall.utils.tiled_helpers import fetch_subcube, read_events
from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.reduction_engine import ReductionEngine
from lightfall.visualization.reductions import REDUCTIONS_BY_NAME, operators_for_frame_count
from lightfall.visualization.scan_geometry import ScanGeometry, parse_scan_geometry
from lightfall.visualization.widgets.lazy_image_view import LazyImageView


class ScanViewerVisualization(BaseVisualization):
    """Two-panel viewer for image-series-per-point scans."""

    viz_name = "scan_viewer"
    viz_display_name = "Scan Viewer"
    viz_icon = "grid"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stream: Any | None = None
        self._data_keys: dict[str, Any] = {}
        self._image_client: Any | None = None
        self._geometry: ScanGeometry = ScanGeometry()
        self._n_points: int = 0
        self._n_frames: int = 0
        self._frame_shape: tuple[int, ...] = ()
        self._layout: str = "empty"   # one of: "empty", "3d", "4d", "other"

        self._engine = ReductionEngine(self)
        self._engine.pointComputed.connect(self._on_point_computed)
        self._engine.finished.connect(self._on_engine_finished)
        self._engine.progress.connect(self._on_engine_progress)
        self._point_values: np.ndarray = np.empty(0)
        self._selected_point: int = 0
        self._roi: pg.RectROI | None = None
        self._roi_debounce = QTimer(self)
        self._roi_debounce.setSingleShot(True)
        self._roi_debounce.setInterval(250)
        self._roi_debounce.timeout.connect(self._restart_engine)

        self._map_repaint_timer = QTimer(self)
        self._map_repaint_timer.setSingleShot(True)
        self._map_repaint_timer.setInterval(66)  # ~15 Hz
        self._map_repaint_timer.timeout.connect(self._redraw_map)

        self._cached_motor_positions: tuple[np.ndarray, np.ndarray] | None = None

        self._build_ui()

    # ---- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: operator combo + independent-axis map
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Reduction:"))
        self._op_combo = QComboBox()
        self._op_combo.currentTextChanged.connect(self._on_operator_changed)
        bar.addWidget(self._op_combo)
        self._progress_label = QLabel("")
        bar.addWidget(self._progress_label)
        bar.addStretch()
        left_layout.addLayout(bar)

        self._map_widget = pg.PlotWidget()
        self._map_image = pg.ImageItem()
        self._map_scatter = pg.ScatterPlotItem(size=10, pen=pg.mkPen(None))
        self._map_widget.addItem(self._map_image)
        self._map_widget.addItem(self._map_scatter)
        self._map_widget.scene().sigMouseClicked.connect(self._on_map_clicked)
        left_layout.addWidget(self._map_widget)
        splitter.addWidget(left)

        # Right: lazy image view with ROI
        self._image_view = LazyImageView()
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()
        try:
            self._image_view.setColorMap(pg.colormap.get("viridis"))
        except Exception:
            pass
        splitter.addWidget(self._image_view)

        splitter.setSizes([400, 500])
        layout.addWidget(splitter)

    # ---- BaseVisualization interface ------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Score 90 for scans with a >= 2-D per-point field; 0 otherwise."""
        try:
            start = run.metadata.get("start", {}) or {}
            dims = start.get("hints", {}).get("dimensions", []) or []
        except Exception:
            return 0
        if not dims or not (1 <= len(dims) <= 2):
            return 0
        try:
            data_keys = run["primary"].metadata.get("data_keys", {})
        except Exception:
            return 0
        has_image = any(len(dk.get("shape", [])) >= 2 for dk in data_keys.values())
        return 90 if has_image else 0

    def set_run(self, run: Any) -> None:
        self._run = run
        self._geometry = parse_scan_geometry(run)

    def get_streams(self) -> list[str]:
        if self._run is None:
            return []
        names = list(self._run.keys())
        if "primary" in names:
            names.remove("primary")
            names.insert(0, "primary")
        return names

    def set_stream(self, stream_name: str) -> None:
        self._cached_motor_positions = None
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
            self._data_keys = self._stream.metadata.get("data_keys", {})
        except Exception as e:
            logger.debug("ScanViewer: could not open stream '{}': {}", stream_name, e)
            self._stream = None
            self._data_keys = {}
        fields = self.get_fields()
        if fields:
            self.set_field(fields[0])

    def get_fields(self) -> list[str]:
        """Return >= 2-D (image) fields, hinted first."""
        if not self._data_keys:
            return []
        try:
            hinted = set(self._stream.metadata.get("hints", {}).get("fields", []))
        except Exception:
            hinted = set()
        hinted_imgs: list[str] = []
        other_imgs: list[str] = []
        for name, dk in self._data_keys.items():
            if len(dk.get("shape", [])) >= 2:
                (hinted_imgs if name in hinted else other_imgs).append(name)
        return hinted_imgs + other_imgs

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        client = self._resolve_client(field_name)
        if client is None:
            return
        self._image_client = client
        self._detect_layout(field_name, client)
        self._render()

    def refresh(self) -> None:
        """Poll for new scan points (live runs)."""
        self._render()

    # ---- helpers ---------------------------------------------------------

    def _resolve_client(self, field_name: str) -> Any | None:
        try:
            return self._stream[field_name]
        except Exception:
            pass
        try:
            return self._stream["external"][field_name]
        except Exception:
            logger.warning("ScanViewer: could not resolve ArrayClient for '{}'", field_name)
            return None

    def _detect_layout(self, field_name: str, client: Any) -> None:
        """Determine layout, n_points, n_frames, frame_shape from the array.

        Two servable forms are supported:
          * 4-D ``(n_points, n_frames, H, W)`` — file-per-acquisition writing.
          * 3-D ``(n_points * frame_per_point, H, W)`` — single long file; point
            ``p`` occupies rows ``[p*fpp : (p+1)*fpp]``. ``fpp`` comes from
            ``frame_per_point`` metadata (see :meth:`_read_frame_per_point`).
        """
        full = tuple(client.shape)
        dk_shape = self._data_keys.get(field_name, {}).get("shape", [])
        frame_shape = tuple(dk_shape[-2:]) if len(dk_shape) >= 2 else tuple(full[-2:])
        fpp = self._read_frame_per_point(field_name, client, dk_shape)
        if not full:
            self._layout = "empty"
            self._n_points, self._n_frames, self._frame_shape = 0, 1, ()
            logger.debug("ScanViewer layout: empty shape for field '{}'", field_name)
            return
        if len(full) >= 4:
            self._layout = "4d"
            self._n_points, self._n_frames = full[0], full[1]
            self._frame_shape = frame_shape
        elif len(full) == 3:
            self._layout = "3d"
            n_total = full[0]
            self._n_frames = max(1, fpp)
            self._n_points = (n_total // self._n_frames) if n_total else 0
            self._frame_shape = frame_shape
        else:
            self._layout = "other"
            self._n_points, self._n_frames, self._frame_shape = full[0], 1, ()
        logger.debug(
            "ScanViewer layout={} n_points={} n_frames={} frame_shape={}",
            self._layout, self._n_points, self._n_frames, self._frame_shape,
        )

    def _read_frame_per_point(
        self, field_name: str, client: Any, dk_shape: Any
    ) -> int:
        """Frames recorded per scan point.

        Prefers ``client.metadata['frame_per_point']``; falls back to the leading
        dimension of the descriptor ``data_keys`` shape, then to
        ``n_total / start.num_points``, then 1.
        """
        try:
            fpp = client.metadata.get("frame_per_point")
            if fpp:
                return int(fpp)
        except Exception:
            pass
        if len(dk_shape) >= 3:
            return int(dk_shape[0])
        try:
            n_total = int(client.shape[0])
            num_points = int(self._run.metadata.get("start", {}).get("num_points", 0))
            if num_points and n_total and n_total % num_points == 0:
                return n_total // num_points
        except Exception:
            pass
        return 1

    def _render(self) -> None:
        """(Re)build operator list, ROI, right panel, and kick off the engine."""
        if self._image_client is None or not self._frame_shape:
            return

        # Operator combo for the detected frame count (preserve current choice)
        names = [op.name for op in operators_for_frame_count(self._n_frames)]
        current_items = [self._op_combo.itemText(i) for i in range(self._op_combo.count())]
        if names != current_items:
            prev = self._op_combo.currentText()
            self._op_combo.blockSignals(True)
            self._op_combo.clear()
            self._op_combo.addItems(names)
            if prev in names:
                self._op_combo.setCurrentText(prev)
            self._op_combo.blockSignals(False)

        # Per-point value buffer
        self._point_values = np.full(self._n_points, np.nan, dtype=np.float64)

        # Right panel: keep a valid selection, load its frames, ensure ROI
        if not (0 <= self._selected_point < self._n_points):
            self._selected_point = 0
        self._load_point_frames(self._selected_point)
        self._ensure_roi()

        self._restart_engine()

    # ---- ROI -------------------------------------------------------------

    def _ensure_roi(self) -> None:
        if self._roi is not None or len(self._frame_shape) < 2:
            return
        h, w = self._frame_shape
        self._roi = pg.RectROI([w // 4, h // 4], [w // 2, h // 2], pen=pg.mkPen("r", width=2))
        self._roi.addScaleHandle([1, 1], [0, 0])
        self._roi.addScaleHandle([0, 0], [1, 1])
        self._image_view.addItem(self._roi)
        # connect AFTER addItem so the construction-time region change isn't caught
        self._roi.sigRegionChanged.connect(self._roi_debounce.start)

    def _roi_bounds(self) -> tuple[int, int, int, int]:
        """Clamped (y0, y1, x0, x1); full frame if no ROI or degenerate."""
        if not self._frame_shape or len(self._frame_shape) < 2:
            return (0, 0, 0, 0)
        h, w = self._frame_shape
        if self._roi is None:
            return (0, h, 0, w)
        pos, size = self._roi.pos(), self._roi.size()
        x0 = max(0, int(pos.x()))
        y0 = max(0, int(pos.y()))
        x1 = min(w, int(pos.x() + size.x()))
        y1 = min(h, int(pos.y() + size.y()))
        if x1 <= x0 or y1 <= y0:
            return (0, h, 0, w)
        return (y0, y1, x0, x1)

    # ---- Reduction engine ------------------------------------------------

    def _fetch_point_subcube(self, p: int) -> np.ndarray:
        """Server-slice point ``p``'s ROI sub-cube as ``(n_frames, h, w)``."""
        y0, y1, x0, x1 = self._roi_bounds()
        if self._layout == "4d":
            sub = fetch_subcube(self._image_client, (p, None, (y0, y1), (x0, x1)))
        else:  # "3d": rows [p*fpp : (p+1)*fpp]
            fpp = self._n_frames
            sub = fetch_subcube(
                self._image_client, ((p * fpp, (p + 1) * fpp), (y0, y1), (x0, x1))
            )
        return np.asarray(sub, dtype=np.float64)

    def _restart_engine(self) -> None:
        if self._image_client is None or self._n_points <= 0:
            return
        op = REDUCTIONS_BY_NAME.get(self._op_combo.currentText())
        if op is None:
            return
        self._point_values = np.full(self._n_points, np.nan, dtype=np.float64)
        self._progress_label.setText(f"0/{self._n_points}")
        self._engine.start(self._n_points, self._fetch_point_subcube, op)

    def _on_operator_changed(self, _name: str) -> None:
        self._restart_engine()

    def _on_point_computed(self, p: int, value: float) -> None:
        if 0 <= p < len(self._point_values):
            self._point_values[p] = value
        if not self._map_repaint_timer.isActive():
            self._map_repaint_timer.start()

    def _on_engine_progress(self, done: int, total: int) -> None:
        self._progress_label.setText(f"{done}/{total}")

    def _on_engine_finished(self) -> None:
        self._map_repaint_timer.stop()
        self._redraw_map()

    # ---- Left map drawing & selection ------------------------------------

    def _redraw_map(self) -> None:
        if self._geometry.is_rectilinear and len(self._geometry.grid_shape) >= 2:
            ny, nx = self._geometry.grid_shape[0], self._geometry.grid_shape[1]
            grid = np.full((ny, nx), np.nan, dtype=np.float64)
            n = min(self._n_points, ny * nx)
            for i in range(n):
                grid[i // nx, i % nx] = self._point_values[i]
            self._map_scatter.setVisible(False)
            self._map_image.setVisible(True)
            finite = self._point_values[np.isfinite(self._point_values)]
            if finite.size >= 2 and float(finite.min()) < float(finite.max()):
                self._map_image.setImage(
                    grid.T, autoLevels=False,
                    levels=(float(finite.min()), float(finite.max())),
                )
            else:
                self._map_image.setImage(grid.T, autoLevels=False)
        else:
            self._map_image.setVisible(False)
            self._map_scatter.setVisible(True)
            self._draw_scatter_map()

    def _draw_scatter_map(self) -> None:
        if len(self._geometry.motors) < 2:
            return
        if self._cached_motor_positions is None:
            events = read_events(self._stream)
            if events is None:
                return
            try:
                xs = np.asarray(events[self._geometry.motors[0]], dtype=np.float64)
                ys = np.asarray(events[self._geometry.motors[1]], dtype=np.float64)
            except Exception:
                return
            self._cached_motor_positions = (xs, ys)
        xs, ys = self._cached_motor_positions
        n = min(len(xs), len(ys), len(self._point_values))
        z = self._point_values[:n]
        finite = z[np.isfinite(z)]
        zmin, zmax = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        norm = (z - zmin) / (zmax - zmin) if zmax > zmin else np.zeros_like(z)
        try:
            cmap = pg.colormap.get("viridis")
            brushes = [
                pg.mkBrush(cmap.map(float(v) if np.isfinite(v) else 0.0, mode="qcolor"))
                for v in norm
            ]
        except Exception:
            brushes = [pg.mkBrush(100, 100, 255, 200)] * n
        self._map_scatter.setData(x=xs[:n], y=ys[:n], brush=brushes)

    def _on_map_clicked(self, ev: Any) -> None:
        vb = self._map_widget.getPlotItem().getViewBox()
        pt = vb.mapSceneToView(ev.scenePos())
        if self._geometry.is_rectilinear and len(self._geometry.grid_shape) >= 2:
            ny, nx = self._geometry.grid_shape[0], self._geometry.grid_shape[1]
            ix, iy = int(pt.y()), int(pt.x())  # map image is displayed transposed
            if 0 <= ix < ny and 0 <= iy < nx:
                self.select_point(ix * nx + iy)
        else:
            data = self._map_scatter.getData()
            if data[0] is None or len(data[0]) == 0:
                return
            d = (np.asarray(data[0]) - pt.x()) ** 2 + (np.asarray(data[1]) - pt.y()) ** 2
            self.select_point(int(np.argmin(d)))

    def select_point(self, p: int) -> None:
        """Load point ``p``'s frames into the right viewer."""
        if not (0 <= p < self._n_points):
            return
        self._selected_point = p
        self._load_point_frames(p)

    def _load_point_frames(self, p: int) -> None:
        if self._image_client is None or not self._frame_shape:
            return
        n_frames = max(1, self._n_frames)
        timestamps = np.arange(n_frames, dtype=np.float64)

        def fetch(frame: int, _p: int = p) -> np.ndarray:
            if self._layout == "4d":
                return fetch_subcube(self._image_client, (_p, int(frame), None, None))
            return fetch_subcube(
                self._image_client, (_p * self._n_frames + int(frame), None, None)
            )

        self._image_view.setArraySource(
            self._image_client, timestamps, tuple(self._frame_shape), fetch_func=fetch
        )
        if n_frames > 0:
            self._image_view.setCurrentIndex(n_frames - 1)

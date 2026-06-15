"""Scan Viewer visualization.

For scans that measure an image series at each scan point. A left map shows a
per-point scalar reduction of each point's image sub-cube within an ROI; a
right image viewer shows the selected point's frames with frame scrolling and
the ROI that defines the reduction region.

Handles two cube layouts:
  * 4-D ``(n_points, n_frames, H, W)`` — multiple frames per point.
  * 3-D ``(n_points, H, W)`` — one frame per point (frame scrubber hidden,
    frame-difference operators disabled).
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from PySide6.QtWidgets import QWidget

from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.scan_geometry import ScanGeometry, parse_scan_geometry


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
        self._build_ui()

    # ---- UI (fleshed out in Task 6) -------------------------------------
    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QVBoxLayout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

    # ---- BaseVisualization interface ------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Score 90 for scans with a >= 2-D per-point field; 0 otherwise."""
        try:
            start = run.metadata.get("start", {}) or {}
            dims = start.get("hints", {}).get("dimensions", []) or []
            data_keys = run["primary"].metadata.get("data_keys", {})
        except Exception:
            return 0
        if not dims or not (1 <= len(dims) <= 2):
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
        self._stream_name = stream_name
        try:
            self._stream = self._run[stream_name]
            self._data_keys = self._stream.metadata.get("data_keys", {})
        except Exception as e:
            logger.debug("ScanViewer: could not open stream '{}': {}", stream_name, e)
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
        self._render()  # implemented in Task 6

    def refresh(self) -> None:
        """Poll for new scan points (live runs). Wired in Task 6."""
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
        """Determine n_points / n_frames / frame_shape from the array shape."""
        full = tuple(client.shape)
        dk_shape = self._data_keys.get(field_name, {}).get("shape", [])
        if len(full) >= 4:
            self._n_points, self._n_frames = full[0], full[1]
            self._frame_shape = tuple(dk_shape[-2:]) if len(dk_shape) >= 2 else full[-2:]
        elif len(full) == 3:
            self._n_points, self._n_frames = full[0], 1
            self._frame_shape = tuple(dk_shape[-2:]) if len(dk_shape) >= 2 else full[-2:]
        else:
            self._n_points, self._n_frames, self._frame_shape = full[0], 1, ()
        logger.debug(
            "ScanViewer layout: n_points={} n_frames={} frame_shape={}",
            self._n_points, self._n_frames, self._frame_shape,
        )

    def _render(self) -> None:  # replaced in Task 6
        pass

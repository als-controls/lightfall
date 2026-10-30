"""Abstract base class for tiled-only visualization widgets.

BaseVisualization defines the interface for all visualization widgets
that read from a tiled BlueskyRun entry. The controller (VisualizationPanel)
orchestrates the selection flow; subclasses handle display logic.
"""

from __future__ import annotations

import io
import json
from abc import abstractmethod
from typing import Any

import numpy as np
from PySide6.QtWidgets import QWidget


class BaseVisualization(QWidget):
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

    # ------------------------------------------------------------------
    # Export (used by VisualizationPanel's "Export..." button)
    # ------------------------------------------------------------------

    def get_supported_export_formats(self) -> list[str]:
        """File formats ``export_data`` can produce (first is the default)."""
        return ["csv", "json"]

    def export_columns(self) -> dict[str, np.ndarray]:
        """Columns to export, in order. All columns must be the same length.

        Default: every per-event scalar column of the current stream
        (array-valued columns such as detector images are skipped).
        Subclasses override this to export what they display.
        """
        if self._run is None or not self._stream_name:
            raise ValueError("No run is open")
        from lightfall.utils.tiled_helpers import read_events, stream_data_keys

        stream = self._run[self._stream_name]
        try:
            data_keys = stream_data_keys(stream)
        except Exception:
            data_keys = {}
        scalar_names = [
            name for name, dk in data_keys.items()
            if list(dk.get("shape") or []) == []
        ]
        # Request only scalar columns so detector images are not downloaded.
        events = read_events(stream, variables=scalar_names or None)
        if events is None:
            raise ValueError(f"Could not read stream '{self._stream_name}'")
        variables = getattr(events, "data_vars", events)
        columns: dict[str, np.ndarray] = {}
        for name in variables:
            if getattr(events[name], "ndim", 1) != 1:
                continue
            arr = np.asarray(events[name])
            if arr.ndim == 1:
                columns[str(name)] = arr
        if not columns:
            raise ValueError(f"Stream '{self._stream_name}' has no scalar columns")
        return columns

    def export_data(self, fmt: str) -> bytes:
        """Serialise ``export_columns()`` as ``fmt`` ("csv" or "json")."""
        columns = self.export_columns()
        n = min((len(c) for c in columns.values()), default=0)
        columns = {k: v[:n] for k, v in columns.items()}
        fmt = fmt.lower()
        if fmt == "csv":
            import pandas as pd

            buf = io.StringIO()
            pd.DataFrame(columns).to_csv(buf, index=False)
            return buf.getvalue().encode("utf-8")
        if fmt == "json":
            payload = {k: v.tolist() for k, v in columns.items()}
            return json.dumps(payload, indent=2, default=str).encode("utf-8")
        raise ValueError(f"Unsupported export format: {fmt}")

    @staticmethod
    @abstractmethod
    def can_handle(run: Any) -> int:
        """Score 0-100 for how well this viz handles the given run."""

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

    def on_stream_update(self, update: Any) -> None:
        """Apply a Tiled streaming push. Default: re-read via refresh().

        The VisualizationPanel calls this on the GUI thread for each Tiled
        stream push affecting this viz's data. Subclasses may override to
        apply the inline payload directly (avoiding a full re-read).
        """
        self.refresh()

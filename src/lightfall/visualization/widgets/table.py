"""Data table visualization on the new BaseVisualization ABC.

Reads the internal/events table from a tiled BlueskyRun stream and
displays all scalar columns in a scrollable QTableView.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from lightfall.visualization.base_visualization import BaseVisualization


class TableVisualization(BaseVisualization):
    """Tiled-backed data table.

    Displays every column from the stream's internal/events table
    (except ts_* timestamp columns) in an alternating-row QTableView.
    Always returns can_handle=40 as a universal fallback visualization.
    """

    viz_name = "table"
    viz_display_name = "Data Table"
    viz_icon = "table"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tiled state
        self._stream: Any | None = None
        self._data_keys: dict[str, Any] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # Inner model
    # ------------------------------------------------------------------

    class _TableModel(QAbstractTableModel):
        """Simple table model backed by column arrays."""

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._columns: list[str] = []
            self._data: dict[str, np.ndarray] = {}
            self._n_rows: int = 0

        def set_data(self, columns: list[str], data_dict: dict[str, Any]) -> None:
            self.beginResetModel()
            self._columns = list(columns)
            self._data = {}
            self._n_rows = 0
            for col in columns:
                arr = data_dict.get(col)
                if arr is not None:
                    self._data[col] = np.asarray(arr)
                    self._n_rows = max(self._n_rows, len(self._data[col]))
            self.endResetModel()

        def rowCount(self, parent: QModelIndex | None = None) -> int:
            return self._n_rows

        def columnCount(self, parent: QModelIndex | None = None) -> int:
            return len(self._columns)

        def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
            if not index.isValid():
                return None
            if role == Qt.ItemDataRole.DisplayRole:
                row, col = index.row(), index.column()
                col_name = self._columns[col]
                arr = self._data.get(col_name)
                if arr is None or row >= len(arr):
                    return ""
                val = arr[row]
                return self._fmt(val)
            return None

        def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = Qt.ItemDataRole.DisplayRole,
        ) -> Any:
            if role == Qt.ItemDataRole.DisplayRole:
                if orientation == Qt.Orientation.Horizontal:
                    if section < len(self._columns):
                        return self._columns[section]
                else:
                    return str(section + 1)
            return None

        @staticmethod
        def _fmt(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, float) or (
                hasattr(val, "dtype") and np.issubdtype(type(val), np.floating)
            ):
                fval = float(val)
                if abs(fval) < 0.001 or abs(fval) >= 10000:
                    return f"{fval:.4e}"
                return f"{fval:.6g}"
            if hasattr(val, "shape") and val.shape:
                return f"array{val.shape}"
            return str(val)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._model = self._TableModel(self)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)

        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # BaseVisualization interface
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(run: Any) -> int:
        """Always 40 — universal fallback."""
        return 40

    def set_run(self, run: Any) -> None:
        self._run = run

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
            logger.debug("Table: could not open stream '{}': {}", stream_name, e)
            self._data_keys = {}

        self.set_field("(all)")

    def get_fields(self) -> list[str]:
        """Always returns ['(all)'] — table shows everything."""
        return ["(all)"]

    def set_field(self, field_name: str) -> None:
        self._field_name = field_name
        self._reload()

    def refresh(self) -> None:
        self._reload()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_events_table(self):
        from lightfall.utils.tiled_helpers import read_events
        return read_events(self._stream)

    def _reload(self) -> None:
        events = self._read_events_table()
        if events is None:
            return

        # events is a pandas DataFrame or similar mapping; get column names
        try:
            all_cols = list(events.keys())
        except Exception:
            return

        # Filter out ts_* columns
        columns = [c for c in all_cols if not c.startswith("ts_")]

        data_dict: dict[str, Any] = {}
        for col in columns:
            try:
                data_dict[col] = np.asarray(events[col])
            except Exception:
                pass

        self._model.set_data(columns, data_dict)
        self._table.scrollToBottom()

        logger.debug(
            "Table: loaded {} columns, {} rows",
            len(columns),
            self._model.rowCount(),
        )

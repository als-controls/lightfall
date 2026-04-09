"""Table visualization for Bluesky data.

Provides a tabular view of all data fields with real-time updates.
"""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QTableView,
    QWidget,
)

from lucid.plugins.visualization_plugin import VisualizationPlugin
from lucid.visualization.base import BaseVisualizationWidget
from lucid.visualization.spec import DataCharacteristics, VisualizationSpec
from lucid.visualization.theme import ThemedVisualizationMixin, VisualizationColors

if TYPE_CHECKING:
    from lucid.acquire.buffer import MultiStreamBuffer


class DataTableModel(QAbstractTableModel):
    """Qt model for tabular visualization data.

    Provides a model that displays sequence number, timestamp, and
    all data fields in columns.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the table model."""
        super().__init__(parent)
        self._columns: list[str] = ["seq_num", "time"]
        self._data: list[dict[str, Any]] = []

    def set_columns(self, columns: list[str]) -> None:
        """Set the data columns.

        Args:
            columns: List of column names.
        """
        self.beginResetModel()
        self._columns = ["seq_num", "time"] + columns
        self.endResetModel()

    def add_row(self, seq_num: int, timestamp: float, data: dict[str, Any]) -> None:
        """Add a new data row.

        Args:
            seq_num: Sequence number.
            timestamp: Event timestamp.
            data: Field values.
        """
        row_idx = len(self._data)
        self.beginInsertRows(QModelIndex(), row_idx, row_idx)

        row_data = {"seq_num": seq_num, "time": timestamp}
        row_data.update(data)
        self._data.append(row_data)

        self.endInsertRows()

    def clear(self) -> None:
        """Clear all data."""
        self.beginResetModel()
        self._data.clear()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of rows."""
        return len(self._data)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of columns."""
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Get data for a cell."""
        if not index.isValid():
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            row = index.row()
            col = index.column()

            if row < len(self._data) and col < len(self._columns):
                col_name = self._columns[col]
                value = self._data[row].get(col_name)
                return self._format_value(value)

        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        """Get header data."""
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                if section < len(self._columns):
                    return self._columns[section]
            else:
                return str(section + 1)
        return None

    def _format_value(self, value: Any) -> str:
        """Format a value for display.

        Args:
            value: The value to format.

        Returns:
            Formatted string.
        """
        if value is None:
            return ""
        elif isinstance(value, float):
            # Format floats with reasonable precision
            if abs(value) < 0.001 or abs(value) >= 10000:
                return f"{value:.4e}"
            return f"{value:.6g}"
        elif isinstance(value, (list, tuple)):
            # Array data - show shape
            if hasattr(value, "shape"):
                return f"array{value.shape}"
            return f"[{len(value)} items]"
        elif hasattr(value, "shape"):
            # Numpy array
            return f"array{value.shape}"
        else:
            return str(value)

    def get_all_data(self) -> list[dict[str, Any]]:
        """Get all data as list of dicts."""
        return self._data.copy()

    def get_columns(self) -> list[str]:
        """Get column names."""
        return self._columns.copy()


class TableVisualization(ThemedVisualizationMixin, BaseVisualizationWidget):
    """Table visualization widget for Bluesky data.

    Displays all data fields in a scrollable table with automatic
    column sizing and real-time updates.
    """

    def __init__(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the table visualization.

        Args:
            spec: Visualization specification.
            buffer: Data buffer.
            parent: Parent widget.
        """
        self._model: DataTableModel | None = None
        self._table: QTableView | None = None
        self._columns_set = False
        super().__init__(spec, buffer, parent)
        self._setup_theme()

    def _setup_ui(self) -> None:
        """Setup the table UI."""
        # Create model
        self._model = DataTableModel(self)

        # Create table view
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)

        # Configure header
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        # Enable sorting
        self._table.setSortingEnabled(False)  # Disable for live data

        self._layout.addWidget(self._table)

    # === Tiled bulk-load path ===

    def set_data(
        self,
        field_arrays: dict[str, np.ndarray],
        field_names: list[str],
    ) -> None:
        """Bulk-load tabular data from tiled ArrayClients."""
        if not self._model:
            return

        self._model.beginResetModel()
        self._model._columns = ["seq_num", "time"] + field_names
        self._model._data.clear()

        n_rows = max((len(arr) for arr in field_arrays.values()), default=0)
        time_arr = field_arrays.get("time")

        for i in range(n_rows):
            row = {"seq_num": i + 1, "time": float(time_arr[i]) if time_arr is not None else 0.0}
            for name in field_names:
                if name in field_arrays and i < len(field_arrays[name]):
                    row[name] = field_arrays[name][i]
            self._model._data.append(row)

        self._model.endResetModel()
        self._columns_set = True

        if self._table:
            self._table.scrollToBottom()

    def _on_new_point(self, seq_num: int, data: dict[str, Any]) -> None:
        """Handle new data point."""
        if not self._model:
            return

        # Set columns on first data
        if not self._columns_set:
            columns = list(data.keys())
            self._model.set_columns(columns)
            self._columns_set = True

        # Get timestamp from buffer
        stream = self.stream_buffer
        timestamp = 0.0
        if stream:
            timestamps = stream.get_timestamps()
            if timestamps:
                timestamp = timestamps[-1]

        # Add row
        self._model.add_row(seq_num, timestamp, data)

        # Auto-scroll to bottom
        if self._table:
            self._table.scrollToBottom()

    def _on_clear(self) -> None:
        """Handle clear request."""
        if self._model:
            self._model.clear()
        self._columns_set = False

    def _apply_viz_colors(self, colors: VisualizationColors) -> None:
        """Apply visualization colors."""
        if self._table:
            self._table.setStyleSheet(
                f"""
                QTableView {{
                    background-color: {colors.background};
                    color: {colors.foreground};
                    gridline-color: {colors.grid};
                    border: 1px solid {colors.border};
                }}
                QTableView::item:alternate {{
                    background-color: {colors.grid};
                }}
                QTableView::item:selected {{
                    background-color: {colors.highlight};
                }}
                QHeaderView::section {{
                    background-color: {colors.grid};
                    color: {colors.foreground};
                    border: 1px solid {colors.border};
                    padding: 4px;
                }}
                """
            )

    def _export_data(self, format: str) -> bytes:
        """Export table data."""
        if not self._model:
            return b""

        data = self._model.get_all_data()
        columns = self._model.get_columns()

        if format == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for row in data:
                # Convert non-string values
                clean_row = {}
                for col in columns:
                    val = row.get(col)
                    if hasattr(val, "tolist"):
                        val = val.tolist()
                    clean_row[col] = val
                writer.writerow(clean_row)
            return output.getvalue().encode("utf-8")

        elif format == "json":
            return json.dumps(data, indent=2, default=str).encode("utf-8")

        else:
            raise ValueError(f"Unsupported export format: {format}")

    def get_supported_export_formats(self) -> list[str]:
        """Get supported export formats."""
        return ["csv", "json"]


class TableVisualizationPlugin(VisualizationPlugin):
    """Plugin for table visualization."""

    @property
    def name(self) -> str:
        return "table"

    @property
    def display_name(self) -> str:
        return "Data Table"

    @property
    def icon(self) -> str:
        return "table"

    @property
    def description(self) -> str:
        return "Tabular view of all data fields with real-time updates"

    def can_handle(self, characteristics: DataCharacteristics) -> int:
        """Table can always handle data (baseline visualization).

        Returns a moderate score (40) as tables work for any data
        but aren't optimal for most visualization needs.
        """
        # Table is always a valid fallback
        return 40

    def create_widget(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> TableVisualization:
        """Create the table widget."""
        return TableVisualization(spec, buffer, parent)

    def get_default_spec(
        self, characteristics: DataCharacteristics
    ) -> VisualizationSpec:
        """Get default spec for table."""
        return VisualizationSpec.for_table(characteristics)

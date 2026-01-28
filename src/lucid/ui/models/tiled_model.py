"""Qt models for Tiled data browser.

Provides TiledRecord dataclass and Qt table model for displaying
records from a Tiled server in the browser panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


@dataclass
class TiledRecord:
    """Data class representing a single record from Tiled.

    Attributes:
        uid: Unique identifier for the run.
        scan_id: Scan ID number (may be None for non-bluesky data).
        plan_name: Name of the plan that was run.
        timestamp: When the run started.
        exit_status: How the run ended (success, fail, abort).
        num_points: Number of data points collected.
        duration: Duration of the run in seconds.
        sample_name: Name of the sample being measured.
        metadata: Full metadata dictionary from the run.
        _client_key: Key for accessing this record in the Tiled client.
    """

    uid: str
    scan_id: int | None
    plan_name: str
    timestamp: datetime
    exit_status: str
    num_points: int
    duration: float | None
    sample_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    _client_key: str = ""


class TiledRecordModel(QAbstractTableModel):
    """Qt table model for displaying TiledRecord objects.

    Provides a tabular view of Tiled records with columns for:
    Scan ID, Plan, Timestamp, Status, Points, Duration, Sample.

    Example:
        model = TiledRecordModel()
        model.set_records(records)
        table_view.setModel(model)
    """

    COLUMNS = ["Scan ID", "Plan", "Timestamp", "Status", "Points", "Duration", "Sample"]

    # Custom roles
    RecordRole = Qt.ItemDataRole.UserRole + 1
    UidRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the model.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._records: list[TiledRecord] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return number of rows."""
        if parent.isValid():
            return 0
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return number of columns."""
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Return data for the given index and role."""
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row < 0 or row >= len(self._records):
            return None

        record = self._records[row]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._get_display_data(record, col)
        elif role == Qt.ItemDataRole.ToolTipRole:
            return self._get_tooltip_data(record, col)
        elif role == Qt.ItemDataRole.ForegroundRole:
            return self._get_foreground_data(record, col)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            # Right-align numeric columns
            if col in (0, 4, 5):  # Scan ID, Points, Duration
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        elif role == self.RecordRole:
            return record
        elif role == self.UidRole:
            return record.uid

        return None

    def _get_display_data(self, record: TiledRecord, col: int) -> str:
        """Get display text for a column."""
        if col == 0:  # Scan ID
            return str(record.scan_id) if record.scan_id is not None else "-"
        elif col == 1:  # Plan
            return record.plan_name or "-"
        elif col == 2:  # Timestamp
            return record.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        elif col == 3:  # Status
            return record.exit_status or "-"
        elif col == 4:  # Points
            return str(record.num_points)
        elif col == 5:  # Duration
            if record.duration is not None:
                if record.duration < 1:
                    return f"{record.duration * 1000:.0f}ms"
                elif record.duration < 60:
                    return f"{record.duration:.1f}s"
                else:
                    minutes = int(record.duration // 60)
                    seconds = record.duration % 60
                    return f"{minutes}m {seconds:.0f}s"
            return "-"
        elif col == 6:  # Sample
            return record.sample_name or "-"
        return ""

    def _get_tooltip_data(self, record: TiledRecord, col: int) -> str | None:
        """Get tooltip text for a column."""
        if col == 0:
            return f"UID: {record.uid}"
        elif col == 2:
            return record.timestamp.isoformat()
        elif col == 5 and record.duration is not None:
            return f"{record.duration:.3f} seconds"
        return None

    def _get_foreground_data(self, record: TiledRecord, col: int) -> Any:
        """Get foreground color for status column."""
        if col == 3:  # Status column
            from PySide6.QtGui import QColor

            status = record.exit_status.lower() if record.exit_status else ""
            if status == "success":
                return QColor(0, 128, 0)  # Green
            elif status in ("fail", "error"):
                return QColor(192, 0, 0)  # Red
            elif status == "abort":
                return QColor(192, 128, 0)  # Orange
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Return header data."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def set_records(self, records: list[TiledRecord]) -> None:
        """Set the records to display.

        Args:
            records: List of TiledRecord objects.
        """
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()

    def append_records(self, records: list[TiledRecord]) -> None:
        """Append records to the model.

        Args:
            records: List of TiledRecord objects to append.
        """
        if not records:
            return
        start = len(self._records)
        end = start + len(records) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._records.extend(records)
        self.endInsertRows()

    def get_record(self, row: int) -> TiledRecord | None:
        """Get a record by row index.

        Args:
            row: Row index.

        Returns:
            TiledRecord at that row, or None if invalid.
        """
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def get_record_by_uid(self, uid: str) -> TiledRecord | None:
        """Get a record by UID.

        Args:
            uid: Record UID.

        Returns:
            TiledRecord with that UID, or None if not found.
        """
        for record in self._records:
            if record.uid == uid:
                return record
        return None

    def clear(self) -> None:
        """Clear all records from the model."""
        self.beginResetModel()
        self._records.clear()
        self.endResetModel()

    @property
    def records(self) -> list[TiledRecord]:
        """Get all records."""
        return list(self._records)


class TiledRecordFilterProxy(QSortFilterProxyModel):
    """Proxy model for filtering and sorting TiledRecord items.

    Provides local text filtering across multiple fields and
    filtering by exit status.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the proxy model.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._text_filter: str = ""
        self._status_filter: str | None = None  # None = all

        # Enable case-insensitive sorting
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_text_filter(self, text: str) -> None:
        """Set the text filter.

        Args:
            text: Text to filter by (searches plan, sample, uid).
        """
        self._text_filter = text.lower()
        self.invalidateFilter()

    def set_status_filter(self, status: str | None) -> None:
        """Set the status filter.

        Args:
            status: Exit status to filter by, or None for all.
        """
        self._status_filter = status
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """Determine if a row should be shown."""
        source_model = self.sourceModel()
        if source_model is None:
            return True

        record = source_model.get_record(source_row)
        if record is None:
            return True

        # Apply status filter
        if self._status_filter is not None:
            if record.exit_status.lower() != self._status_filter.lower():
                return False

        # Apply text filter
        if self._text_filter:
            searchable = " ".join([
                record.plan_name or "",
                record.sample_name or "",
                record.uid or "",
                str(record.scan_id) if record.scan_id is not None else "",
            ]).lower()
            if self._text_filter not in searchable:
                return False

        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Compare two items for sorting."""
        source_model = self.sourceModel()
        if source_model is None:
            return False

        left_record = source_model.get_record(left.row())
        right_record = source_model.get_record(right.row())

        if left_record is None or right_record is None:
            return False

        col = left.column()

        # Sort by appropriate field
        if col == 0:  # Scan ID
            left_val = left_record.scan_id or 0
            right_val = right_record.scan_id or 0
            return left_val < right_val
        elif col == 2:  # Timestamp
            return left_record.timestamp < right_record.timestamp
        elif col == 4:  # Points
            return left_record.num_points < right_record.num_points
        elif col == 5:  # Duration
            left_val = left_record.duration or 0
            right_val = right_record.duration or 0
            return left_val < right_val

        # Default string comparison for other columns
        left_data = source_model.data(left, Qt.ItemDataRole.DisplayRole)
        right_data = source_model.data(right, Qt.ItemDataRole.DisplayRole)
        return str(left_data or "") < str(right_data or "")

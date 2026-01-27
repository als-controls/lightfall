"""Thread model for Qt Model/View architecture.

Provides a table model for displaying ThreadManager threads with
real-time status updates and historical tracking.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    Signal,
    QTimer,
)
from PySide6.QtGui import QColor

from ncs.utils.logging import logger
from ncs.utils.threads import QThreadFuture, thread_manager

if TYPE_CHECKING:
    pass


class ThreadStatus(Enum):
    """Status of a tracked thread."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class ThreadRecord:
    """Record capturing thread state that survives after thread is GC'd.

    Attributes:
        thread_id: Python id() of the thread object.
        name: Thread name for display.
        key: Optional ThreadManager lookup key.
        status: Current thread status.
        started_at: When the thread started.
        finished_at: When the thread finished (None if still running).
        exception_msg: Exception message if thread errored.
        thread_ref: Weak reference to the thread (None if GC'd).
        method_name: Name of the method being executed.
        args_repr: String representation of method arguments.
    """

    thread_id: int
    name: str
    key: str | None
    status: ThreadStatus
    started_at: datetime
    finished_at: datetime | None = None
    exception_msg: str | None = None
    thread_ref: weakref.ref[QThreadFuture] | None = None
    method_name: str = ""
    args_repr: str = ""

    def get_thread(self) -> QThreadFuture | None:
        """Get the thread object if it still exists."""
        if self.thread_ref is None:
            return None
        return self.thread_ref()

    def get_duration(self) -> float:
        """Get duration in seconds (elapsed if running, final if finished)."""
        if self.finished_at is not None:
            return (self.finished_at - self.started_at).total_seconds()
        return (datetime.now() - self.started_at).total_seconds()

    def format_duration(self) -> str:
        """Format duration as human-readable string."""
        seconds = self.get_duration()
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}m {secs:.0f}s"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"


class ThreadManagerObserver(QObject):
    """Observer that monitors ThreadManager without modifying it.

    Polls ThreadManager._threads periodically to detect changes and
    emits Qt signals for thread lifecycle events.

    Signals:
        thread_added: Emitted when a new thread is detected.
        thread_updated: Emitted when a thread's status changes.
        thread_removed: Emitted when a thread is garbage collected.
    """

    thread_added = Signal(object)  # ThreadRecord
    thread_updated = Signal(object)  # ThreadRecord
    thread_removed = Signal(int)  # thread_id

    def __init__(
        self,
        poll_interval_ms: int = 100,
        parent: QObject | None = None,
    ) -> None:
        """Initialize the observer.

        Args:
            poll_interval_ms: How often to poll ThreadManager (milliseconds).
            parent: Qt parent object.
        """
        super().__init__(parent)
        self._known_threads: dict[int, ThreadRecord] = {}
        self._poll_interval = poll_interval_ms
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        """Start observing ThreadManager."""
        self._timer.start(self._poll_interval)
        # Do initial poll
        self._poll()

    def stop(self) -> None:
        """Stop observing ThreadManager."""
        self._timer.stop()

    def get_records(self) -> list[ThreadRecord]:
        """Get all known thread records."""
        return list(self._known_threads.values())

    def get_record(self, thread_id: int) -> ThreadRecord | None:
        """Get a specific thread record by ID."""
        return self._known_threads.get(thread_id)

    def clear_history(self) -> None:
        """Remove all non-running threads from tracking."""
        to_remove = [
            tid for tid, record in self._known_threads.items()
            if record.status not in (ThreadStatus.PENDING, ThreadStatus.RUNNING)
        ]
        for tid in to_remove:
            del self._known_threads[tid]
            self.thread_removed.emit(tid)

    def _poll(self) -> None:
        """Poll ThreadManager for changes."""
        current_ids = set()

        # Access ThreadManager's internal thread dict
        with thread_manager._registry_lock:
            threads_snapshot = dict(thread_manager._threads)

        for thread_id, ref in threads_snapshot.items():
            thread = ref()
            if thread is None:
                continue

            current_ids.add(thread_id)

            if thread_id not in self._known_threads:
                # New thread detected
                record = self._create_record(thread, thread_id)
                self._known_threads[thread_id] = record
                self.thread_added.emit(record)
                logger.trace("ThreadObserver: detected new thread {}", thread_id)
            else:
                # Update existing thread
                self._update_record(thread_id, thread)

        # Detect threads that have been garbage collected
        for thread_id in list(self._known_threads.keys()):
            if thread_id not in current_ids:
                record = self._known_threads[thread_id]
                # Thread was garbage collected - mark as completed if still running
                if record.status == ThreadStatus.RUNNING:
                    record.status = ThreadStatus.COMPLETED
                    record.finished_at = datetime.now()
                    record.thread_ref = None
                    self.thread_updated.emit(record)

    def _create_record(self, thread: QThreadFuture, thread_id: int) -> ThreadRecord:
        """Create a ThreadRecord for a new thread."""
        # Extract method info
        method_name = getattr(thread, "_name", "unknown")
        method = getattr(thread, "_method", None)
        if method is not None:
            method_name = getattr(method, "__name__", method_name)

        args = getattr(thread, "_args", ())
        kwargs = getattr(thread, "_kwargs", {})
        args_repr = ""
        if args or kwargs:
            args_parts = [repr(a)[:50] for a in args[:3]]
            kwargs_parts = [f"{k}={repr(v)[:30]}" for k, v in list(kwargs.items())[:3]]
            args_repr = ", ".join(args_parts + kwargs_parts)
            if len(args) > 3 or len(kwargs) > 3:
                args_repr += ", ..."

        # Determine initial status
        if thread.isRunning():
            status = ThreadStatus.RUNNING
        elif thread.isFinished():
            if thread.cancelled:
                status = ThreadStatus.CANCELLED
            elif thread.exception is not None:
                status = ThreadStatus.ERROR
            else:
                status = ThreadStatus.COMPLETED
        else:
            status = ThreadStatus.PENDING

        return ThreadRecord(
            thread_id=thread_id,
            name=getattr(thread, "_name", "unnamed"),
            key=getattr(thread, "_manager_key", None),
            status=status,
            started_at=datetime.now(),
            finished_at=datetime.now() if thread.isFinished() else None,
            exception_msg=str(thread.exception) if thread.exception else None,
            thread_ref=weakref.ref(thread),
            method_name=method_name,
            args_repr=args_repr,
        )

    def _update_record(self, thread_id: int, thread: QThreadFuture) -> None:
        """Update an existing thread record with current state."""
        record = self._known_threads[thread_id]
        old_status = record.status

        # Determine new status
        if thread.isRunning():
            new_status = ThreadStatus.RUNNING
        elif thread.isFinished():
            if thread.cancelled:
                new_status = ThreadStatus.CANCELLED
            elif thread.exception is not None:
                new_status = ThreadStatus.ERROR
            else:
                new_status = ThreadStatus.COMPLETED
        else:
            new_status = ThreadStatus.PENDING

        # Update record if status changed
        if new_status != old_status:
            record.status = new_status
            if new_status in (ThreadStatus.COMPLETED, ThreadStatus.CANCELLED, ThreadStatus.ERROR):
                record.finished_at = datetime.now()
            if thread.exception is not None:
                record.exception_msg = str(thread.exception)
            self.thread_updated.emit(record)


class ThreadTableModel(QAbstractTableModel):
    """Qt table model for displaying thread records.

    Columns:
        0: Name - thread name
        1: Key - optional ThreadManager key
        2: Status - running/completed/cancelled/error
        3: Started - start timestamp
        4: Duration - elapsed/final time
    """

    COLUMNS = ["Name", "Key", "Status", "Started", "Duration"]

    def __init__(
        self,
        observer: ThreadManagerObserver,
        parent: QObject | None = None,
    ) -> None:
        """Initialize the model.

        Args:
            observer: ThreadManagerObserver to get data from.
            parent: Qt parent object.
        """
        super().__init__(parent)
        self._observer = observer
        self._records: list[ThreadRecord] = []
        self._max_history = 100

        # Connect observer signals
        self._observer.thread_added.connect(self._on_thread_added)
        self._observer.thread_updated.connect(self._on_thread_updated)
        self._observer.thread_removed.connect(self._on_thread_removed)

        # Timer for updating duration column
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_durations)
        self._refresh_timer.start(500)  # Update durations every 500ms

    def set_max_history(self, max_count: int) -> None:
        """Set maximum number of historical records to keep."""
        self._max_history = max_count
        self._trim_history()

    def _trim_history(self) -> None:
        """Remove oldest completed records if over limit."""
        # Count completed records
        completed = [
            (i, r) for i, r in enumerate(self._records)
            if r.status not in (ThreadStatus.PENDING, ThreadStatus.RUNNING)
        ]

        while len(self._records) > self._max_history and completed:
            # Remove oldest completed
            idx, _ = completed.pop(0)
            self.beginRemoveRows(QModelIndex(), idx, idx)
            self._records.pop(idx)
            self.endRemoveRows()
            # Recalculate indices
            completed = [(i, r) for i, r in enumerate(self._records)
                         if r.status not in (ThreadStatus.PENDING, ThreadStatus.RUNNING)]

    def _on_thread_added(self, record: ThreadRecord) -> None:
        """Handle new thread added."""
        row = len(self._records)
        self.beginInsertRows(QModelIndex(), row, row)
        self._records.append(record)
        self.endInsertRows()
        self._trim_history()

    def _on_thread_updated(self, record: ThreadRecord) -> None:
        """Handle thread status update."""
        try:
            row = next(
                i for i, r in enumerate(self._records)
                if r.thread_id == record.thread_id
            )
            # Emit dataChanged for entire row
            top_left = self.index(row, 0)
            bottom_right = self.index(row, len(self.COLUMNS) - 1)
            self.dataChanged.emit(top_left, bottom_right)
        except StopIteration:
            pass  # Record not in our list

    def _on_thread_removed(self, thread_id: int) -> None:
        """Handle thread removed from observer."""
        try:
            row = next(
                i for i, r in enumerate(self._records)
                if r.thread_id == thread_id
            )
            self.beginRemoveRows(QModelIndex(), row, row)
            self._records.pop(row)
            self.endRemoveRows()
        except StopIteration:
            pass  # Already removed

    def _refresh_durations(self) -> None:
        """Refresh duration column for running threads."""
        for row, record in enumerate(self._records):
            if record.status == ThreadStatus.RUNNING:
                idx = self.index(row, 4)  # Duration column
                self.dataChanged.emit(idx, idx)

    def get_record(self, row: int) -> ThreadRecord | None:
        """Get the record at the specified row."""
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def get_record_by_id(self, thread_id: int) -> ThreadRecord | None:
        """Get a record by thread ID."""
        for record in self._records:
            if record.thread_id == thread_id:
                return record
        return None

    # === QAbstractTableModel implementation ===

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Get number of rows."""
        if parent.isValid():
            return 0
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Get number of columns."""
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Get data for index and role."""
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row < 0 or row >= len(self._records):
            return None

        record = self._records[row]

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:  # Name
                return record.name
            elif col == 1:  # Key
                return record.key or ""
            elif col == 2:  # Status
                return record.status.value.title()
            elif col == 3:  # Started
                return record.started_at.strftime("%H:%M:%S")
            elif col == 4:  # Duration
                return record.format_duration()

        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == 2:  # Status column color
                status_colors = {
                    ThreadStatus.RUNNING: QColor("#2196F3"),  # Blue
                    ThreadStatus.COMPLETED: QColor("#4CAF50"),  # Green
                    ThreadStatus.CANCELLED: QColor("#FF9800"),  # Orange
                    ThreadStatus.ERROR: QColor("#F44336"),  # Red
                    ThreadStatus.PENDING: QColor("#9E9E9E"),  # Gray
                }
                return status_colors.get(record.status)

        elif role == Qt.ItemDataRole.ToolTipRole:
            parts = [
                f"Name: {record.name}",
                f"Method: {record.method_name}",
            ]
            if record.args_repr:
                parts.append(f"Args: {record.args_repr}")
            if record.key:
                parts.append(f"Key: {record.key}")
            if record.exception_msg:
                parts.append(f"Error: {record.exception_msg}")
            return "\n".join(parts)

        elif role == Qt.ItemDataRole.UserRole:
            # Return the record itself
            return record

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Get header data."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None


class ThreadFilterProxyModel(QSortFilterProxyModel):
    """Filter proxy for thread table.

    Supports filtering by:
    - Text search (name or key)
    - Status filter (show only specific statuses)
    """

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the filter proxy."""
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._visible_statuses: set[ThreadStatus] | None = None

    def set_visible_statuses(self, statuses: set[ThreadStatus] | None) -> None:
        """Set which statuses should be visible.

        Args:
            statuses: Set of statuses to show, or None for all.
        """
        self._visible_statuses = statuses
        self.invalidateFilter()

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex
    ) -> bool:
        """Check if row should be shown."""
        source_model = self.sourceModel()
        if source_model is None:
            return True

        # Get the record
        record = source_model.get_record(source_row)
        if record is None:
            return False

        # Check status filter
        if self._visible_statuses is not None:
            if record.status not in self._visible_statuses:
                return False

        # Check text filter
        pattern = self.filterRegularExpression().pattern()
        if pattern:
            pattern_lower = pattern.lower()
            if pattern_lower in record.name.lower():
                return True
            if record.key and pattern_lower in record.key.lower():
                return True
            return False

        return True

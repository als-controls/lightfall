"""Thread model for Qt Model/View architecture.

Provides a table model for displaying ThreadManager threads with
real-time status updates, historical tracking, and CPU usage monitoring.
"""

from __future__ import annotations

import functools
import sys
import threading
import weakref
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import psutil
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor

from lightfall.utils.logging import logger
from lightfall.utils.threads import ManagedThreadPool, QThreadFuture, thread_manager

if TYPE_CHECKING:
    pass


@functools.cache
def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE  # default int would truncate on x64
    kernel32.GetThreadTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetThreadTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _win_thread_times(native_ids: set[int]) -> dict[int, tuple[float, float]]:
    """Per-thread (user, system) CPU seconds via kernel32.GetThreadTimes.

    ``psutil.Process.threads()`` on Windows walks the system-wide process
    list with the GIL held (60-130 ms with ~80 threads), which stalls the GUI
    even from a worker thread. Querying only the threads we can name is a
    handful of ctypes calls, each of which releases the GIL.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    thread_query_limited_information = 0x0800
    times: dict[int, tuple[float, float]] = {}
    creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
    for tid in native_ids:
        handle = kernel32.OpenThread(thread_query_limited_information, False, tid)
        if not handle:
            continue
        try:
            if kernel32.GetThreadTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_),
                ctypes.byref(kernel), ctypes.byref(user),
            ):
                def secs(ft: wintypes.FILETIME) -> float:
                    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7
                times[tid] = (secs(user), secs(kernel))
        finally:
            kernel32.CloseHandle(handle)
    return times


def _thread_cpu_times(process: psutil.Process, native_ids: set[int]) -> dict[int, tuple[float, float]]:
    """(user, system) CPU seconds for *native_ids* in this process."""
    if sys.platform == "win32":
        try:
            return _win_thread_times(native_ids)
        except (OSError, AttributeError):  # pragma: no cover - fall back to psutil
            pass
    return {t.id: (t.user_time, t.system_time) for t in process.threads() if t.id in native_ids}


# CPU tracking helper
class ThreadCpuTracker:
    """Tracks CPU usage for threads using psutil.

    Uses native thread IDs to match Python threads to OS threads,
    then calculates CPU percentage from time deltas.
    """

    def __init__(self) -> None:
        """Initialize the CPU tracker."""
        self._process = psutil.Process()
        # Map native_thread_id -> (last_user_time, last_system_time, last_sample_time)
        self._last_cpu_times: dict[int, tuple[float, float, float]] = {}
        # Map native_thread_id -> cpu_percent
        self._cpu_percent: dict[int, float] = {}
        # Cache of thread name -> native_id mapping
        self._name_to_native_id: dict[str, int] = {}

    def update(self) -> None:
        """Update CPU measurements for all threads."""
        now = datetime.now().timestamp()

        # Build name -> native_id mapping from Python threading
        self._name_to_native_id.clear()
        for thread in threading.enumerate():
            native_id = getattr(thread, "native_id", None)
            if native_id is not None:
                self._name_to_native_id[thread.name] = native_id

        # Get CPU times for those threads
        try:
            thread_times = _thread_cpu_times(self._process, set(self._name_to_native_id.values()))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return

        # Calculate CPU percent for each thread
        for native_id, (user_time, system_time) in thread_times.items():
            total_time = user_time + system_time

            if native_id in self._last_cpu_times:
                last_user, last_system, last_sample = self._last_cpu_times[native_id]
                last_total = last_user + last_system
                time_delta = now - last_sample

                if time_delta > 0:
                    cpu_delta = total_time - last_total
                    # CPU percent (can exceed 100% on multi-core)
                    cpu_percent = (cpu_delta / time_delta) * 100
                    self._cpu_percent[native_id] = max(0.0, min(cpu_percent, 100.0 * psutil.cpu_count()))
            else:
                self._cpu_percent[native_id] = 0.0

            self._last_cpu_times[native_id] = (user_time, system_time, now)

        # Clean up old entries
        current_ids = set(thread_times.keys())
        for native_id in list(self._last_cpu_times.keys()):
            if native_id not in current_ids:
                del self._last_cpu_times[native_id]
                self._cpu_percent.pop(native_id, None)

    def get_cpu_percent(self, thread_name: str) -> float | None:
        """Get CPU percent for a thread by name.

        Args:
            thread_name: The thread name to look up.

        Returns:
            CPU percentage (0-100+) or None if not available.
        """
        native_id = self._name_to_native_id.get(thread_name)
        if native_id is None:
            return None
        return self._cpu_percent.get(native_id)

    def get_native_id(self, thread_name: str) -> int | None:
        """Get the native thread ID for a thread name."""
        return self._name_to_native_id.get(thread_name)


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
        cpu_percent: Current CPU usage percentage (0-100+).
        native_thread_id: OS-level thread ID for CPU tracking.
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
    cpu_percent: float = 0.0
    native_thread_id: int | None = None

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

    def format_cpu(self) -> str:
        """Format CPU usage as a string."""
        if self.status != ThreadStatus.RUNNING:
            return "-"
        if self.cpu_percent < 0.1:
            return "<0.1%"
        return f"{self.cpu_percent:.1f}%"


@dataclass
class _ThreadState:
    """Plain-data view of one ThreadManager thread, captured on the GUI thread.

    Holds no QThreadFuture: only a weak reference, so this object can never
    keep a thread alive (or have its C++ destructor run) anywhere else.
    """

    ref: weakref.ref
    name: str
    status: ThreadStatus
    exception_msg: str | None
    key: str | None = None
    method_name: str = ""
    args_repr: str = ""


@dataclass
class _Snapshot:
    """CPU figures gathered on the sampler thread (plain data only)."""

    cpu_by_name: dict[str, float | None]
    native_by_name: dict[str, int | None]


def _describe_call(thread: QThreadFuture) -> tuple[str, str]:
    """(method name, short args repr) for a new thread's record."""
    method_name = getattr(thread, "_name", "unnamed")
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
    return method_name, args_repr


def _thread_status(thread: QThreadFuture) -> ThreadStatus:
    if thread.isRunning():
        return ThreadStatus.RUNNING
    if thread.isFinished():
        if thread.cancelled:
            return ThreadStatus.CANCELLED
        if thread.exception is not None:
            return ThreadStatus.ERROR
        return ThreadStatus.COMPLETED
    return ThreadStatus.PENDING


class ThreadManagerObserver(QObject):
    """Observer that monitors ThreadManager without modifying it.

    Polls ThreadManager._threads periodically to detect changes and
    emits Qt signals for thread lifecycle events. Also tracks CPU usage
    per thread using psutil.

    ``psutil.Process.threads()`` takes tens of milliseconds on Windows, so CPU
    times are gathered on a single background sampler thread. ThreadManager's
    QThreadFuture objects are read only on the GUI thread (cheap status calls)
    and reduced to plain data plus weak references; nothing QObject-valued
    crosses to the sampler, so it can never hold the last reference to a
    thread and run its destructor off the GUI thread. At most one sample
    is in flight: a tick that finds the previous sample still running is
    skipped. Callers own the polling lifetime (``start``/``stop``); the
    Threads panel runs it only while it is visible.

    Signals:
        thread_added: Emitted when a new thread is detected.
        thread_updated: Emitted when a thread's status changes.
        thread_removed: Emitted when a thread is garbage collected.
        cpu_updated: Emitted when CPU stats are refreshed.
    """

    thread_added = Signal(object)  # ThreadRecord
    thread_updated = Signal(object)  # ThreadRecord
    thread_removed = Signal(int)  # thread_id
    cpu_updated = Signal()  # Emitted when CPU stats refresh
    # Emitted from the sampler thread; queued onto the observer's (GUI) thread.
    _snapshot_ready = Signal(object)

    def __init__(
        self,
        poll_interval_ms: int = 1000,
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
        self._cpu_tracker = ThreadCpuTracker()
        self._pool: ManagedThreadPool | None = None
        self._sample_in_flight = False
        self._pending_states: dict[int, _ThreadState] | None = None
        self._snapshot_ready.connect(self._on_snapshot_ready, Qt.ConnectionType.QueuedConnection)

    @property
    def is_polling(self) -> bool:
        """Whether the periodic poll timer is running."""
        return self._timer.isActive()

    def start(self) -> None:
        """Start observing ThreadManager (first sample is requested at once)."""
        if not self._timer.isActive():
            self._timer.start(self._poll_interval)
        self._poll()

    def stop(self) -> None:
        """Stop observing ThreadManager. A sample already in flight still lands."""
        self._timer.stop()

    def shutdown(self) -> None:
        """Stop polling and release the sampler thread."""
        self.stop()
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def refresh_now(self) -> None:
        """Sample synchronously, for one-off reads while polling is stopped.

        Cheap on Windows (no psutil thread walk); skipped if a background
        sample is in flight, since that one is about to land anyway.
        """
        if self._sample_in_flight:
            return
        states = self._collect_states()
        self._apply_snapshot(states, self._gather_cpu({s.name for s in states.values()}))

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
        """Read thread states here (GUI), sample CPU on the worker (non-blocking)."""
        if self._sample_in_flight:
            return
        if self._pool is None:
            self._pool = ManagedThreadPool(max_workers=1, name="thread-monitor")
        states = self._collect_states()
        self._pending_states = states
        names = frozenset(s.name for s in states.values())
        self._sample_in_flight = True
        try:
            self._pool.submit(self._sample_and_post, names)
        except RuntimeError:  # pool shut down underneath us (app quitting)
            self._sample_in_flight = False
            self._pending_states = None

    def _collect_states(self) -> dict[int, _ThreadState]:
        """Plain-data thread states. GUI thread; the strong refs stay local."""
        with thread_manager._registry_lock:
            threads_snapshot = dict(thread_manager._threads)

        states: dict[int, _ThreadState] = {}
        for thread_id, thread in threads_snapshot.items():
            # thread_manager._threads stores QThreadFuture objects directly (strong refs)
            if thread is None:
                continue
            exc = thread.exception
            state = _ThreadState(
                ref=weakref.ref(thread),
                name=getattr(thread, "_name", "unnamed"),
                status=_thread_status(thread),
                exception_msg=str(exc) if exc is not None else None,
                key=getattr(thread, "_manager_key", None),
            )
            if thread_id not in self._known_threads:
                state.method_name, state.args_repr = _describe_call(thread)
            states[thread_id] = state
        return states

    def _sample_and_post(self, names: frozenset[str]) -> None:
        """Sampler-thread body: CPU times for *names*, queued back to the GUI."""
        try:
            snapshot = self._gather_cpu(names)
        except Exception as exc:  # never let a sampler error wedge polling
            logger.debug("Thread monitor sample failed: {}", exc)
            snapshot = None
        try:
            self._snapshot_ready.emit(snapshot)
        except RuntimeError:  # observer deleted while sampling
            pass

    def _gather_cpu(self, names: frozenset[str] | set[str]) -> _Snapshot:
        """Collect per-thread CPU figures. Runs off the GUI thread; no QObjects."""
        tracker = self._cpu_tracker
        tracker.update()
        return _Snapshot(
            cpu_by_name={n: tracker.get_cpu_percent(n) for n in names},
            native_by_name={n: tracker.get_native_id(n) for n in names},
        )

    def _on_snapshot_ready(self, snapshot: _Snapshot | None) -> None:
        self._sample_in_flight = False
        states, self._pending_states = self._pending_states, None
        if snapshot is not None and states is not None:
            self._apply_snapshot(states, snapshot)

    def _apply_snapshot(self, states: dict[int, _ThreadState], snapshot: _Snapshot) -> None:
        """Diff states + CPU against the known records. GUI thread; no psutil."""
        current_ids = set()

        for thread_id, state in states.items():
            current_ids.add(thread_id)

            if thread_id not in self._known_threads:
                record = self._create_record(state, thread_id, snapshot)
                self._known_threads[thread_id] = record
                self.thread_added.emit(record)
                logger.trace("ThreadObserver: detected new thread {}", thread_id)
            else:
                self._update_record(thread_id, state, snapshot)

        # Detect threads that have been garbage collected
        for thread_id in list(self._known_threads.keys()):
            if thread_id not in current_ids:
                record = self._known_threads[thread_id]
                # Thread was garbage collected - mark as completed if still running
                if record.status == ThreadStatus.RUNNING:
                    record.status = ThreadStatus.COMPLETED
                    record.finished_at = datetime.now()
                    record.cpu_percent = 0.0
                    record.thread_ref = None
                    self.thread_updated.emit(record)

        # Signal that CPU stats have been updated
        self.cpu_updated.emit()

    def _create_record(self, state: _ThreadState, thread_id: int, snapshot: _Snapshot) -> ThreadRecord:
        """Create a ThreadRecord for a new thread."""
        thread_name = state.name
        status = state.status

        # Get CPU info if running
        cpu_percent = 0.0
        native_id = None
        if status == ThreadStatus.RUNNING:
            cpu_percent = snapshot.cpu_by_name.get(thread_name) or 0.0
            native_id = snapshot.native_by_name.get(thread_name)

        finished = status in (ThreadStatus.COMPLETED, ThreadStatus.CANCELLED, ThreadStatus.ERROR)
        return ThreadRecord(
            thread_id=thread_id,
            name=thread_name,
            key=state.key,
            status=status,
            started_at=datetime.now(),
            finished_at=datetime.now() if finished else None,
            exception_msg=state.exception_msg,
            thread_ref=state.ref,
            method_name=state.method_name or thread_name,
            args_repr=state.args_repr,
            cpu_percent=cpu_percent,
            native_thread_id=native_id,
        )

    def _update_record(self, thread_id: int, state: _ThreadState, snapshot: _Snapshot) -> None:
        """Update an existing thread record with current state."""
        record = self._known_threads[thread_id]
        old_status = record.status
        new_status = state.status

        # Update CPU info for running threads
        if new_status == ThreadStatus.RUNNING:
            cpu = snapshot.cpu_by_name.get(record.name)
            if cpu is not None:
                record.cpu_percent = cpu
            # Update native ID if not set
            if record.native_thread_id is None:
                record.native_thread_id = snapshot.native_by_name.get(record.name)
        else:
            record.cpu_percent = 0.0

        # Update record if status changed
        if new_status != old_status:
            record.status = new_status
            if new_status in (ThreadStatus.COMPLETED, ThreadStatus.CANCELLED, ThreadStatus.ERROR):
                record.finished_at = datetime.now()
                record.cpu_percent = 0.0
            if state.exception_msg is not None:
                record.exception_msg = state.exception_msg
            self.thread_updated.emit(record)


class ThreadTableModel(QAbstractTableModel):
    """Qt table model for displaying thread records.

    Columns:
        0: Name - thread name
        1: Key - optional ThreadManager key
        2: Status - running/completed/cancelled/error
        3: CPU - CPU usage percentage
        4: Started - start timestamp
        5: Duration - elapsed/final time
    """

    COLUMNS = ["Name", "Key", "Status", "CPU", "Started", "Duration"]

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
        self._observer.cpu_updated.connect(self._on_cpu_updated)

        # Timer for updating duration column
        # Timer for updating duration column; driven by set_live() so it
        # only ticks while a view is showing the model.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(500)  # Update durations every 500ms
        self._refresh_timer.timeout.connect(self._refresh_durations)

    def set_live(self, live: bool) -> None:
        """Start or stop the duration-column refresh timer."""
        if live and not self._refresh_timer.isActive():
            self._refresh_timer.start()
        elif not live:
            self._refresh_timer.stop()

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

    def _on_cpu_updated(self) -> None:
        """Handle CPU stats update - refresh CPU column for running threads."""
        for row, record in enumerate(self._records):
            if record.status == ThreadStatus.RUNNING:
                idx = self.index(row, 3)  # CPU column
                self.dataChanged.emit(idx, idx)

    def _refresh_durations(self) -> None:
        """Refresh duration column for running threads."""
        for row, record in enumerate(self._records):
            if record.status == ThreadStatus.RUNNING:
                idx = self.index(row, 5)  # Duration column
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

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of rows."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._records)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
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
            elif col == 3:  # CPU
                return record.format_cpu()
            elif col == 4:  # Started
                return record.started_at.strftime("%H:%M:%S")
            elif col == 5:  # Duration
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
            elif col == 3:  # CPU column color based on usage
                if record.status == ThreadStatus.RUNNING:
                    if record.cpu_percent > 80:
                        return QColor("#F44336")  # Red - high CPU
                    elif record.cpu_percent > 50:
                        return QColor("#FF9800")  # Orange - medium CPU
                    elif record.cpu_percent > 10:
                        return QColor("#2196F3")  # Blue - normal
                return None

        elif role == Qt.ItemDataRole.ToolTipRole:
            parts = [
                f"Name: {record.name}",
                f"Method: {record.method_name}",
            ]
            if record.args_repr:
                parts.append(f"Args: {record.args_repr}")
            if record.key:
                parts.append(f"Key: {record.key}")
            if record.native_thread_id:
                parts.append(f"Native Thread ID: {record.native_thread_id}")
            if record.status == ThreadStatus.RUNNING:
                parts.append(f"CPU: {record.format_cpu()}")
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

"""Live data buffer for streaming Bluesky documents to Qt.

Provides thread-safe buffering of Bluesky document streams for
real-time Qt plot updates. The buffer handles the bridge between
the RunEngine's callback thread and the Qt main thread.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger
from PySide6.QtCore import QObject, Signal


@dataclass
class DataPoint:
    """A single data point from an event document.

    Attributes:
        timestamp: Event timestamp.
        seq_num: Sequence number in the run.
        data: Dictionary of field values.
        filled: Which fields have been filled.
    """

    timestamp: float
    seq_num: int
    data: dict[str, Any]
    filled: dict[str, bool] = field(default_factory=dict)


@dataclass
class RunInfo:
    """Information about a Bluesky run.

    Attributes:
        uid: Run UID.
        start_time: Run start time.
        plan_name: Name of the plan.
        motors: List of motor names.
        detectors: List of detector names.
        metadata: Start document metadata.
    """

    uid: str
    start_time: datetime
    plan_name: str = ""
    motors: list[str] = field(default_factory=list)
    detectors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_start_doc(cls, doc: dict[str, Any]) -> RunInfo:
        """Create from a Bluesky start document.

        Args:
            doc: Start document.

        Returns:
            RunInfo instance.
        """
        return cls(
            uid=doc.get("uid", ""),
            start_time=datetime.fromtimestamp(doc.get("time", 0)),
            plan_name=doc.get("plan_name", ""),
            motors=doc.get("motors", []),
            detectors=doc.get("detectors", []),
            metadata=doc,
        )


class LiveDataBuffer(QObject):
    """Thread-safe buffer for streaming Bluesky documents to Qt plots.

    LiveDataBuffer acts as a callback for RunEngine.subscribe() and
    emits Qt signals for UI updates. It maintains rolling buffers
    for each data field for efficient real-time plotting.

    Signals:
        data_updated(str, dict): Emitted when new data arrives (doc_name, doc).
        scan_started(dict): Emitted on 'start' document.
        scan_completed(dict): Emitted on 'stop' document.
        new_point(int, dict): Emitted for each event (seq_num, data).
        descriptor_received(str, dict): Emitted when descriptor arrives.

    Example:
        >>> from lightfall.acquire import get_run_engine
        >>> RE = get_run_engine()
        >>> buffer = LiveDataBuffer(max_points=1000)
        >>> RE.subscribe(buffer)
        >>> buffer.new_point.connect(plot.update_data)
    """

    # Signals for Qt thread-safe updates
    data_updated = Signal(str, dict)  # (doc_name, doc)
    scan_started = Signal(dict)  # start document
    scan_completed = Signal(dict)  # stop document
    new_point = Signal(int, dict)  # (seq_num, data dict)
    descriptor_received = Signal(str, dict)  # (stream_name, descriptor)

    def __init__(
        self,
        max_points: int = 10000,
        parent: QObject | None = None,
    ) -> None:
        """Initialize the live data buffer.

        Args:
            max_points: Maximum points to keep in each buffer.
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._max_points = max_points

        # Per-field rolling buffers
        self._buffers: dict[str, deque[Any]] = {}
        self._timestamps: deque[float] = deque(maxlen=max_points)
        self._seq_nums: deque[int] = deque(maxlen=max_points)

        # Current run state
        self._current_run: RunInfo | None = None
        self._descriptors: dict[str, dict[str, Any]] = {}  # stream_name -> descriptor
        self._data_keys: dict[str, dict[str, Any]] = {}  # field -> data_key info

    # === Callback Interface ===

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        """Handle a Bluesky document.

        This is called from the RunEngine's thread, so it must
        emit signals for thread-safe Qt updates.

        Args:
            name: Document type ('start', 'descriptor', 'event', 'stop').
            doc: Document dictionary.
        """
        if name == "start":
            self._handle_start(doc)
        elif name == "descriptor":
            self._handle_descriptor(doc)
        elif name == "event":
            self._handle_event(doc)
        elif name == "stop":
            self._handle_stop(doc)

        # Always emit the generic signal
        self.data_updated.emit(name, doc)

    def _handle_start(self, doc: dict[str, Any]) -> None:
        """Handle start document.

        Args:
            doc: Start document.
        """
        self.clear()
        self._current_run = RunInfo.from_start_doc(doc)
        self.scan_started.emit(doc)
        logger.info(f"Scan started: {self._current_run.uid[:8]}")

    def _handle_descriptor(self, doc: dict[str, Any]) -> None:
        """Handle descriptor document.

        Args:
            doc: Descriptor document.
        """
        stream_name = doc.get("name", "primary")
        self._descriptors[stream_name] = doc

        # Extract data keys for this stream
        data_keys = doc.get("data_keys", {})
        for key, info in data_keys.items():
            self._data_keys[key] = info
            # Create buffer for this field if not exists
            if key not in self._buffers:
                self._buffers[key] = deque(maxlen=self._max_points)

        self.descriptor_received.emit(stream_name, doc)
        logger.debug(f"Descriptor received: {stream_name} with {len(data_keys)} keys")

    def _handle_event(self, doc: dict[str, Any]) -> None:
        """Handle event document.

        Args:
            doc: Event document.
        """
        timestamp = doc.get("time", 0.0)
        seq_num = doc.get("seq_num", 0)
        data = doc.get("data", {})

        self._timestamps.append(timestamp)
        self._seq_nums.append(seq_num)

        # Buffer each field's data, reshaping flat arrays using descriptor shape
        reshaped = {}
        for field_name, value in data.items():
            if field_name not in self._buffers:
                self._buffers[field_name] = deque(maxlen=self._max_points)
            # Reshape flat arrays using shape from descriptor (like EPICS AreaDetectors).
            # Data from Tiled/pyarrow may arrive as Python lists, so convert first.
            shape = self._data_keys.get(field_name, {}).get("shape", [])
            if shape and len(shape) >= 2:
                if isinstance(value, list):
                    import numpy as np
                    value = np.asarray(value)
                if hasattr(value, "reshape") and value.ndim == 1:
                    value = value.reshape(shape)
            self._buffers[field_name].append(value)
            reshaped[field_name] = value

        # Emit signal for real-time updates
        self.new_point.emit(seq_num, reshaped)

    def _handle_stop(self, doc: dict[str, Any]) -> None:
        """Handle stop document.

        Args:
            doc: Stop document.
        """
        exit_status = doc.get("exit_status", "unknown")
        num_events = doc.get("num_events", {})
        self.scan_completed.emit(doc)
        logger.info(f"Scan completed: {exit_status}, events: {num_events}")

    # === Data Access ===

    @property
    def current_run(self) -> RunInfo | None:
        """Get info about the current run."""
        return self._current_run

    @property
    def is_running(self) -> bool:
        """Check if a run is in progress."""
        return self._current_run is not None

    @property
    def field_names(self) -> list[str]:
        """Get list of available field names."""
        return list(self._buffers.keys())

    def get_data(self, field: str) -> list[Any]:
        """Get all buffered data for a field.

        Args:
            field: Field name.

        Returns:
            List of values.
        """
        if field in self._buffers:
            return list(self._buffers[field])
        return []

    def get_timestamps(self) -> list[float]:
        """Get all buffered timestamps.

        Returns:
            List of timestamps.
        """
        return list(self._timestamps)

    def get_seq_nums(self) -> list[int]:
        """Get all buffered sequence numbers.

        Returns:
            List of sequence numbers.
        """
        return list(self._seq_nums)

    def get_field_info(self, field: str) -> dict[str, Any]:
        """Get data key info for a field.

        Args:
            field: Field name.

        Returns:
            Data key dictionary or empty dict.
        """
        return self._data_keys.get(field, {})

    def get_latest(self, field: str) -> Any | None:
        """Get the latest value for a field.

        Args:
            field: Field name.

        Returns:
            Latest value or None.
        """
        if field in self._buffers and self._buffers[field]:
            return self._buffers[field][-1]
        return None

    def get_point_count(self) -> int:
        """Get number of data points buffered.

        Returns:
            Number of points.
        """
        return len(self._timestamps)

    # === Buffer Management ===

    def clear(self) -> None:
        """Clear all buffered data."""
        self._buffers.clear()
        self._timestamps.clear()
        self._seq_nums.clear()
        self._descriptors.clear()
        self._data_keys.clear()
        self._current_run = None

    def set_max_points(self, max_points: int) -> None:
        """Set the maximum number of points to buffer.

        Args:
            max_points: New maximum.
        """
        self._max_points = max_points
        self._timestamps = deque(self._timestamps, maxlen=max_points)
        self._seq_nums = deque(self._seq_nums, maxlen=max_points)
        for key in self._buffers:
            self._buffers[key] = deque(self._buffers[key], maxlen=max_points)

    # === Array Conversion ===

    def get_array(self, field: str) -> Any:
        """Get buffered data as a numpy array.

        Args:
            field: Field name.

        Returns:
            Numpy array or None if numpy not available.
        """
        try:
            import numpy as np

            data = self.get_data(field)
            return np.array(data)
        except ImportError:
            logger.warning("numpy not available for array conversion")
            return None

    def get_xy_data(self, x_field: str, y_field: str) -> tuple[list[Any], list[Any]]:
        """Get paired X-Y data for plotting.

        Args:
            x_field: X-axis field name.
            y_field: Y-axis field name.

        Returns:
            Tuple of (x_data, y_data) lists.
        """
        x_data = self.get_data(x_field)
        y_data = self.get_data(y_field)
        # Ensure same length
        min_len = min(len(x_data), len(y_data))
        return x_data[:min_len], y_data[:min_len]

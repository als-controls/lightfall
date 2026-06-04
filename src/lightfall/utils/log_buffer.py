"""In-process ring buffer of recent log records.

Provides a thread-safe singleton that captures all log records (at the
configured level and above) into a bounded deque so that consumers — most
notably the embedded Claude agent — can look back at what happened in the
moments before something unexpected.

This complements ``ErrorCollector``: that collector is ERROR-only and
optimized for the bug-report dialog, whereas ``LogBuffer`` captures the
full tail (DEBUG/INFO/WARNING/ERROR) and is read by the in-app agent.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loguru import Record


_LEVEL_NO = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


@dataclass(frozen=True)
class LogRecord:
    """A captured log record.

    Attributes:
        timestamp: When the record was emitted (UTC).
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        level_no: Numeric level from loguru (DEBUG=10 ... CRITICAL=50).
        name: Logger name (typically the module path).
        function: Function where the record was emitted.
        line: Line number where the record was emitted.
        thread: Thread name.
        message: The formatted log message.
        exception_info: Formatted exception traceback, if any.
    """

    timestamp: datetime
    level: str
    level_no: int
    name: str
    function: str
    line: int
    thread: str
    message: str
    exception_info: str | None = None


class LogBuffer:
    """Singleton ring buffer of recent loguru records.

    Install once at application startup (after :func:`configure_logging`)
    via :meth:`install`. Subsequent calls are no-ops. Use
    :meth:`get_records` to retrieve filtered tails.
    """

    DEFAULT_MAX_RECORDS = 10_000
    DEFAULT_LEVEL = "DEBUG"

    _instance: LogBuffer | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> LogBuffer:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._records: deque[LogRecord] = deque(maxlen=self.DEFAULT_MAX_RECORDS)
        self._buffer_lock = threading.Lock()
        self._sink_id: int | None = None

    @classmethod
    def get_instance(cls) -> LogBuffer:
        """Return the LogBuffer singleton."""
        return cls()

    def install(
        self,
        level: str = DEFAULT_LEVEL,
        max_records: int = DEFAULT_MAX_RECORDS,
    ) -> None:
        """Install the loguru sink. Safe to call multiple times.

        Args:
            level: Minimum log level to capture (TRACE/DEBUG/INFO/...).
            max_records: Ring buffer capacity. The oldest record is
                evicted when full.
        """
        if self._sink_id is not None:
            return

        from loguru import logger

        if max_records != self._records.maxlen:
            with self._buffer_lock:
                self._records = deque(self._records, maxlen=max_records)

        self._sink_id = logger.add(
            self._sink,
            level=level,
            format="{message}",
        )

    def uninstall(self) -> None:
        """Remove the loguru sink. Primarily useful for testing."""
        if self._sink_id is None:
            return

        from loguru import logger

        logger.remove(self._sink_id)
        self._sink_id = None

    def clear(self) -> None:
        """Drop all captured records."""
        with self._buffer_lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._buffer_lock:
            return len(self._records)

    def get_records(
        self,
        *,
        level: str | None = None,
        since: datetime | None = None,
        since_seconds: float | None = None,
        contains: str | None = None,
        name_prefix: str | None = None,
        max_count: int | None = None,
    ) -> list[LogRecord]:
        """Return a filtered tail of recent records, newest first.

        Args:
            level: Minimum level filter (e.g. ``"WARNING"`` returns
                WARNING/ERROR/CRITICAL only). Case-insensitive.
            since: Return records with timestamp >= this datetime
                (UTC; naive datetimes are treated as UTC).
            since_seconds: Return records emitted within the last N
                seconds. Mutually exclusive with ``since``; if both are
                given, the more restrictive one wins.
            contains: Case-insensitive substring filter on the message.
            name_prefix: Return only records whose logger name starts
                with this prefix (e.g. ``"lightfall.devices"``).
            max_count: Cap on returned records. None = unbounded.
        """
        min_level_no = _LEVEL_NO.get(level.upper(), 0) if level else 0

        cutoffs: list[datetime] = []
        if since is not None:
            cutoffs.append(since if since.tzinfo else since.replace(tzinfo=UTC))
        if since_seconds is not None:
            cutoffs.append(datetime.now(UTC) - timedelta(seconds=float(since_seconds)))
        cutoff = max(cutoffs) if cutoffs else None

        contains_lc = contains.lower() if contains else None

        with self._buffer_lock:
            records = list(self._records)

        out: list[LogRecord] = []
        for rec in reversed(records):
            if rec.level_no < min_level_no:
                continue
            if cutoff is not None and rec.timestamp < cutoff:
                continue
            if name_prefix is not None and not rec.name.startswith(name_prefix):
                continue
            if contains_lc is not None and contains_lc not in rec.message.lower():
                continue
            out.append(rec)
            if max_count is not None and len(out) >= max_count:
                break
        return out

    def _sink(self, message) -> None:
        record: Record = message.record

        exception_info: str | None = None
        if record["exception"] is not None:
            exc = record["exception"]
            if exc.traceback:
                import traceback

                exception_info = "".join(
                    traceback.format_exception(exc.type, exc.value, exc.traceback)
                )

        thread = record.get("thread")
        thread_name = thread.name if thread is not None else ""

        log_record = LogRecord(
            timestamp=datetime.now(UTC),
            level=record["level"].name,
            level_no=record["level"].no,
            name=record["name"] or "",
            function=record["function"] or "",
            line=record["line"] or 0,
            thread=thread_name,
            message=record["message"],
            exception_info=exception_info,
        )

        with self._buffer_lock:
            self._records.append(log_record)


def get_log_buffer() -> LogBuffer:
    """Convenience accessor for the LogBuffer singleton."""
    return LogBuffer.get_instance()
